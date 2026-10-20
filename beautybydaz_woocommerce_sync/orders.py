from __future__ import annotations

from decimal import Decimal

import frappe
from frappe.utils import getdate, nowdate

from .audit import create_log, finish_log
from .client import WooClient
from .config import safe_statuses
from .core import (
    decimal_value,
    is_paid_order,
    payment_entries_are_enabled,
    payment_idempotency_key,
    payment_sync_decision,
    resolve_payment_route,
    validate_payment_values,
)
from .mapping import get_mapping


def _field(doc, name, value):
    if doc.meta.has_field(name):
        doc.set(name, value)


def _base_fields(doc, channel, order, refund_id=None):
    if not refund_id:
        _field(doc, "custom_woocommerce_order_id", str(order["id"]))
        _field(doc, "custom_woocommerce_order_number", str(order.get("number") or order["id"]))
    else:
        _field(doc, "custom_woocommerce_refund_id", str(refund_id))
    _field(doc, "custom_woo_channel", channel.alias)
    _field(doc, "custom_woo_site", channel.base_url.rstrip("/"))


def _country_name(code):
    if not code:
        return "Nigeria"
    return frappe.db.get_value("Country", {"code": code}, "name") or {"NG": "Nigeria"}.get(code, code)


def _customer(channel, order):
    billing = order.get("billing") or {}
    email = (billing.get("email") or "").strip().lower()
    customer_id = str(order.get("customer_id") or "")
    filters = {"custom_woo_site": channel.base_url.rstrip("/")}
    if customer_id and customer_id != "0":
        filters["custom_woocommerce_customer_id"] = customer_id
    elif email:
        filters["custom_woocommerce_email"] = email
    name = frappe.db.get_value("Customer", filters, "name")
    if name:
        customer = frappe.get_doc("Customer", name)
    else:
        display_name = " ".join(x for x in (billing.get("first_name"), billing.get("last_name")) if x).strip()
        display_name = billing.get("company") or display_name or f"Woo Customer {customer_id or order['id']}"
        customer = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": display_name,
                "customer_type": "Company" if billing.get("company") else "Individual",
                "customer_group": channel.customer_group,
                "territory": "All Territories",
                "custom_woocommerce_customer_id": customer_id,
                "custom_woocommerce_email": email,
                "custom_woo_channel": channel.alias,
                "custom_woo_site": channel.base_url.rstrip("/"),
            }
        )
        customer.insert(ignore_permissions=True)
    _contact(customer, billing, customer_id)
    _address(customer, billing, customer_id, "Billing")
    shipping = order.get("shipping") or {}
    if shipping.get("address_1") or shipping.get("city"):
        _address(customer, shipping, customer_id, "Shipping")
    return customer


def _contact(customer, data, customer_id):
    email = (data.get("email") or "").strip().lower()
    name = None
    if customer_id and customer_id != "0":
        name = frappe.db.get_value("Contact", {"custom_woocommerce_customer_id": customer_id}, "name")
    if not name and email:
        name = frappe.db.get_value("Contact Email", {"email_id": email}, "parent")
    if name:
        return frappe.get_doc("Contact", name)
    doc = frappe.get_doc(
        {
            "doctype": "Contact",
            "first_name": data.get("first_name") or customer.customer_name,
            "last_name": data.get("last_name"),
            "custom_woocommerce_customer_id": customer_id,
            "links": [{"link_doctype": "Customer", "link_name": customer.name}],
        }
    )
    if email:
        doc.append("email_ids", {"email_id": email, "is_primary": 1})
    if data.get("phone"):
        doc.append("phone_nos", {"phone": data["phone"], "is_primary_phone": 1})
    doc.insert(ignore_permissions=True)
    return doc


def _address(customer, data, customer_id, address_type):
    if not data.get("address_1") and not data.get("city"):
        return None
    title = f"{customer.name}-{address_type}"
    name = frappe.db.get_value("Address", {"address_title": title, "address_type": address_type}, "name")
    doc = frappe.get_doc("Address", name) if name else frappe.new_doc("Address")
    doc.update(
        {
            "address_title": title,
            "address_type": address_type,
            "address_line1": data.get("address_1") or "Not provided",
            "address_line2": data.get("address_2"),
            "city": data.get("city") or "Not provided",
            "state": data.get("state"),
            "pincode": data.get("postcode"),
            "country": _country_name(data.get("country")),
            "phone": data.get("phone"),
            "email_id": data.get("email"),
            "custom_woocommerce_customer_id": customer_id,
        }
    )
    if not name:
        doc.append("links", {"link_doctype": "Customer", "link_name": customer.name})
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)
    return doc


def _items(channel, order):
    rows = []
    for line in order.get("line_items") or []:
        mapping = get_mapping(channel.alias, int(line["product_id"]), int(line.get("variation_id") or 0))
        qty = decimal_value(line.get("quantity"))
        if qty <= 0:
            frappe.throw(f"Woo line {line.get('id')} has invalid quantity {qty}")
        total = decimal_value(line.get("total")) + decimal_value(line.get("total_tax"))
        rows.append(
            {
                "item_code": mapping.erp_item,
                "item_name": line.get("name"),
                "warehouse": channel.warehouse,
                "qty": float(qty),
                "rate": float(total / qty),
                "income_account": channel.income_account,
                "cost_center": channel.cost_center,
                "custom_woocommerce_line_item_id": str(line.get("id") or ""),
            }
        )
    if not rows:
        frappe.throw(f"Woo order {order['id']} has no line items")
    return rows


def _date(order):
    return getdate((order.get("date_paid") or order.get("date_created") or nowdate())[:10])


def _create_sales_order(channel, order, customer):
    existing = frappe.db.get_value(
        "Sales Order", {"custom_woo_site": channel.base_url.rstrip("/"), "custom_woocommerce_order_id": str(order["id"]), "docstatus": ["<", 2]}, "name"
    )
    if existing:
        return frappe.get_doc("Sales Order", existing)
    if decimal_value(order.get("total_tax")):
        frappe.throw(f"Woo order {order['id']} includes tax but no tax mapping is configured")
    so = frappe.get_doc(
        {
            "doctype": "Sales Order",
            "company": channel.company,
            "customer": customer.name,
            "transaction_date": _date(order),
            "delivery_date": _date(order),
            "currency": order.get("currency") or frappe.get_cached_value("Company", channel.company, "default_currency"),
            "selling_price_list": channel.price_list,
            "price_list_currency": order.get("currency") or frappe.get_cached_value("Price List", channel.price_list, "currency"),
            "ignore_pricing_rule": 1,
            "items": _items(channel, order),
            "remarks": f"WooCommerce {channel.alias} order {order.get('number') or order['id']}",
        }
    )
    _base_fields(so, channel, order)
    shipping_total = decimal_value(order.get("shipping_total")) + decimal_value(order.get("shipping_tax"))
    if shipping_total:
        if not channel.shipping_income_account:
            frappe.throw(f"Woo order {order['id']} has shipping but channel has no Shipping Income Account")
        so.append(
            "taxes",
            {
                "charge_type": "Actual",
                "account_head": channel.shipping_income_account,
                "description": "WooCommerce Shipping",
                "tax_amount": float(shipping_total),
                "cost_center": channel.cost_center,
            },
        )
    so.insert(ignore_permissions=True)
    so.submit()
    return so


def _create_delivery_note(channel, order, so):
    existing = frappe.db.get_value(
        "Delivery Note", {"custom_woo_site": channel.base_url.rstrip("/"), "custom_woocommerce_order_id": str(order["id"]), "docstatus": ["<", 2]}, "name"
    )
    if existing:
        return frappe.get_doc("Delivery Note", existing)
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

    dn = make_delivery_note(so.name)
    _base_fields(dn, channel, order)
    for row in dn.items:
        row.warehouse = channel.warehouse
    dn.insert(ignore_permissions=True)
    if channel.allow_negative_stock_on_paid_orders:
        from types import MethodType
        from erpnext.controllers.selling_controller import SellingController

        def update_stock_ledger_for_paid_woo_order(self, allow_negative_stock=False):
            return SellingController.update_stock_ledger(self, allow_negative_stock=True)

        dn.update_stock_ledger = MethodType(update_stock_ledger_for_paid_woo_order, dn)
    dn.submit()
    return dn


def _create_sales_invoice(channel, order, so):
    existing = frappe.db.get_value(
        "Sales Invoice", {"custom_woo_site": channel.base_url.rstrip("/"), "custom_woocommerce_order_id": str(order["id"]), "docstatus": ["<", 2]}, "name"
    )
    if existing:
        return frappe.get_doc("Sales Invoice", existing)
    from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

    invoice = make_sales_invoice(so.name)
    _base_fields(invoice, channel, order)
    invoice.update_stock = 0
    invoice.insert(ignore_permissions=True)
    invoice.submit()
    return invoice


def _existing_payment_entry(invoice_name):
    rows = frappe.db.sql(
        """
        SELECT pe.name
        FROM `tabPayment Entry` pe
        INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
        WHERE per.reference_doctype = 'Sales Invoice'
          AND per.reference_name = %s
          AND pe.docstatus < 2
        ORDER BY pe.docstatus DESC, pe.creation ASC
        LIMIT 1
        """,
        (invoice_name,),
        as_dict=True,
    )
    return rows[0].name if rows else None


def _validate_payment_master_data(channel, route):
    mode_enabled = frappe.db.get_value("Mode of Payment", route["mode_of_payment"], "enabled")
    if mode_enabled is None:
        frappe.throw(f"Mode of Payment does not exist: {route['mode_of_payment']}")
    if not int(mode_enabled):
        frappe.throw(f"Mode of Payment is disabled: {route['mode_of_payment']}")

    account = frappe.db.get_value(
        "Account",
        route["clearing_account"],
        ["name", "company", "disabled", "is_group"],
        as_dict=True,
    )
    if not account:
        frappe.throw(f"Clearing account does not exist: {route['clearing_account']}")
    if account.company != channel.company:
        frappe.throw(
            f"Clearing account {account.name} belongs to {account.company}, not {channel.company}"
        )
    if int(account.disabled or 0):
        frappe.throw(f"Clearing account is disabled: {account.name}")
    if int(account.is_group or 0):
        frappe.throw(f"Clearing account must be a ledger account: {account.name}")


def _validate_existing_payment(pe, route, amount, invoice_name):
    if pe.docstatus == 0:
        frappe.throw(
            f"Draft Payment Entry {pe.name} already references {invoice_name}; review or submit it before retrying"
        )
    if pe.mode_of_payment != route["mode_of_payment"] or pe.paid_to != route["clearing_account"]:
        frappe.throw(
            f"Existing Payment Entry {pe.name} uses {pe.mode_of_payment}/{pe.paid_to}, "
            f"expected {route['mode_of_payment']}/{route['clearing_account']}"
        )
    allocated = sum(
        decimal_value(row.allocated_amount)
        for row in pe.references
        if row.reference_doctype == "Sales Invoice" and row.reference_name == invoice_name
    )
    if allocated != amount:
        frappe.throw(
            f"Existing Payment Entry {pe.name} allocated {allocated} to {invoice_name}; expected {amount}"
        )
    return pe


def _create_payment_entry(channel, order, invoice):
    if invoice.docstatus != 1:
        frappe.throw(f"Sales Invoice {invoice.name} must be submitted before payment synchronization")
    if str(getattr(invoice, "custom_woo_channel", "") or "") != channel.alias:
        frappe.throw(
            f"Sales Invoice {invoice.name} Woo channel mismatch: "
            f"{getattr(invoice, 'custom_woo_channel', None)!r} != {channel.alias!r}"
        )
    if str(getattr(invoice, "custom_woocommerce_order_id", "") or "") != str(order["id"]):
        frappe.throw(
            f"Sales Invoice {invoice.name} Woo order mismatch: "
            f"{getattr(invoice, 'custom_woocommerce_order_id', None)!r} != {order['id']!r}"
        )

    route = resolve_payment_route(order)
    _validate_payment_master_data(channel, route)
    existing_name = _existing_payment_entry(invoice.name)
    decision, existing_name = payment_sync_decision(existing_name)
    if decision == "existing":
        pe = frappe.get_doc("Payment Entry", existing_name)
        allocated = sum(
            decimal_value(row.allocated_amount)
            for row in pe.references
            if row.reference_doctype == "Sales Invoice" and row.reference_name == invoice.name
        )
        amount = validate_payment_values(order, allocated, invoice.currency)
        pe = _validate_existing_payment(pe, route, amount, invoice.name)
        invoice.reload()
        if decimal_value(invoice.outstanding_amount) != 0:
            frappe.throw(
                f"Existing Payment Entry {pe.name} references {invoice.name}, but invoice outstanding is "
                f"{decimal_value(invoice.outstanding_amount)}, expected 0"
            )
        return pe, route, amount, "existing"

    amount = validate_payment_values(order, invoice.outstanding_amount, invoice.currency)
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    pe = get_payment_entry(
        "Sales Invoice",
        invoice.name,
        bank_account=route["clearing_account"],
        reference_date=_date(order),
    )
    pe.mode_of_payment = route["mode_of_payment"]
    pe.paid_to = route["clearing_account"]
    pe.reference_no = f"Woo {channel.alias} order {order['id']}"
    pe.reference_date = _date(order)
    pe.posting_date = _date(order)
    pe.remarks = (
        f"WooCommerce {channel.alias} order {order.get('number') or order['id']} "
        f"paid via {route['gateway']}"
    )
    pe.paid_amount = float(amount)
    pe.received_amount = float(amount)
    matched_reference = False
    for reference in pe.references:
        if reference.reference_doctype == "Sales Invoice" and reference.reference_name == invoice.name:
            reference.allocated_amount = float(amount)
            matched_reference = True
    if not matched_reference:
        frappe.throw(f"Payment Entry draft did not include Sales Invoice reference {invoice.name}")
    pe.insert(ignore_permissions=True)
    pe.submit()
    invoice.reload()
    if decimal_value(invoice.outstanding_amount) != 0:
        frappe.throw(
            f"Payment Entry {pe.name} submitted but Sales Invoice {invoice.name} outstanding is "
            f"{decimal_value(invoice.outstanding_amount)}, expected 0"
        )
    return pe, route, amount, "created"


def import_paid_order(channel, order, *, client=None):
    order_id = int(order["id"])
    key = f"order:{channel.alias}:{order_id}"
    with frappe.cache.lock(f"bbd-woo:{key}", timeout=300):
        log = create_log("Order", "Woo to ERP", channel.alias, order_id, key, {
                "id": order_id,
                "status": order.get("status"),
                "date_paid": order.get("date_paid"),
                "currency": order.get("currency"),
                "total": order.get("total"),
                "payment_method": order.get("payment_method"),
                "payment_method_title": order.get("payment_method_title"),
            },
        )
        try:
            if order_id < int(channel.order_floor or 0):
                finish_log(log, "Skipped", {"reason": "below channel order floor"})
                return {"status": "skipped", "reason": "below_floor"}
            if not is_paid_order(order, safe_statuses()):
                finish_log(log, "Skipped", {"reason": "order is not paid", "status": order.get("status")})
                return {"status": "skipped", "reason": "not_paid"}
            customer = _customer(channel, order)
            so = _create_sales_order(channel, order, customer)
            dn = _create_delivery_note(channel, order, so)
            invoice = _create_sales_invoice(channel, order, so)
            result = {
                "woo_order_id": order_id,
                "customer": customer.name,
                "sales_order": so.name,
                "delivery_note": dn.name,
                "sales_invoice": invoice.name,
                "payment_entry": None,
                "payment_gateway": None,
                "payment_amount": None,
                "payment_status": "disabled",
            }
            settings = frappe.get_single("BBD Woo Sync Settings")
            if payment_entries_are_enabled(settings.payment_entries_enabled):
                payment_key = payment_idempotency_key(channel.alias, order_id, invoice.name)
                with frappe.cache.lock(f"bbd-woo:{payment_key}", timeout=300):
                    pe, route, amount, payment_status = _create_payment_entry(channel, order, invoice)
                result.update(
                    {
                        "payment_entry": pe.name,
                        "payment_gateway": route["gateway"],
                        "payment_amount": float(amount),
                        "payment_status": payment_status,
                    }
                )
            invoice.reload()
            result["sales_invoice_outstanding"] = float(invoice.outstanding_amount)
            reference_doctype = "Payment Entry" if result["payment_entry"] else "Sales Invoice"
            reference_name = result["payment_entry"] or invoice.name
            finish_log(log, "Success", result, reference_doctype, reference_name)
            return {"status": "success", **result}
        except Exception as exc:
            frappe.db.rollback()
            finish_log(log, "Failed", error=exc, traceback_text=frappe.get_traceback())
            raise


def _return_doc(channel, order, refund, doctype, maker):
    target_id = str(refund["id"])
    existing = frappe.db.get_value(doctype, {"custom_woo_site": channel.base_url.rstrip("/"), "custom_woocommerce_refund_id": target_id, "docstatus": ["<", 2]}, "name")
    if existing:
        return frappe.get_doc(doctype, existing)
    original = frappe.db.get_value(doctype, {"custom_woo_site": channel.base_url.rstrip("/"), "custom_woocommerce_order_id": str(order["id"]), "docstatus": 1}, "name")
    if not original:
        frappe.throw(f"Cannot import refund {target_id}: submitted {doctype} for order {order['id']} was not found")
    doc = maker(original)
    _base_fields(doc, channel, order, target_id)
    refund_lines = {}
    for detail in refund.get("line_items") or []:
        source_id = None
        for meta in detail.get("meta_data") or []:
            if meta.get("key") in {"_refunded_item_id", "refunded_item_id", "_line_item_id"} and meta.get("value"):
                source_id = str(meta.get("value"))
                break
        refund_lines[source_id or str(detail.get("id"))] = detail
    if refund_lines:
        keep = []
        for row in doc.items:
            source_id = str(getattr(row, "custom_woocommerce_line_item_id", "") or "")
            detail = refund_lines.get(source_id)
            if not detail:
                continue
            qty = abs(decimal_value(detail.get("quantity")))
            row.qty = -float(qty)
            amount = abs(decimal_value(detail.get("total")) + decimal_value(detail.get("total_tax")))
            if qty and amount:
                row.rate = float(amount / qty)
            keep.append(row)
        doc.set("items", keep)
    if not doc.items:
        frappe.throw(f"Refund {target_id} has no mappable line items")
    doc.set("taxes", [])
    for fieldname, value in (
        ("shipping_rule", None),
        ("coupon_code", None),
        ("additional_discount_percentage", 0),
        ("discount_amount", 0),
        ("base_discount_amount", 0),
    ):
        if doc.meta.has_field(fieldname):
            doc.set(fieldname, value)
    doc.calculate_taxes_and_totals()
    expected = abs(decimal_value(refund.get("amount")))
    actual = abs(decimal_value(doc.grand_total))
    if abs(expected - actual) > decimal_value("0.01"):
        frappe.throw(f"Refund {target_id} total mismatch: Woo={expected}, {doctype}={actual}")
    doc.insert(ignore_permissions=True)
    doc.submit()
    return doc


def import_refund(channel, order, refund):
    refund_id = int(refund["id"])
    key = f"refund:{channel.alias}:{refund_id}"
    with frappe.cache.lock(f"bbd-woo:{key}", timeout=300):
        log = create_log("Refund", "Woo to ERP", channel.alias, refund_id, key, {"order_id": order["id"], "refund_id": refund_id})
        try:
            if decimal_value(refund.get("amount")) <= 0:
                finish_log(log, "Skipped", {"reason": "zero-value refund"})
                return {"status": "skipped"}
            from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return as make_invoice_return
            from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return as make_delivery_return

            dn = _return_doc(channel, order, refund, "Delivery Note", make_delivery_return)
            si = _return_doc(channel, order, refund, "Sales Invoice", make_invoice_return)
            result = {"delivery_note_return": dn.name, "sales_invoice_return": si.name}
            finish_log(log, "Success", result, "Sales Invoice", si.name)
            return {"status": "success", **result}
        except Exception as exc:
            frappe.db.rollback()
            finish_log(log, "Failed", error=exc, traceback_text=frappe.get_traceback())
            raise


def poll_channel(channel):
    client = WooClient(channel)
    orders = sorted(client.list_orders(frappe.get_single("BBD Woo Sync Settings").order_poll_limit or 100), key=lambda row: int(row["id"]))
    summary = {"orders": 0, "refunds": 0, "skipped": 0, "errors": []}
    for order in orders:
        if int(order["id"]) < int(channel.order_floor or 0):
            continue
        try:
            result = import_paid_order(channel, order, client=client)
            summary["orders" if result["status"] == "success" else "skipped"] += 1
            if result["status"] == "success" and channel.refund_sync_enabled:
                for refund in client.list_refunds(order["id"]):
                    refund_result = import_refund(channel, order, refund)
                    if refund_result["status"] == "success":
                        summary["refunds"] += 1
        except Exception as exc:
            summary["errors"].append({"order_id": order.get("id"), "error": str(exc)})
    channel.db_set("last_poll_at", frappe.utils.now_datetime(), update_modified=False)
    return summary
