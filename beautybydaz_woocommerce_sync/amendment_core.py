from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

MONEY = Decimal("0.01")
ALLOWED_ORDER_STATUSES = {"processing", "on-hold"}
ALLOWED_PAYMENT_ACTIONS = {"preserve", "put_on_hold"}
IDENTITY_FIELDS = ("first_name", "last_name", "company", "email", "phone")


class AmendmentValidationError(ValueError):
    pass


def money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AmendmentValidationError(f"Invalid monetary value: {value!r}") from exc


def quantity(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AmendmentValidationError(f"Invalid quantity: {value!r}") from exc
    if result < 0:
        raise AmendmentValidationError("Quantity cannot be negative")
    if result != result.to_integral_value():
        raise AmendmentValidationError("Woo order quantity must be a whole number")
    return result


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def payment_state(order: dict[str, Any]) -> str:
    if order.get("date_paid"):
        return "paid"
    return "unpaid"


def _line_rate(line: dict[str, Any], field: str = "total") -> Decimal:
    qty = quantity(line.get("quantity") or 0)
    if qty <= 0:
        return Decimal("0.00")
    return (money(line.get(field)) + money(line.get(f"{field}_tax"))) / qty


def order_snapshot(order: dict[str, Any], item_codes: dict[str, str] | None = None) -> dict[str, Any]:
    item_codes = item_codes or {}
    billing = order.get("billing") or {}
    return {
        "id": int(order["id"]),
        "number": str(order.get("number") or order["id"]),
        "status": str(order.get("status") or ""),
        "payment_state": payment_state(order),
        "date_created": order.get("date_created"),
        "date_paid": order.get("date_paid"),
        "currency": str(order.get("currency") or ""),
        "total": str(money(order.get("total"))),
        "discount_total": str(money(order.get("discount_total"))),
        "shipping_total": str(money(order.get("shipping_total")) + money(order.get("shipping_tax"))),
        "payment_method": str(order.get("payment_method") or ""),
        "payment_method_title": str(order.get("payment_method_title") or ""),
        "customer": {field: str(billing.get(field) or "") for field in IDENTITY_FIELDS},
        "items": [
            {
                "line_item_id": str(line.get("id") or ""),
                "product_id": int(line.get("product_id") or 0),
                "variation_id": int(line.get("variation_id") or 0),
                "item_code": item_codes.get(str(line.get("id") or ""), ""),
                "name": str(line.get("name") or ""),
                "quantity": str(quantity(line.get("quantity") or 0)),
                "unit_rate": str(_line_rate(line, "subtotal")),
                "net_unit_rate": str(_line_rate(line)),
                "unit_subtotal": str(_line_rate(line, "subtotal")),
                "line_total": str(money(line.get("total")) + money(line.get("total_tax"))),
                "tax_class": str(line.get("tax_class") or ""),
            }
            for line in order.get("line_items") or []
        ],
        "shipping_lines": [
            {
                "id": int(row.get("id") or 0),
                "method_id": str(row.get("method_id") or ""),
                "method_title": str(row.get("method_title") or ""),
                "total": str(money(row.get("total")) + money(row.get("total_tax"))),
            }
            for row in order.get("shipping_lines") or []
        ],
    }


def normalize_request(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise AmendmentValidationError("Amendment request must be an object")
    reason = str(request.get("reason") or "").strip()
    if len(reason) < 10:
        raise AmendmentValidationError("A specific amendment reason of at least 10 characters is required")
    status = str(request.get("status") or "").strip().lower()
    if status and status not in ALLOWED_ORDER_STATUSES:
        raise AmendmentValidationError(f"Unsupported Woo order status: {status}")
    payment_action = str(request.get("payment_action") or "preserve").strip().lower()
    if payment_action not in ALLOWED_PAYMENT_ACTIONS:
        raise AmendmentValidationError(f"Unsupported payment action: {payment_action}")
    order_date = str(request.get("order_date") or "").strip()
    if order_date:
        raise AmendmentValidationError(
            "WooCommerce REST v3 does not safely support order-date amendments; use a separately approved workflow"
        )
    customer = request.get("customer") or {}
    normalized_customer = {field: str(customer.get(field) or "").strip() for field in IDENTITY_FIELDS}
    items = []
    seen_ids: set[str] = set()
    for row in request.get("items") or []:
        line_id = str(row.get("line_item_id") or "").strip()
        if line_id and line_id in seen_ids:
            raise AmendmentValidationError(f"Duplicate Woo line item ID: {line_id}")
        if line_id:
            seen_ids.add(line_id)
        item_code = str(row.get("item_code") or "").strip()
        qty = quantity(row.get("quantity"))
        if qty > 0 and not item_code:
            raise AmendmentValidationError("Every retained or added line requires an ERP item code")
        unit_rate = money(row.get("unit_rate"))
        if unit_rate < 0:
            raise AmendmentValidationError("Item rate cannot be negative")
        items.append(
            {
                "line_item_id": line_id,
                "item_code": item_code,
                "quantity": str(qty),
                "unit_rate": str(unit_rate),
                "tax_class": str(row.get("tax_class") or "").strip(),
            }
        )
    if not items or not any(quantity(row["quantity"]) > 0 for row in items):
        raise AmendmentValidationError("At least one order line must remain")
    confirmed_payment_amount = money(request.get("confirmed_payment_amount"))
    shipping_total = money(request.get("shipping_total"))
    discount_total = money(request.get("discount_total"))
    if confirmed_payment_amount < 0:
        raise AmendmentValidationError("Confirmed payment amount cannot be negative")
    if shipping_total < 0:
        raise AmendmentValidationError("Shipping cannot be negative")
    if discount_total < 0:
        raise AmendmentValidationError("Discount cannot be negative")
    return {
        "reason": reason,
        "proof_attachment": str(request.get("proof_attachment") or "").strip(),
        "order_date": order_date,
        "status": status,
        "payment_action": payment_action,
        "confirmed_payment_amount": str(confirmed_payment_amount),
        "shipping_total": str(shipping_total),
        "discount_total": str(discount_total),
        "customer": normalized_customer,
        "items": items,
        "material_change_confirmed": bool(request.get("material_change_confirmed")),
        "fulfilment_override_confirmed": bool(request.get("fulfilment_override_confirmed")),
        "reconciliation_review_confirmed": bool(request.get("reconciliation_review_confirmed")),
    }


def _allocate_discount(lines: list[dict[str, Any]], discount_total: Decimal) -> list[Decimal]:
    gross = [money(quantity(row["quantity"]) * money(row["unit_rate"])) for row in lines]
    gross_total = sum(gross, Decimal("0.00"))
    if discount_total < 0:
        raise AmendmentValidationError("Discount cannot be negative")
    if discount_total > gross_total:
        raise AmendmentValidationError("Discount cannot exceed the line subtotal")
    if not gross_total:
        return [Decimal("0.00") for _ in lines]
    allocations: list[Decimal] = []
    remaining = discount_total
    for index, amount in enumerate(gross):
        allocated = remaining if index == len(gross) - 1 else money(discount_total * amount / gross_total)
        allocations.append(allocated)
        remaining -= allocated
    return allocations


def build_woo_update(
    current_order: dict[str, Any],
    normalized_request: dict[str, Any],
    reverse_mappings: dict[str, dict[str, int]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_lines = {str(row.get("id") or ""): row for row in current_order.get("line_items") or []}
    active_lines = [row for row in normalized_request["items"] if quantity(row["quantity"]) > 0]
    requested_ids = {row["line_item_id"] for row in active_lines if row["line_item_id"]}
    if len(requested_ids) != len(active_lines) or requested_ids != set(current_lines):
        raise AmendmentValidationError(
            "Adding or removing Woo order lines is not supported in this release; amend quantities and rates on existing lines only"
        )
    discounts = _allocate_discount(active_lines, money(normalized_request["discount_total"]))
    payload_lines: list[dict[str, Any]] = []
    expected_items: list[dict[str, Any]] = []
    for row, allocated_discount in zip(active_lines, discounts):
        line_id = row["line_item_id"]
        mapping = reverse_mappings.get(row["item_code"])
        if not mapping:
            raise AmendmentValidationError(f"ERP item {row['item_code']} is not mapped to this Woo channel")
        qty = quantity(row["quantity"])
        subtotal = money(qty * money(row["unit_rate"]))
        total = money(subtotal - allocated_discount)
        existing = current_lines.get(line_id) if line_id else None
        same_product = existing and int(existing.get("product_id") or 0) == int(mapping["product_id"]) and int(existing.get("variation_id") or 0) == int(mapping.get("variation_id") or 0)
        item_payload: dict[str, Any] = {
            "quantity": float(qty),
            "subtotal": f"{subtotal:.2f}",
            "total": f"{total:.2f}",
            "tax_class": row["tax_class"],
        }
        if not same_product:
            raise AmendmentValidationError(
                f"Replacing Woo line {line_id} with another product is not supported in this release"
            )
        item_payload["id"] = int(line_id)
        payload_lines.append(item_payload)
        expected_items.append(
            {
                "source_line_item_id": line_id,
                "item_code": row["item_code"],
                "product_id": int(mapping["product_id"]),
                "variation_id": int(mapping.get("variation_id") or 0),
                "quantity": str(qty),
                "unit_rate": str(money(row["unit_rate"])),
                "line_subtotal": str(subtotal),
                "tax_class": row["tax_class"],
                "line_total": str(total),
            }
        )
    billing = dict(current_order.get("billing") or {})
    billing.update(normalized_request["customer"])
    payload: dict[str, Any] = {"line_items": payload_lines, "billing": billing}
    if normalized_request["status"]:
        payload["status"] = normalized_request["status"]
    if normalized_request["payment_action"] == "put_on_hold":
        payload["status"] = "on-hold"
    shipping_total = money(normalized_request["shipping_total"])
    shipping_lines = current_order.get("shipping_lines") or []
    if len(shipping_lines) > 1:
        raise AmendmentValidationError("Orders with multiple shipping lines require separate handling")
    if shipping_lines:
        payload["shipping_lines"] = [{"id": int(shipping_lines[0]["id"]), "total": f"{shipping_total:.2f}"}]
    elif shipping_total:
        payload["shipping_lines"] = [{"method_id": "flat_rate", "method_title": "Shipping", "total": f"{shipping_total:.2f}"}]
    expected_total = sum((money(row["line_total"]) for row in expected_items), Decimal("0.00")) + shipping_total
    expected = {
        "items": expected_items,
        "shipping_total": str(shipping_total),
        "discount_total": normalized_request["discount_total"],
        "expected_total_before_tax": str(expected_total),
        "currency": str(current_order.get("currency") or ""),
        "payment_method": str(current_order.get("payment_method") or ""),
        "date_paid": str(current_order.get("date_paid") or ""),
    }
    return payload, expected


def change_metrics(old_total: Any, new_total: Any) -> dict[str, Decimal]:
    old_value = money(old_total)
    new_value = money(new_total)
    delta = money(new_value - old_value)
    percent = Decimal("0.00") if old_value == 0 else money(abs(delta) * Decimal("100") / old_value)
    return {"old_total": old_value, "new_total": new_value, "delta": delta, "percent": percent}


def confirmation_token(before_hash: str, normalized_request: dict[str, Any], user: str) -> str:
    return payload_hash({"before_hash": before_hash, "request": normalized_request, "user": user})
