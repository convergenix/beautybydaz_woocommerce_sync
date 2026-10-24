from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime

from .amendment_core import (
    build_woo_update,
    canonical_json,
    change_metrics,
    confirmation_token,
    money,
    normalize_request,
    order_snapshot,
    payload_hash,
    quantity,
)
from .client import WooClient
from .core import decimal_value, resolve_payment_route
from .mapping import get_mapping
from .orders import (
    _create_delivery_note,
    _create_sales_invoice,
    _create_sales_order,
    _field,
    order_lock_key,
)

AMENDMENT_ROLE = "BBD Order Amendment Manager"


def _json_arg(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    parsed = frappe.parse_json(value)
    if not isinstance(parsed, dict):
        frappe.throw(_("Expected a JSON object"))
    return parsed


def _require_permission():
    roles = set(frappe.get_roles())
    if "System Manager" not in roles and AMENDMENT_ROLE not in roles:
        frappe.throw(_("You are not permitted to amend BeautybyDaz orders"), frappe.PermissionError)


def _settings(require_enabled: bool = False):
    settings = frappe.get_single("BBD Woo Sync Settings")
    if require_enabled and not cint(settings.order_amendments_enabled):
        frappe.throw(_("Controlled order amendments are disabled in BBD Woo Sync Settings"))
    return settings


def _validate_proof_file(file_url: str):
    if not file_url:
        return None
    file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    if not file_name:
        frappe.throw(_("Proof attachment does not exist in File Manager"))
    file_doc = frappe.get_doc("File", file_name)
    if not cint(file_doc.is_private):
        frappe.throw(_("Proof attachment must be private"))
    if file_doc.owner != frappe.session.user and "System Manager" not in frappe.get_roles():
        frappe.throw(_("Proof attachment must be uploaded by the requesting user"))
    return file_doc


def _channel(name: str):
    channel = frappe.get_doc("BBD Woo Channel", name)
    if not cint(channel.enabled):
        frappe.throw(_("Woo channel {0} is disabled").format(name))
    return channel


def _line_item_codes(channel, order: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in order.get("line_items") or []:
        mapping = get_mapping(channel.alias, int(line.get("product_id") or 0), int(line.get("variation_id") or 0))
        result[str(line.get("id") or "")] = mapping.erp_item
    return result


def _reverse_mappings(channel) -> dict[str, dict[str, int]]:
    rows = frappe.get_all(
        "BBD Woo Item Mapping",
        filters={"channel": channel.name, "enabled": 1},
        fields=["erp_item", "woo_product_id", "variation_id"],
        limit_page_length=0,
    )
    return {
        row.erp_item: {"product_id": int(row.woo_product_id), "variation_id": int(row.variation_id or 0)}
        for row in rows
    }


def _active_parent_rows(doctype: str, channel, order_id: int) -> list[dict[str, Any]]:
    return frappe.db.sql(
        f"""
        SELECT name, docstatus, status, modified
        FROM `tab{doctype}`
        WHERE custom_woo_site=%s
          AND docstatus=1
          AND (custom_woocommerce_order_root_id=%s OR custom_woocommerce_order_id=%s)
        ORDER BY modified DESC
        """,
        (channel.base_url.rstrip("/"), str(order_id), str(order_id)),
        as_dict=True,
    )


def _linked_documents(doctype: str, child_doctype: str, link_field: str, parent_name: str):
    return frappe.db.sql(
        f"""
        SELECT DISTINCT p.name, p.docstatus, p.status, p.modified
        FROM `tab{doctype}` p
        INNER JOIN `tab{child_doctype}` c ON c.parent=p.name AND c.parenttype=%s
        WHERE p.docstatus=1 AND IFNULL(p.is_return, 0)=0 AND c.{link_field}=%s
        ORDER BY p.modified DESC
        """,
        (doctype, parent_name),
        as_dict=True,
    )


def _payment_entries(invoice_name: str):
    return frappe.db.sql(
        """
        SELECT DISTINCT pe.name, pe.docstatus, pe.posting_date, pe.paid_amount,
               pe.received_amount, pe.unallocated_amount, pe.mode_of_payment,
               pe.paid_to, pe.reference_no, pe.reference_date, pe.modified
        FROM `tabPayment Entry` pe
        INNER JOIN `tabPayment Entry Reference` per ON per.parent=pe.name
        WHERE pe.docstatus=1
          AND per.reference_doctype='Sales Invoice'
          AND per.reference_name=%s
        ORDER BY pe.modified DESC
        """,
        (invoice_name,),
        as_dict=True,
    )


def _bank_reconciliation_rows(payment_entry: str) -> list[str]:
    if not payment_entry:
        return []
    rows = frappe.db.sql(
        """
        SELECT DISTINCT parent
        FROM `tabBank Transaction Payments`
        WHERE payment_document='Payment Entry' AND payment_entry=%s
        """,
        (payment_entry,),
    )
    return [row[0] for row in rows]


def _lock_payment_reconciliation_state(payment_entry: str):
    if not payment_entry:
        return
    payment = frappe.db.sql(
        """
        SELECT name, docstatus, clearance_date
        FROM `tabPayment Entry`
        WHERE name=%s
        FOR UPDATE
        """,
        (payment_entry,),
        as_dict=True,
    )
    if not payment:
        frappe.throw(_("Payment Entry {0} no longer exists").format(payment_entry))
    if payment[0].docstatus != 1:
        frappe.throw(_("Payment Entry {0} is no longer submitted; the ERP chain changed").format(payment_entry))
    reconciliations = frappe.db.sql(
        """
        SELECT DISTINCT parent
        FROM `tabBank Transaction Payments`
        WHERE payment_document='Payment Entry' AND payment_entry=%s
        FOR UPDATE
        """,
        (payment_entry,),
    )
    if payment[0].clearance_date:
        frappe.throw(
            _("Payment Entry {0} has clearance date {1}; automatic cancellation is prohibited").format(
                payment_entry, payment[0].clearance_date
            )
        )
    if reconciliations:
        frappe.throw(
            _("Payment Entry {0} is linked to Bank Transaction(s) {1}; automatic cancellation is prohibited").format(
                payment_entry, ", ".join(row[0] for row in reconciliations)
            )
        )


def _erp_chain(channel, order_id: int) -> dict[str, Any]:
    sales_orders = _active_parent_rows("Sales Order", channel, order_id)
    if len(sales_orders) != 1:
        frappe.throw(
            _("Expected exactly one submitted Sales Order for Woo order {0}; found {1}").format(order_id, len(sales_orders))
        )
    so = frappe.get_doc("Sales Order", sales_orders[0].name)
    delivery_notes = _linked_documents("Delivery Note", "Delivery Note Item", "against_sales_order", so.name)
    invoices = _linked_documents("Sales Invoice", "Sales Invoice Item", "sales_order", so.name)
    if len(delivery_notes) != 1 or len(invoices) != 1:
        frappe.throw(
            _("Woo order {0} requires one active Delivery Note and one active Sales Invoice; found DN={1}, SI={2}").format(
                order_id, len(delivery_notes), len(invoices)
            )
        )
    dn = frappe.get_doc("Delivery Note", delivery_notes[0].name)
    invoice = frappe.get_doc("Sales Invoice", invoices[0].name)
    payments = _payment_entries(invoice.name)
    if len(payments) > 1:
        frappe.throw(_("Sales Invoice {0} has multiple active Payment Entries and requires separate handling").format(invoice.name))
    draft_payments = frappe.db.sql(
        """
        SELECT DISTINCT pe.name
        FROM `tabPayment Entry` pe
        INNER JOIN `tabPayment Entry Reference` per ON per.parent=pe.name
        WHERE pe.docstatus=0
          AND per.reference_doctype='Sales Invoice'
          AND per.reference_name=%s
        """,
        (invoice.name,),
    )
    if draft_payments:
        frappe.throw(_("Sales Invoice {0} has a draft Payment Entry and requires separate handling").format(invoice.name))
    payment = frappe.get_doc("Payment Entry", payments[0].name) if payments else None
    if payment:
        references = [row for row in payment.references if row.allocated_amount]
        if len(references) != 1 or references[0].reference_doctype != "Sales Invoice" or references[0].reference_name != invoice.name:
            frappe.throw(_("Payment Entry {0} is shared across documents and cannot be amended automatically").format(payment.name))
        if payment.deductions or money(payment.unallocated_amount):
            frappe.throw(_("Payment Entry {0} has deductions or an unallocated balance and requires separate handling").format(payment.name))
        if payment.paid_from_account_currency != payment.paid_to_account_currency or payment.paid_to_account_currency != invoice.currency:
            frappe.throw(_("Payment Entry {0} is multi-currency and requires separate handling").format(payment.name))
    return {"sales_order": so, "delivery_note": dn, "sales_invoice": invoice, "payment_entry": payment}


def _document_summary(chain: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, doc in chain.items():
        if not doc:
            output[key] = None
            continue
        output[key] = {
            "doctype": doc.doctype,
            "name": doc.name,
            "docstatus": doc.docstatus,
            "status": getattr(doc, "status", None),
            "posting_date": str(getattr(doc, "posting_date", "") or ""),
            "transaction_date": str(getattr(doc, "transaction_date", "") or ""),
            "grand_total": float(getattr(doc, "grand_total", 0) or 0),
            "outstanding_amount": float(getattr(doc, "outstanding_amount", 0) or 0),
            "paid_amount": float(getattr(doc, "paid_amount", 0) or 0),
            "received_amount": float(getattr(doc, "received_amount", 0) or 0),
        }
    return output


def _document_review_state(doc) -> dict[str, Any] | None:
    if not doc:
        return None
    state = {
        "doctype": doc.doctype,
        "name": doc.name,
        "docstatus": int(doc.docstatus or 0),
        "modified": str(doc.modified or ""),
        "status": str(getattr(doc, "status", "") or ""),
        "woocommerce_order_root_id": str(doc.get("custom_woocommerce_order_root_id") or ""),
        "woocommerce_order_id": str(doc.get("custom_woocommerce_order_id") or ""),
        "amendment": str(doc.get("custom_bbd_order_amendment") or ""),
    }
    if doc.doctype == "Payment Entry":
        state["paid_amount"] = str(money(doc.paid_amount))
        state["received_amount"] = str(money(doc.received_amount))
        state["unallocated_amount"] = str(money(doc.unallocated_amount))
        state["clearance_date"] = str(doc.clearance_date or "")
        state["bank_reconciliations"] = sorted(_bank_reconciliation_rows(doc.name))
        state["references"] = sorted(
            [
                {
                    "doctype": str(row.reference_doctype or ""),
                    "name": str(row.reference_name or ""),
                    "allocated_amount": str(money(row.allocated_amount)),
                }
                for row in doc.references
                if money(row.allocated_amount)
            ],
            key=lambda row: (row["doctype"], row["name"]),
        )
    return state


def _chain_review_state(chain: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _document_review_state(chain.get(key))
        for key in ("sales_order", "delivery_note", "sales_invoice", "payment_entry")
    }


def _chain_fingerprint(chain: dict[str, Any]) -> str:
    return payload_hash(_chain_review_state(chain))


def _chain_evidence(chain: dict[str, Any]) -> dict[str, Any]:
    fresh_chain = {
        key: (frappe.get_doc(doc.doctype, doc.name) if doc else None)
        for key, doc in chain.items()
    }
    state = _chain_review_state(fresh_chain)
    return {
        "fingerprint": payload_hash(state),
        "state": state,
        "documents": _document_summary(fresh_chain),
    }


def _evidence_documents(evidence: dict[str, Any]) -> dict[str, Any]:
    documents = evidence.get("documents")
    return documents if isinstance(documents, dict) else evidence


def _assert_chain_matches_evidence(
    chain: dict[str, Any],
    evidence: dict[str, Any],
    amendment_name: str | None = None,
):
    if not evidence.get("fingerprint"):
        frappe.throw(
            _("Amendment evidence predates exact ERP-chain fingerprints; automatic recovery or rollback is not permitted")
        )
    if _chain_fingerprint(chain) != evidence["fingerprint"]:
        frappe.throw(_("The active ERP chain changed after amendment review; start a new review"))
    if amendment_name:
        for key, doc in chain.items():
            if doc and str(doc.get("custom_bbd_order_amendment") or "") != amendment_name:
                frappe.throw(_("{0} {1} is not owned by amendment {2}").format(doc.doctype, doc.name, amendment_name))


def _dimension_fieldnames() -> set[str]:
    fields = {"warehouse", "target_warehouse", "income_account", "expense_account", "cost_center", "project"}
    if frappe.db.exists("DocType", "Accounting Dimension"):
        fields.update(
            row.fieldname
            for row in frappe.get_all(
                "Accounting Dimension",
                filters={"disabled": 0},
                fields=["fieldname"],
                limit_page_length=0,
            )
            if row.fieldname
        )
    return fields


def _line_dimension_map(doc) -> dict[str, dict[str, Any]]:
    fields = _dimension_fieldnames()
    output: dict[str, dict[str, Any]] = {}
    for row in doc.items:
        line_id = str(getattr(row, "custom_woocommerce_line_item_id", "") or "")
        if not line_id or line_id in output:
            frappe.throw(_("{0} {1} has missing or duplicate Woo line IDs").format(doc.doctype, doc.name))
        output[line_id] = {
            fieldname: row.get(fieldname)
            for fieldname in fields
            if row.meta.has_field(fieldname)
        }
    return output


def _shipping_dimensions(sales_order, shipping_total: Decimal) -> dict[str, Any] | None:
    if not shipping_total:
        return None
    rows = [row for row in sales_order.taxes if money(row.tax_amount)]
    if len(rows) != 1:
        frappe.throw(_("Sales Order {0} must have exactly one preserved shipping charge").format(sales_order.name))
    row = rows[0]
    fields = _dimension_fieldnames() | {"account_head"}
    return {
        fieldname: row.get(fieldname)
        for fieldname in fields
        if row.meta.has_field(fieldname)
    }


def _settings_summary(settings) -> dict[str, Any]:
    return {
        "enabled": bool(cint(settings.order_amendments_enabled)),
        "warning_percent": float(settings.order_amendment_warning_percent or 5),
        "limit_percent": float(settings.order_amendment_limit_percent or 20),
        "max_value_change": float(settings.order_amendment_max_value_change or 500000),
        "max_age_days": int(settings.order_amendment_max_age_days or 30),
        "proof_required_above": float(settings.order_amendment_require_proof_above or 50000),
    }


def _stock_rows(channel, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in snapshot["items"]:
        totals[row["item_code"]] += quantity(row["quantity"])
    output = []
    for item_code, ordered_qty in sorted(totals.items()):
        bin_row = frappe.db.get_value(
            "Bin",
            {"item_code": item_code, "warehouse": channel.warehouse},
            ["actual_qty", "projected_qty", "reserved_qty"],
            as_dict=True,
        ) or {}
        output.append(
            {
                "item_code": item_code,
                "warehouse": channel.warehouse,
                "ordered_qty": float(ordered_qty),
                "actual_qty": float(bin_row.get("actual_qty") or 0),
                "projected_qty": float(bin_row.get("projected_qty") or 0),
                "reserved_qty": float(bin_row.get("reserved_qty") or 0),
            }
        )
    return output


def _fetch_context(channel_name: str, order_id: int) -> dict[str, Any]:
    channel = _channel(channel_name)
    client = WooClient(channel)
    order = client.get_order(order_id)
    if not order or int(order.get("id") or 0) != int(order_id):
        frappe.throw(_("Woo order {0} was not found").format(order_id))
    item_codes = _line_item_codes(channel, order)
    snapshot = order_snapshot(order, item_codes)
    chain = _erp_chain(channel, int(order_id))
    settings = _settings()
    return {
        "channel": channel,
        "client": client,
        "order": order,
        "snapshot": snapshot,
        "before_hash": payload_hash(snapshot),
        "chain": chain,
        "settings": settings,
        "stock": _stock_rows(channel, snapshot),
    }


@frappe.whitelist()
def get_order_amendment_channels():
    _require_permission()
    rows = frappe.get_all(
        "BBD Woo Channel",
        filters={"enabled": 1},
        fields=["name", "alias", "company", "base_url", "warehouse"],
        order_by="idx asc, name asc",
        limit_page_length=0,
    )
    return rows


def _default_pending_request(context: dict[str, Any]) -> dict[str, Any]:
    snapshot = context["snapshot"]
    return {
        "reason": "",
        "proof_attachment": "",
        "status": snapshot["status"],
        "payment_action": "preserve",
        "confirmed_payment_amount": str(_payment_amount(context["chain"])),
        "shipping_total": str(snapshot["shipping_total"]),
        "discount_total": str(snapshot["discount_total"]),
        "customer": snapshot["customer"],
        "items": [
            {
                "line_item_id": row["line_item_id"],
                "item_code": row["item_code"],
                "quantity": str(row["quantity"]),
                "unit_rate": str(row["unit_rate"]),
                "tax_class": row["tax_class"],
            }
            for row in snapshot["items"]
        ],
        "material_change_confirmed": False,
        "fulfilment_override_confirmed": False,
        "reconciliation_review_confirmed": False,
    }


def _pending_chain_matches(audit, evidence: dict[str, Any]) -> bool:
    try:
        stored = json.loads(audit.previous_erp_documents or "{}")
    except (TypeError, ValueError):
        return False
    return bool(stored.get("fingerprint")) and stored.get("fingerprint") == evidence.get("fingerprint")


def _create_or_reuse_pending_audit(context: dict[str, Any]):
    channel_name = context["channel"].name
    order_id = str(context["order"]["id"])
    evidence = _chain_evidence(context["chain"])
    existing_name = frappe.db.get_value(
        "BBD Order Amendment",
        {
            "channel": channel_name,
            "woo_order_id": order_id,
            "status": "Pending",
            "requested_by": frappe.session.user,
        },
        "name",
        order_by="modified desc",
    )
    if existing_name:
        existing = frappe.get_doc("BBD Order Amendment", existing_name)
        if existing.before_hash == context["before_hash"] and _pending_chain_matches(existing, evidence):
            return existing
        existing.status = "Failed"
        existing.error = _("Woo or ERP state changed before this pending amendment was completed; start again.")
        existing.save(ignore_permissions=True)

    revision = int(
        frappe.db.get_value(
            "BBD Order Amendment",
            {"channel": channel_name, "woo_order_id": order_id},
            "max(revision)",
        )
        or 0
    ) + 1
    request = _default_pending_request(context)
    total = money(context["snapshot"]["total"])
    audit = frappe.get_doc(
        {
            "doctype": "BBD Order Amendment",
            "status": "Pending",
            "channel": channel_name,
            "woo_order_id": order_id,
            "revision": revision,
            "requested_by": frappe.session.user,
            "requested_at": now_datetime(),
            "idempotency_key": payload_hash(
                {
                    "operation": "pending-draft",
                    "channel": channel_name,
                    "order_id": order_id,
                    "revision": revision,
                    "requested_by": frappe.session.user,
                    "nonce": frappe.generate_hash(length=16),
                }
            ),
            "currency": context["snapshot"]["currency"],
            "old_total": float(total),
            "new_total": float(total),
            "delta_total": 0,
            "payment_amount_preserved": float(_payment_amount(context["chain"])),
            "reason": "",
            "warnings_json": "[]",
            "before_hash": context["before_hash"],
            "before_payload": json.dumps(context["order"], indent=2, default=str),
            "requested_payload": json.dumps(request, indent=2, default=str),
            "previous_erp_documents": json.dumps(evidence, indent=2, default=str),
            "rollback_instructions": (
                "This pending record has not changed WooCommerce or ERPNext. Continue Amendment to review and apply it. "
                "Applied amendments retain separate rollback evidence and instructions."
            ),
            "items": _audit_item_rows(context["snapshot"], request),
        }
    )
    audit.insert(ignore_permissions=True)
    frappe.db.commit()
    return audit


@frappe.whitelist()
def start_sales_order_amendment(sales_order: str):
    _require_permission()
    _settings(require_enabled=True)
    sales_order_doc = frappe.get_doc("Sales Order", sales_order)
    sales_order_doc.check_permission("read")
    if sales_order_doc.docstatus != 1:
        frappe.throw(_("Only a submitted Woo-linked Sales Order can be amended"))
    channel_name = str(sales_order_doc.get("custom_woo_channel") or "").strip()
    order_id = str(
        sales_order_doc.get("custom_woocommerce_order_root_id")
        or sales_order_doc.get("custom_woocommerce_order_id")
        or ""
    ).strip()
    if not channel_name or not order_id.isdigit():
        frappe.throw(_("Sales Order {0} is not linked to a valid WooCommerce order").format(sales_order_doc.name))
    with frappe.cache.lock(order_lock_key(_channel(channel_name), int(order_id)), timeout=600):
        context = _fetch_context(channel_name, int(order_id))
        active_sales_order = context["chain"]["sales_order"]
        if active_sales_order.name != sales_order_doc.name:
            frappe.throw(
                _("Sales Order {0} is not the active document for Woo order {1}; open {2} instead").format(
                    sales_order_doc.name, order_id, active_sales_order.name
                )
            )
        if context["snapshot"]["status"] not in {"processing", "on-hold"}:
            frappe.throw(_("Only processing or on-hold Woo orders can use controlled amendment"))
        audit = _create_or_reuse_pending_audit(context)
    return {
        "amendment": audit.name,
        "channel": channel_name,
        "order_id": int(order_id),
        "sales_order": sales_order_doc.name,
        "woo_status": context["order"].get("status"),
        "total": context["order"].get("total"),
    }


@frappe.whitelist()
def get_pending_amendment(amendment: str):
    _require_permission()
    _settings(require_enabled=True)
    audit = frappe.get_doc("BBD Order Amendment", amendment)
    audit.check_permission("read")
    if audit.status != "Pending":
        frappe.throw(_("Amendment {0} is not pending").format(audit.name))
    if audit.requested_by != frappe.session.user and "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only the requesting user or a System Manager can continue this amendment"), frappe.PermissionError)
    return {
        "name": audit.name,
        "channel": audit.channel,
        "order_id": int(audit.woo_order_id),
        "before_hash": audit.before_hash,
        "request": json.loads(audit.requested_payload or "{}"),
    }


@frappe.whitelist()
def get_mapped_items(doctype, txt, searchfield, start, page_len, filters):
    _require_permission()
    filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
    channel = filters.get("channel")
    if not channel:
        return []
    return frappe.db.sql(
        """
        SELECT erp_item, sku
        FROM `tabBBD Woo Item Mapping`
        WHERE channel=%s
          AND enabled=1
          AND (erp_item LIKE %s OR sku LIKE %s)
        ORDER BY erp_item
        LIMIT %s, %s
        """,
        (channel, f"%{txt}%", f"%{txt}%", int(start), int(page_len)),
    )


@frappe.whitelist()
def fetch_order_for_amendment(channel: str, order_id: int | str):
    _require_permission()
    context = _fetch_context(channel, int(order_id))
    payment = context["chain"].get("payment_entry")
    return {
        "before_hash": context["before_hash"],
        "order": context["snapshot"],
        "erp_documents": _document_summary(context["chain"]),
        "payment_bank_reconciliations": _bank_reconciliation_rows(payment.name if payment else ""),
        "stock": context["stock"],
        "settings": _settings_summary(context["settings"]),
    }


def _changed(before: dict[str, Any], normalized: dict[str, Any], expected: dict[str, Any]) -> dict[str, bool]:
    before_items = [
        {
            "item_code": row["item_code"],
            "quantity": str(quantity(row["quantity"])),
            "unit_rate": str(money(row["unit_rate"])),
            "tax_class": row["tax_class"],
        }
        for row in before["items"]
    ]
    requested_items = [
        {
            "item_code": row["item_code"],
            "quantity": str(quantity(row["quantity"])),
            "unit_rate": str(money(row["unit_rate"])),
            "tax_class": row["tax_class"],
        }
        for row in normalized["items"]
        if quantity(row["quantity"]) > 0
    ]
    return {
        "items": canonical_json(before_items) != canonical_json(requested_items),
        "shipping": money(before["shipping_total"]) != money(normalized["shipping_total"]),
        "discount": money(before["discount_total"]) != money(normalized["discount_total"]),
        "customer": canonical_json(before["customer"]) != canonical_json(normalized["customer"]),
        "status": bool(normalized["status"]) and before.get("status") != normalized["status"],
        "payment": normalized["payment_action"] != "preserve",
        "value": money(before["total"]) != money(expected["expected_total_before_tax"]),
    }


def _freeze_dates(company: str) -> dict[str, str]:
    accounts = frappe.get_single("Accounts Settings")
    stock = frappe.get_single("Stock Settings")
    latest_period = frappe.db.get_value(
        "Period Closing Voucher",
        {"company": company, "docstatus": 1},
        "period_end_date",
        order_by="period_end_date desc",
    )
    return {
        "accounts_frozen_upto": str(accounts.acc_frozen_upto or ""),
        "stock_frozen_upto": str(stock.stock_frozen_upto or ""),
        "period_closed_upto": str(latest_period or ""),
    }


def _assert_open_dates(chain: dict[str, Any], company: str):
    freezes = _freeze_dates(company)
    dates = []
    for doc in chain.values():
        if not doc:
            continue
        date_value = getattr(doc, "posting_date", None) or getattr(doc, "transaction_date", None)
        if date_value:
            dates.append((f"{doc.doctype} {doc.name}", getdate(date_value)))
    for label, doc_date in dates:
        for freeze_label, freeze_value in freezes.items():
            if freeze_value and doc_date <= getdate(freeze_value):
                frappe.throw(_("{0} falls in a closed/frozen period ending {1} ({2})").format(label, freeze_value, freeze_label))


def _assert_stock(channel, chain: dict[str, Any], normalized: dict[str, Any]):
    old_qty: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    old_lines: dict[str, tuple[str, str]] = {}
    for row in chain["delivery_note"].items:
        line_id = str(getattr(row, "custom_woocommerce_line_item_id", "") or "")
        warehouse = str(row.warehouse or "")
        if not line_id or not warehouse or line_id in old_lines:
            frappe.throw(_("Delivery Note {0} has missing or duplicate Woo line/warehouse data").format(chain["delivery_note"].name))
        key = (row.item_code, warehouse)
        old_lines[line_id] = key
        old_qty[key] += quantity(row.qty)
    new_qty: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for row in normalized["items"]:
        if quantity(row["quantity"]) <= 0:
            continue
        key = old_lines.get(row["line_item_id"])
        if not key or key[0] != row["item_code"]:
            frappe.throw(_("Woo line {0} does not match the original Delivery Note item").format(row["line_item_id"]))
        assert key is not None
        new_qty[key] += quantity(row["quantity"])
    for item_code, warehouse in sorted(set(old_qty) | set(new_qty)):
        key = (item_code, warehouse)
        increase = new_qty[key] - old_qty[key]
        item = frappe.db.get_value(
            "Item",
            item_code,
            ["is_stock_item", "has_serial_no", "has_batch_no"],
            as_dict=True,
        ) or {}
        if item.get("has_serial_no") or item.get("has_batch_no") or frappe.db.exists("Product Bundle", item_code):
            frappe.throw(_("Item {0} uses serial, batch, or product-bundle handling and requires separate processing").format(item_code))
        if increase <= 0 or not cint(item.get("is_stock_item")):
            continue
        actual = decimal_value(
            frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty") or 0
        )
        if actual < increase:
            frappe.throw(
                _("Insufficient available stock for {0} in {1}: available {2}, amendment increase {3}").format(
                    item_code, warehouse, actual, increase
                )
            )


def _payment_amount(chain: dict[str, Any]) -> Decimal:
    payment = chain.get("payment_entry")
    invoice = chain.get("sales_invoice")
    if not payment or not invoice:
        return money(0)
    return sum(
        (money(row.allocated_amount) for row in payment.references if row.reference_doctype == "Sales Invoice" and row.reference_name == invoice.name),
        Decimal("0.00"),
    )


def _validate_preview(context: dict[str, Any], normalized: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    settings = context["settings"]
    if not cint(settings.order_amendments_enabled):
        frappe.throw(_("Controlled order amendments are disabled"))
    before = context["snapshot"]
    chain = context["chain"]
    if before["status"] not in {"processing", "on-hold"}:
        frappe.throw(_("Only processing or on-hold Woo orders can use controlled amendment"))
    if context["order"].get("coupon_lines") or context["order"].get("fee_lines") or context["order"].get("refunds"):
        frappe.throw(_("Orders with coupons, fees, or prior refunds require separate handling"))
    changed = _changed(before, normalized, expected)
    if not any(changed.values()):
        frappe.throw(_("The proposed amendment does not change the Woo order"))
    current_has_shipping = bool(context["order"].get("shipping_lines"))
    revised_has_shipping = money(normalized["shipping_total"]) > 0
    if current_has_shipping != revised_has_shipping:
        frappe.throw(_("Adding or removing the shipping line requires separate handling"))
    if any(row.get("tax_class") for row in normalized["items"]) or money(context["order"].get("total_tax")):
        frappe.throw(_("Tax-class amendments require a configured ERP tax mapping; none is configured for this integration"))
    metrics = change_metrics(before["total"], expected["expected_total_before_tax"])
    if metrics["percent"] > money(settings.order_amendment_limit_percent or 20):
        frappe.throw(
            _("Value change {0}% exceeds the configured maximum of {1}%").format(
                metrics["percent"], money(settings.order_amendment_limit_percent or 20)
            )
        )
    if abs(metrics["delta"]) > money(settings.order_amendment_max_value_change or 500000):
        frappe.throw(_("Absolute value change exceeds the configured maximum"))
    warnings: list[str] = []
    if metrics["percent"] >= money(settings.order_amendment_warning_percent or 5):
        warnings.append(f"Material order value change: {metrics['percent']}%")
        if not normalized["material_change_confirmed"]:
            frappe.throw(_("Confirm the material value-change warning before proceeding"))
    order_date = getdate((context["order"].get("date_created") or "")[:10])
    max_age_days = int(settings.order_amendment_max_age_days or 30)
    if order_date < add_days(getdate(now_datetime()), -max_age_days):
        frappe.throw(_("Order is older than the configured amendment window of {0} days").format(max_age_days))
    payment = chain.get("payment_entry")
    if payment and payment.clearance_date:
        frappe.throw(
            _("Payment Entry {0} has clearance date {1}; clearance/reversal review is required outside this tool").format(
                payment.name, payment.clearance_date
            )
        )
    reconciliations = _bank_reconciliation_rows(payment.name if payment else "")
    if reconciliations:
        frappe.throw(
            _("Payment Entry {0} is linked to Bank Transaction(s) {1}; reconcile/reversal review is required outside this tool").format(
                payment.name, ", ".join(reconciliations)
            )
        )
    rebuild_required = any(changed[key] for key in ("items", "shipping", "discount", "customer", "payment", "value"))
    if rebuild_required and not normalized["fulfilment_override_confirmed"]:
        frappe.throw(_("The linked Delivery Note is submitted; confirm the fulfilment override to rebuild the document chain"))
    if payment and rebuild_required and not normalized["reconciliation_review_confirmed"]:
        frappe.throw(_("Confirm payment reconciliation review before replacing the submitted Payment Entry"))
    _assert_open_dates(chain, context["channel"].company)
    if rebuild_required:
        _assert_stock(context["channel"], chain, normalized)
    current_payment = _payment_amount(chain)
    confirmed_payment = money(normalized["confirmed_payment_amount"])
    if confirmed_payment != current_payment:
        frappe.throw(
            _("Confirmed payment amount {0} does not match the ERP allocation {1}").format(
                confirmed_payment, current_payment
            )
        )
    desired_payment = current_payment
    if payment and desired_payment != metrics["new_total"]:
        frappe.throw(
            _("Orders with a submitted Payment Entry can only be amended when its allocation exactly matches the revised total")
        )
    if not payment and desired_payment < metrics["new_total"] and normalized["payment_action"] != "put_on_hold":
        frappe.throw(_("The unpaid revised order must be placed On Hold for payment review"))
    proof_threshold = money(settings.order_amendment_require_proof_above or 50000)
    proof_file = _validate_proof_file(normalized["proof_attachment"])
    if abs(metrics["delta"]) >= proof_threshold and not proof_file:
        frappe.throw(_("Proof attachment is required for this amendment"))
    if desired_payment != metrics["new_total"]:
        warnings.append(
            f"Payment {desired_payment} will be preserved against revised total {metrics['new_total']}; the difference remains outstanding or unallocated."
        )
    return {
        "changed": changed,
        "rebuild_required": rebuild_required,
        "metrics": {key: str(value) for key, value in metrics.items()},
        "warnings": warnings,
        "desired_payment": str(desired_payment),
        "previous_documents": _chain_evidence(chain),
        "chain_fingerprint": _chain_fingerprint(chain),
        "freeze_dates": _freeze_dates(context["channel"].company),
    }


def _prepare(channel_name: str, order_id: int, before_hash: str, amendment: dict[str, Any]):
    context = _fetch_context(channel_name, order_id)
    if context["before_hash"] != before_hash:
        frappe.throw(_("Woo order changed after it was loaded. Fetch it again before applying an amendment."))
    normalized = normalize_request(amendment)
    woo_payload, expected = build_woo_update(context["order"], normalized, _reverse_mappings(context["channel"]))
    validation = _validate_preview(context, normalized, expected)
    chain_fingerprint = validation["chain_fingerprint"]
    approval_hash = payload_hash({"before_hash": before_hash, "chain_fingerprint": chain_fingerprint})
    token = confirmation_token(approval_hash, normalized, frappe.session.user)
    return context, normalized, woo_payload, expected, validation, token


@frappe.whitelist()
def preview_order_amendment(channel: str, order_id: int | str, before_hash: str, amendment: str | dict[str, Any]):
    _require_permission()
    context, _normalized, _woo_payload, expected, validation, token = _prepare(
        channel, int(order_id), before_hash, _json_arg(amendment)
    )
    return {
        "confirmation_token": token,
        "chain_fingerprint": validation["chain_fingerprint"],
        "summary": validation,
        "expected": expected,
        "before": context["snapshot"],
        "confirmation_message": (
            f"Woo order {order_id}: {validation['metrics']['old_total']} → {validation['metrics']['new_total']} "
            f"({validation['metrics']['delta']} {context['snapshot']['currency']}). "
            f"ERP rebuild: {'Yes' if validation['rebuild_required'] else 'No'}."
        ),
    }


def _audit_item_rows(before: dict[str, Any], normalized: dict[str, Any]):
    old_by_id = {row["line_item_id"]: row for row in before["items"]}
    rows = []
    for new in normalized["items"]:
        old = old_by_id.get(new["line_item_id"])
        if not old:
            action = "Added"
        elif quantity(new["quantity"]) == 0:
            action = "Removed"
        elif old["item_code"] != new["item_code"]:
            action = "Replaced"
        elif quantity(old["quantity"]) != quantity(new["quantity"]) or money(old["unit_rate"]) != money(new["unit_rate"]) or old["tax_class"] != new["tax_class"]:
            action = "Updated"
        else:
            action = "Unchanged"
        rows.append(
            {
                "line_item_id": new["line_item_id"],
                "action": action,
                "old_item_code": old["item_code"] if old else None,
                "new_item_code": new["item_code"] or None,
                "old_quantity": float(quantity(old["quantity"])) if old else 0,
                "new_quantity": float(quantity(new["quantity"])),
                "old_rate": float(money(old["unit_rate"])) if old else 0,
                "new_rate": float(money(new["unit_rate"])),
                "old_tax_class": old["tax_class"] if old else "",
                "new_tax_class": new["tax_class"],
            }
        )
    requested_ids = {row["line_item_id"] for row in normalized["items"] if row["line_item_id"]}
    for line_id, old in old_by_id.items():
        if line_id in requested_ids:
            continue
        rows.append(
            {
                "line_item_id": line_id,
                "action": "Removed",
                "old_item_code": old["item_code"],
                "new_item_code": None,
                "old_quantity": float(quantity(old["quantity"])),
                "new_quantity": 0,
                "old_rate": float(money(old["unit_rate"])),
                "new_rate": 0,
                "old_tax_class": old["tax_class"],
                "new_tax_class": "",
            }
        )
    return rows


def _create_audit(
    context,
    normalized,
    validation,
    idempotency_key: str,
    pending_amendment: str = "",
):
    existing = frappe.db.get_value(
        "BBD Order Amendment",
        {"idempotency_key": idempotency_key},
        ["name", "status"],
        as_dict=True,
    )
    if existing:
        audit = frappe.get_doc("BBD Order Amendment", existing.name)
        if existing.status == "Applied":
            return audit, True
        if existing.status == "Pending":
            return audit, False
        if existing.status == "Rolled Back" and "Manual rollback completed" in (audit.rollback_instructions or ""):
            frappe.throw(
                _("Amendment {0} was manually rolled back; start a new amendment with a new approval reason").format(
                    audit.name
                )
            )
        if existing.status in {"Failed", "Rolled Back"}:
            audit.status = "Pending"
            audit.error = None
            audit.requested_at = now_datetime()
            audit.save(ignore_permissions=True)
            frappe.db.commit()
            return audit, False
        frappe.throw(_("Amendment {0} is in recovery state {1}; resolve it before retrying").format(existing.name, existing.status))

    metrics = validation["metrics"]
    values = {
        "status": "Pending",
        "channel": context["channel"].name,
        "woo_order_id": str(context["order"]["id"]),
        "requested_by": frappe.session.user,
        "requested_at": now_datetime(),
        "idempotency_key": idempotency_key,
        "currency": context["snapshot"]["currency"],
        "old_total": float(money(metrics["old_total"])),
        "new_total": float(money(metrics["new_total"])),
        "delta_total": float(money(metrics["delta"])),
        "payment_amount_preserved": float(money(validation["desired_payment"])),
        "reason": normalized["reason"],
        "proof_attachment": normalized["proof_attachment"],
        "warnings_json": json.dumps(validation["warnings"], indent=2),
        "before_hash": context["before_hash"],
        "before_payload": json.dumps(context["order"], indent=2, default=str),
        "requested_payload": json.dumps(normalized, indent=2, default=str),
        "previous_erp_documents": json.dumps(validation["previous_documents"], indent=2, default=str),
        "rollback_instructions": (
            "Automatic rollback restores the Woo order from the stored before payload when ERP rebuilding fails. "
            "For manual rollback, review this record, reverse the replacement ERP documents in Payment Entry → "
            "Sales Invoice → Delivery Note → Sales Order order, then re-apply the stored Woo before payload."
        ),
    }
    item_rows = _audit_item_rows(context["snapshot"], normalized)
    if pending_amendment:
        audit = frappe.get_doc("BBD Order Amendment", pending_amendment)
        audit.check_permission("read")
        if audit.status != "Pending":
            frappe.throw(_("Amendment {0} is not pending").format(audit.name))
        if audit.channel != values["channel"] or str(audit.woo_order_id) != values["woo_order_id"]:
            frappe.throw(_("Pending amendment {0} does not belong to this Woo order").format(audit.name))
        if audit.requested_by != frappe.session.user and "System Manager" not in frappe.get_roles():
            frappe.throw(_("Amendment {0} belongs to another requester").format(audit.name), frappe.PermissionError)
        if audit.before_hash != context["before_hash"] or not _pending_chain_matches(audit, validation["previous_documents"]):
            frappe.throw(_("The pending amendment is stale. Start again from the active Sales Order."))
        audit.update(values)
        audit.set("items", [])
        for row in item_rows:
            audit.append("items", row)
        audit.save(ignore_permissions=True)
    else:
        revision = int(
            frappe.db.get_value(
                "BBD Order Amendment",
                {"channel": context["channel"].name, "woo_order_id": str(context["order"]["id"])},
                "max(revision)",
            )
            or 0
        ) + 1
        audit = frappe.get_doc(
            {
                "doctype": "BBD Order Amendment",
                "revision": revision,
                **values,
                "items": item_rows,
            }
        )
        audit.insert(ignore_permissions=True)
    proof_file = _validate_proof_file(normalized["proof_attachment"])
    if proof_file:
        proof_file.attached_to_doctype = "BBD Order Amendment"
        proof_file.attached_to_name = audit.name
        proof_file.attached_to_field = "proof_attachment"
        proof_file.save(ignore_permissions=True)
    frappe.db.commit()
    return audit, False


def _restore_payload(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_lines = before.get("line_items") or []
    after_lines = after.get("line_items") or []
    before_ids = {int(row.get("id") or 0) for row in before_lines}
    payload_lines = []
    for row in after_lines:
        line_id = int(row.get("id") or 0)
        if line_id and line_id not in before_ids:
            payload_lines.append({"id": line_id, "quantity": 0})
    after_ids = {int(row.get("id") or 0) for row in after_lines}
    for row in before_lines:
        item = {
            "quantity": float(quantity(row.get("quantity") or 0)),
            "subtotal": f"{money(row.get('subtotal')):.2f}",
            "total": f"{money(row.get('total')):.2f}",
            "tax_class": str(row.get("tax_class") or ""),
        }
        if int(row.get("id") or 0) in after_ids:
            item["id"] = int(row["id"])
        else:
            item["product_id"] = int(row.get("product_id") or 0)
            if int(row.get("variation_id") or 0):
                item["variation_id"] = int(row["variation_id"])
        payload_lines.append(item)
    payload: dict[str, Any] = {
        "line_items": payload_lines,
        "billing": {key: (before.get("billing") or {}).get(key) or "" for key in ("first_name", "last_name", "company", "email", "phone")},
        "status": before.get("status"),
    }
    before_shipping = before.get("shipping_lines") or []
    after_shipping = after.get("shipping_lines") or []
    if before_shipping and after_shipping:
        payload["shipping_lines"] = [{"id": int(after_shipping[0]["id"]), "total": f"{money(before_shipping[0].get('total')):.2f}"}]
    return payload


def _cancel_chain(chain: dict[str, Any]):
    for key in ("payment_entry", "sales_invoice", "delivery_note", "sales_order"):
        doc = chain.get(key)
        if not doc:
            continue
        doc = frappe.get_doc(doc.doctype, doc.name)
        chain[key] = doc
        if doc.docstatus == 1:
            if key == "payment_entry":
                _lock_payment_reconciliation_state(doc.name)
                doc = frappe.get_doc(doc.doctype, doc.name)
                chain[key] = doc
            doc.flags.ignore_permissions = True
            doc.cancel()


def _new_payment(channel, order, old_payment, invoice, desired_payment: Decimal, amendment_name: str):
    if desired_payment <= 0:
        return None
    allocated = min(desired_payment, money(invoice.outstanding_amount))
    if old_payment:
        payment = frappe.copy_doc(old_payment)
        payment.name = None
        payment.docstatus = 0
        payment.amended_from = old_payment.name
        payment.set("references", [])
        payment.paid_amount = float(desired_payment)
        payment.received_amount = float(desired_payment)
        payment.append(
            "references",
            {
                "reference_doctype": "Sales Invoice",
                "reference_name": invoice.name,
                "total_amount": invoice.grand_total,
                "outstanding_amount": invoice.outstanding_amount,
                "allocated_amount": float(allocated),
            },
        )
    else:
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

        route = resolve_payment_route(order)
        payment = get_payment_entry("Sales Invoice", invoice.name, bank_account=route["clearing_account"])
        payment.mode_of_payment = route["mode_of_payment"]
        payment.paid_to = route["clearing_account"]
        payment.reference_no = f"Woo {channel.alias} order {order['id']} amendment"
        payment.reference_date = getdate((order.get("date_paid") or order.get("date_created"))[:10])
        payment.paid_amount = float(desired_payment)
        payment.received_amount = float(desired_payment)
        for reference in payment.references:
            if reference.reference_doctype == "Sales Invoice" and reference.reference_name == invoice.name:
                reference.allocated_amount = float(allocated)
    _field(payment, "custom_woocommerce_order_root_id", str(order["id"]))
    _field(payment, "custom_bbd_order_amendment", amendment_name)
    _field(payment, "custom_woo_channel", channel.alias)
    payment.remarks = (payment.remarks or "") + f"\nControlled Woo order amendment {amendment_name}."
    payment.insert(ignore_permissions=True)
    payment.submit()
    return payment


def _rebuild_erp(
    context,
    revised_order,
    audit,
    desired_payment: Decimal,
    external_order_id: str | None = None,
):
    chain = context["chain"]
    old_payment = chain.get("payment_entry")
    so_dimensions = _line_dimension_map(chain["sales_order"])
    dn_dimensions = _line_dimension_map(chain["delivery_note"])
    invoice_dimensions = _line_dimension_map(chain["sales_invoice"])
    shipping_dimensions = _shipping_dimensions(
        chain["sales_order"],
        money(revised_order.get("shipping_total")) + money(revised_order.get("shipping_tax")),
    )
    original_dates = {
        "so_transaction": chain["sales_order"].transaction_date,
        "so_delivery": chain["sales_order"].delivery_date,
        "dn_date": chain["delivery_note"].posting_date,
        "dn_time": chain["delivery_note"].posting_time,
        "invoice_date": chain["sales_invoice"].posting_date,
        "invoice_time": chain["sales_invoice"].posting_time,
    }
    _cancel_chain(chain)
    revision_id = external_order_id or f"{revised_order['id']}-A{audit.revision}"
    customer = frappe.get_doc("Customer", chain["sales_order"].customer)
    so = _create_sales_order(
        context["channel"],
        revised_order,
        customer,
        external_order_id=revision_id,
        root_order_id=str(revised_order["id"]),
        amendment=audit.name,
        amended_from=chain["sales_order"].name,
        document_date=original_dates["so_transaction"],
        delivery_date=original_dates["so_delivery"],
        line_dimensions=so_dimensions,
        shipping_dimensions=shipping_dimensions,
    )
    dn = _create_delivery_note(
        context["channel"],
        revised_order,
        so,
        external_order_id=revision_id,
        root_order_id=str(revised_order["id"]),
        amendment=audit.name,
        amended_from=chain["delivery_note"].name,
        document_date=original_dates["dn_date"],
        document_time=original_dates["dn_time"],
        line_dimensions=dn_dimensions,
        allow_negative_stock=False,
    )
    invoice = _create_sales_invoice(
        context["channel"],
        revised_order,
        so,
        external_order_id=revision_id,
        root_order_id=str(revised_order["id"]),
        amendment=audit.name,
        amended_from=chain["sales_invoice"].name,
        document_date=original_dates["invoice_date"],
        document_time=original_dates["invoice_time"],
        line_dimensions=invoice_dimensions,
    )
    payment = _new_payment(context["channel"], revised_order, old_payment, invoice, desired_payment, audit.name)
    invoice.reload()
    if getdate(so.transaction_date) != getdate(original_dates["so_transaction"]):
        frappe.throw(_("Replacement Sales Order transaction date changed"))
    if getdate(so.delivery_date) != getdate(original_dates["so_delivery"]):
        frappe.throw(_("Replacement Sales Order delivery date changed"))
    if getdate(dn.posting_date) != getdate(original_dates["dn_date"]):
        frappe.throw(_("Replacement Delivery Note posting date changed"))
    if getdate(invoice.posting_date) != getdate(original_dates["invoice_date"]):
        frappe.throw(_("Replacement Sales Invoice posting date changed"))
    if _line_dimension_map(so) != so_dimensions:
        frappe.throw(_("Replacement Sales Order dimensions differ from the original"))
    if _line_dimension_map(dn) != dn_dimensions:
        frappe.throw(_("Replacement Delivery Note dimensions differ from the original"))
    if _line_dimension_map(invoice) != invoice_dimensions:
        frappe.throw(_("Replacement Sales Invoice dimensions differ from the original"))
    if _shipping_dimensions(so, money(revised_order.get("shipping_total")) + money(revised_order.get("shipping_tax"))) != shipping_dimensions:
        frappe.throw(_("Replacement shipping accounting dimensions differ from the original"))
    if money(so.grand_total) != money(revised_order.get("total")):
        frappe.throw(_("Revised Sales Order total does not match Woo readback"))
    if money(invoice.grand_total) != money(revised_order.get("total")):
        frappe.throw(_("Revised Sales Invoice total does not match Woo readback"))
    if payment:
        allocated = sum(
            money(row.allocated_amount)
            for row in payment.references
            if row.reference_doctype == "Sales Invoice" and row.reference_name == invoice.name
        )
        if allocated != min(desired_payment, money(invoice.grand_total)):
            frappe.throw(_("Replacement Payment Entry allocation is inconsistent"))
    return {"sales_order": so, "delivery_note": dn, "sales_invoice": invoice, "payment_entry": payment}


def _verify_woo_readback(
    revised: dict[str, Any],
    normalized: dict[str, Any],
    expected: dict[str, Any],
    idempotency_key: str | None = None,
):
    if money(revised.get("total_tax")):
        frappe.throw(_("Woo readback introduced tax that is not mapped in ERPNext"))
    if money(revised.get("total")) != money(expected["expected_total_before_tax"]):
        frappe.throw(
            _("Woo readback total {0} does not match requested total {1}").format(
                revised.get("total"), expected["expected_total_before_tax"]
            )
        )
    wanted_lines = sorted(
        (
            int(row["source_line_item_id"]),
            int(row["product_id"]),
            int(row["variation_id"]),
            str(quantity(row["quantity"])),
            str(money(row["line_subtotal"])),
            str(money(row["line_total"])),
            str(row["tax_class"] or ""),
        )
        for row in expected["items"]
    )
    actual_lines = sorted(
        (
            int(row.get("id") or 0),
            int(row.get("product_id") or 0),
            int(row.get("variation_id") or 0),
            str(quantity(row.get("quantity") or 0)),
            str(money(row.get("subtotal"))),
            str(money(row.get("total"))),
            str(row.get("tax_class") or ""),
        )
        for row in revised.get("line_items") or []
    )
    if actual_lines != wanted_lines:
        frappe.throw(_("Woo line-item readback does not match the approved amendment"))
    shipping_total = sum(
        (money(row.get("total")) + money(row.get("total_tax")) for row in revised.get("shipping_lines") or []),
        Decimal("0.00"),
    )
    if shipping_total != money(expected["shipping_total"]):
        frappe.throw(_("Woo shipping readback does not match the approved amendment"))
    billing = revised.get("billing") or {}
    for fieldname, expected_value in normalized["customer"].items():
        if str(billing.get(fieldname) or "").strip() != str(expected_value or "").strip():
            frappe.throw(_("Woo billing field {0} does not match the approved amendment").format(fieldname))
    if str(revised.get("currency") or "") != expected["currency"]:
        frappe.throw(_("Woo currency changed during amendment"))
    if str(revised.get("payment_method") or "") != expected["payment_method"]:
        frappe.throw(_("Woo payment gateway changed during amendment"))
    if str(revised.get("date_paid") or "") != expected["date_paid"]:
        frappe.throw(_("Woo paid-date/payment state changed during amendment"))
    if idempotency_key and not any(
        row.get("key") == "_bbd_amendment_key" and str(row.get("value") or "") == idempotency_key
        for row in revised.get("meta_data") or []
    ):
        frappe.throw(_("Woo amendment idempotency marker is missing from readback"))
    expected_status = "on-hold" if normalized["payment_action"] == "put_on_hold" else normalized["status"]
    if expected_status and revised.get("status") != expected_status:
        frappe.throw(_("Woo readback status does not match the requested status"))


def _amendment_idempotency_key(
    channel: str,
    order_id: int,
    before_hash: str,
    chain_fingerprint: str,
    normalized: dict[str, Any],
) -> str:
    return payload_hash(
        {
            "operation": "amend",
            "channel": channel,
            "order_id": order_id,
            "before_hash": before_hash,
            "chain_fingerprint": chain_fingerprint,
            "request_hash": payload_hash(normalized),
        }
    )


def _has_amendment_marker(order: dict[str, Any], idempotency_key: str) -> bool:
    return any(
        row.get("key") == "_bbd_amendment_key" and str(row.get("value") or "") == idempotency_key
        for row in order.get("meta_data") or []
    )


def _applied_response(audit):
    evidence = json.loads(audit.resulting_erp_documents or "{}")
    return {
        "status": "Applied",
        "amendment": audit.name,
        "erp_documents": _evidence_documents(evidence),
    }


def _resume_incomplete_amendment(audit, normalized: dict[str, Any], idempotency_key: str):
    stored_request = json.loads(audit.requested_payload or "{}")
    if payload_hash(stored_request) != payload_hash(normalized):
        frappe.throw(_("The retry payload differs from amendment {0}").format(audit.name))
    before_order = json.loads(audit.before_payload or "{}")
    if not before_order:
        frappe.throw(_("Amendment {0} has no recoverable Woo before-state").format(audit.name))
    context = _fetch_context(audit.channel, int(audit.woo_order_id))
    current_order = context["order"]
    if not _has_amendment_marker(current_order, idempotency_key):
        current_snapshot = order_snapshot(current_order, _line_item_codes(context["channel"], current_order))
        if audit.status == "Pending" and payload_hash(current_snapshot) == audit.before_hash:
            return None
        audit.status = "Manual Recovery"
        audit.error = "Woo state does not contain the amendment marker and does not match the stored before-state."
        audit.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.throw(_("Amendment {0} requires manual recovery").format(audit.name))

    _payload, expected = build_woo_update(before_order, normalized, _reverse_mappings(context["channel"]))
    _verify_woo_readback(current_order, normalized, expected, idempotency_key)
    previous_evidence = json.loads(audit.previous_erp_documents or "{}")
    _assert_chain_matches_evidence(context["chain"], previous_evidence)
    if audit.status == "Pending":
        audit.status = "Woo Applied"
        audit.after_payload = json.dumps(current_order, indent=2, default=str)
        audit.save(ignore_permissions=True)
        frappe.db.commit()

    before_snapshot = order_snapshot(before_order, _line_item_codes(context["channel"], before_order))
    changed = _changed(before_snapshot, normalized, expected)
    rebuild_required = any(changed[key] for key in ("items", "shipping", "discount", "customer", "payment", "value"))
    _assert_open_dates(context["chain"], context["channel"].company)
    if rebuild_required:
        _assert_stock(context["channel"], context["chain"], normalized)
    desired_payment = money(audit.payment_amount_preserved)
    if _payment_amount(context["chain"]) != desired_payment:
        frappe.throw(_("ERP payment allocation changed while amendment {0} was incomplete").format(audit.name))

    try:
        new_chain = (
            _rebuild_erp(context, current_order, audit, desired_payment)
            if rebuild_required
            else context["chain"]
        )
        result_summary = _chain_evidence(new_chain)
        readback = context["client"].get_order(int(audit.woo_order_id))
        _verify_woo_readback(readback, normalized, expected, idempotency_key)
        audit.reload()
        audit.status = "Applied"
        audit.applied_at = now_datetime()
        audit.after_payload = json.dumps(readback, indent=2, default=str)
        audit.resulting_erp_documents = json.dumps(result_summary, indent=2, default=str)
        audit.error = None
        audit.save(ignore_permissions=True)
        frappe.db.commit()
        return {
            "status": "Applied",
            "amendment": audit.name,
            "woo_order": order_snapshot(readback, _line_item_codes(context["channel"], readback)),
            "erp_documents": _evidence_documents(result_summary),
            "warnings": json.loads(audit.warnings_json or "[]"),
        }
    except Exception:
        frappe.db.rollback()
        frappe.db.set_value(
            "BBD Order Amendment",
            audit.name,
            {"status": "Woo Applied", "error": frappe.get_traceback()},
            update_modified=True,
        )
        frappe.db.commit()
        raise


@frappe.whitelist()
def apply_order_amendment(
    channel: str,
    order_id: int | str,
    before_hash: str,
    amendment: str | dict[str, Any],
    confirmation: str,
    chain_fingerprint: str,
    pending_amendment: str = "",
):
    _require_permission()
    _settings(require_enabled=True)
    order_id = int(order_id)
    with frappe.cache.lock(order_lock_key(_channel(channel), order_id), timeout=600):
        amendment_payload = _json_arg(amendment)
        normalized_for_key = normalize_request(amendment_payload)
        approval_hash = payload_hash({"before_hash": before_hash, "chain_fingerprint": chain_fingerprint})
        token = confirmation_token(approval_hash, normalized_for_key, frappe.session.user)
        if confirmation != token:
            frappe.throw(_("Preview confirmation is stale. Preview the amendment again."))
        idempotency_key = _amendment_idempotency_key(
            channel, order_id, before_hash, chain_fingerprint, normalized_for_key
        )
        existing = frappe.db.get_value(
            "BBD Order Amendment",
            {"idempotency_key": idempotency_key},
            ["name", "status"],
            as_dict=True,
        )
        pending_audit = None
        if existing:
            existing_audit = frappe.get_doc("BBD Order Amendment", existing.name)
            if (
                existing.status in {"Pending", "Woo Applied"}
                and existing_audit.requested_by != frappe.session.user
                and "System Manager" not in frappe.get_roles()
            ):
                frappe.throw(_("Amendment {0} belongs to another requester").format(existing_audit.name))
            if existing.status == "Applied":
                return _applied_response(existing_audit)
            if existing.status in {"Pending", "Woo Applied"}:
                resumed = _resume_incomplete_amendment(existing_audit, normalized_for_key, idempotency_key)
                if resumed:
                    return resumed
                pending_audit = existing_audit

        context, normalized, woo_payload, expected, validation, _token = _prepare(
            channel, order_id, before_hash, amendment_payload
        )
        if validation["chain_fingerprint"] != chain_fingerprint:
            frappe.throw(_("The ERP document chain changed after preview. Preview the amendment again."))
        if pending_audit:
            audit = pending_audit
            already_applied = False
        else:
            audit, already_applied = _create_audit(
                context,
                normalized,
                validation,
                idempotency_key,
                pending_amendment=str(pending_amendment or "").strip(),
            )
        if already_applied:
            return _applied_response(audit)
        revised_order = None
        try:
            revised_order = context["client"].update_order(
                order_id,
                woo_payload,
                idempotency_key=idempotency_key,
            )
            _verify_woo_readback(revised_order, normalized, expected, idempotency_key)
            audit.reload()
            audit.status = "Woo Applied"
            audit.after_payload = json.dumps(revised_order, indent=2, default=str)
            audit.save(ignore_permissions=True)
            frappe.db.commit()

            desired_payment = money(validation["desired_payment"])
            if validation["rebuild_required"]:
                new_chain = _rebuild_erp(context, revised_order, audit, desired_payment)
            else:
                new_chain = context["chain"]
            result_summary = _chain_evidence(new_chain)

            # Keep the ERP rebuild uncommitted until Woo has passed a fresh final readback.
            readback = context["client"].get_order(order_id)
            _verify_woo_readback(readback, normalized, expected, idempotency_key)
            audit.reload()
            audit.status = "Applied"
            audit.applied_at = now_datetime()
            audit.after_payload = json.dumps(readback, indent=2, default=str)
            audit.resulting_erp_documents = json.dumps(result_summary, indent=2, default=str)
            audit.save(ignore_permissions=True)
            frappe.db.commit()
            return {
                "status": "Applied",
                "amendment": audit.name,
                "woo_order": order_snapshot(readback, _line_item_codes(context["channel"], readback)),
                "erp_documents": _evidence_documents(result_summary),
                "warnings": validation["warnings"],
            }
        except Exception:
            frappe.db.rollback()
            rollback_error = None
            rollback_status = "Failed"
            if not revised_order:
                try:
                    candidate = context["client"].get_order(order_id)
                    if _has_amendment_marker(candidate, idempotency_key):
                        revised_order = candidate
                except Exception as exc:
                    rollback_error = f"Could not determine whether Woo applied the amendment: {exc}"
                    rollback_status = "Manual Recovery"
            if revised_order:
                try:
                    restored = context["client"].update_order(
                        order_id,
                        _restore_payload(context["order"], revised_order),
                        idempotency_key=payload_hash(
                            {"operation": "rollback", "idempotency_key": idempotency_key}
                        ),
                    )
                    restored_snapshot = order_snapshot(restored, _line_item_codes(context["channel"], restored))
                    if payload_hash(restored_snapshot) != context["before_hash"]:
                        rollback_error = "Woo rollback readback hash differs from the stored before state"
                        rollback_status = "Manual Recovery"
                    else:
                        rollback_status = "Rolled Back"
                except Exception as exc:
                    rollback_error = str(exc)
                    rollback_status = "Manual Recovery"
            error = frappe.get_traceback()
            if rollback_error:
                error += f"\nWoo rollback warning: {rollback_error}"
            frappe.db.set_value(
                "BBD Order Amendment",
                audit.name,
                {"status": rollback_status, "error": error},
                update_modified=True,
            )
            frappe.db.commit()
            raise


@frappe.whitelist()
def rollback_order_amendment(amendment: str, confirmation: str, reason: str):
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only a System Manager can roll back an applied amendment"), frappe.PermissionError)
    _settings(require_enabled=True)
    reason = str(reason or "").strip()
    if len(reason) < 10:
        frappe.throw(_("Provide a specific rollback reason of at least 10 characters"))
    initial_audit = frappe.get_doc("BBD Order Amendment", amendment)
    initial_audit.check_permission("read")
    if str(confirmation or "").strip() != initial_audit.name:
        frappe.throw(_("Rollback confirmation does not match amendment {0}").format(initial_audit.name))

    channel = _channel(initial_audit.channel)
    order_id = int(initial_audit.woo_order_id)
    with frappe.cache.lock(order_lock_key(channel, order_id), timeout=600):
        audit = frappe.get_doc("BBD Order Amendment", amendment)
        if audit.status == "Rolled Back":
            rollback_evidence = json.loads(audit.rollback_erp_documents or "{}")
            return {
                "status": "Rolled Back",
                "amendment": audit.name,
                "erp_documents": _evidence_documents(rollback_evidence),
            }
        if audit.status != "Applied":
            frappe.throw(_("Only an Applied amendment can be rolled back"))
        if audit.channel != initial_audit.channel or int(audit.woo_order_id) != order_id:
            frappe.throw(_("Amendment identity changed while waiting for the order lock"))

        before_order = json.loads(audit.before_payload or "{}")
        after_order = json.loads(audit.after_payload or "{}")
        normalized = normalize_request(json.loads(audit.requested_payload or "{}"))
        if not before_order or not after_order:
            frappe.throw(_("Amendment {0} does not contain complete rollback evidence").format(audit.name))
        rollback_key = payload_hash({"operation": "manual-rollback", "amendment": audit.name})
        context = _fetch_context(audit.channel, order_id)
        current_chain = context["chain"]
        payment = current_chain.get("payment_entry")
        if payment and payment.clearance_date:
            frappe.throw(
                _("Payment Entry {0} has clearance date {1}; automatic rollback is prohibited").format(
                    payment.name, payment.clearance_date
                )
            )
        reconciliations = _bank_reconciliation_rows(payment.name if payment else "")
        if reconciliations:
            frappe.throw(
                _("Payment Entry {0} is linked to Bank Transaction(s) {1}; automatic rollback is prohibited").format(
                    payment.name, ", ".join(reconciliations)
                )
            )

        before_snapshot = order_snapshot(before_order, _line_item_codes(channel, before_order))
        _rollback_payload, expected = build_woo_update(before_order, normalized, _reverse_mappings(channel))
        changed = _changed(before_snapshot, normalized, expected)
        rebuild_required = any(
            changed[key] for key in ("items", "shipping", "discount", "customer", "payment", "value")
        )
        applied_evidence = json.loads(audit.resulting_erp_documents or "{}")
        _assert_chain_matches_evidence(
            current_chain,
            applied_evidence,
            amendment_name=audit.name if rebuild_required else None,
        )

        current_snapshot = order_snapshot(context["order"], _line_item_codes(channel, context["order"]))
        after_snapshot = order_snapshot(after_order, _line_item_codes(channel, after_order))
        current_hash = payload_hash(current_snapshot)
        after_hash = payload_hash(after_snapshot)
        resumed_woo_rollback = current_hash == audit.before_hash and _has_amendment_marker(
            context["order"], rollback_key
        )
        if current_hash != after_hash and not resumed_woo_rollback:
            frappe.throw(_("Woo order changed after amendment {0}; rollback requires manual review").format(audit.name))

        desired_payment = money(audit.payment_amount_preserved)
        if rebuild_required:
            rollback_request = {
                "items": [
                    {
                        "line_item_id": row["line_item_id"],
                        "item_code": row["item_code"],
                        "quantity": row["quantity"],
                    }
                    for row in before_snapshot["items"]
                ]
            }
            _assert_open_dates(current_chain, channel.company)
            _assert_stock(channel, current_chain, rollback_request)
            if _payment_amount(current_chain) != desired_payment:
                frappe.throw(_("ERP payment allocation changed after amendment {0}").format(audit.name))

        restored_order = context["order"] if resumed_woo_rollback else None
        try:
            if not restored_order:
                restored_order = context["client"].update_order(
                    order_id,
                    _restore_payload(before_order, context["order"]),
                    idempotency_key=rollback_key,
                )
            restored_snapshot = order_snapshot(restored_order, _line_item_codes(channel, restored_order))
            if payload_hash(restored_snapshot) != audit.before_hash:
                frappe.throw(_("Woo rollback readback does not match the stored before-state"))

            if rebuild_required:
                rollback_external_id = f"{order_id}-A{audit.revision}-R"
                new_chain = _rebuild_erp(
                    context,
                    restored_order,
                    audit,
                    desired_payment,
                    external_order_id=rollback_external_id,
                )
            else:
                new_chain = current_chain
            rollback_summary = _chain_evidence(new_chain)
            final_order = context["client"].get_order(order_id)
            final_snapshot = order_snapshot(final_order, _line_item_codes(channel, final_order))
            if payload_hash(final_snapshot) != audit.before_hash:
                frappe.throw(_("Final Woo rollback readback does not match the stored before-state"))

            audit.reload()
            audit.status = "Rolled Back"
            audit.rollback_requested_payload = json.dumps(
                {
                    "requested_by": frappe.session.user,
                    "requested_at": str(now_datetime()),
                    "reason": reason,
                    "woo_payload": _restore_payload(before_order, context["order"]),
                },
                indent=2,
                default=str,
            )
            audit.rollback_result_payload = json.dumps(final_order, indent=2, default=str)
            audit.rollback_erp_documents = json.dumps(rollback_summary, indent=2, default=str)
            audit.rollback_instructions = (
                (audit.rollback_instructions or "")
                + f"\nManual rollback completed by {frappe.session.user} at {now_datetime()}: {reason}"
            )
            audit.error = None
            audit.save(ignore_permissions=True)
            frappe.db.commit()
            return {
                "status": "Rolled Back",
                "amendment": audit.name,
                "erp_documents": _evidence_documents(rollback_summary),
            }
        except Exception:
            frappe.db.rollback()
            recovery_status = "Applied"
            recovery_note = ""
            if restored_order:
                try:
                    compensated = context["client"].update_order(
                        order_id,
                        _restore_payload(after_order, restored_order),
                        idempotency_key=payload_hash({"operation": "rollback-compensation", "amendment": audit.name}),
                    )
                    compensated_snapshot = order_snapshot(compensated, _line_item_codes(channel, compensated))
                    if payload_hash(compensated_snapshot) != payload_hash(after_snapshot):
                        recovery_status = "Manual Recovery"
                        recovery_note = " Woo compensation did not restore the applied state."
                except Exception as exc:
                    recovery_status = "Manual Recovery"
                    recovery_note = f" Woo compensation failed: {exc}"
            frappe.db.set_value(
                "BBD Order Amendment",
                audit.name,
                {
                    "status": recovery_status,
                    "error": frappe.get_traceback() + recovery_note,
                },
                update_modified=True,
            )
            frappe.db.commit()
            raise
