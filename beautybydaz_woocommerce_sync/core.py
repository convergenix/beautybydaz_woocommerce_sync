from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

PAID_STATUSES = {"processing", "completed", "on-hold"}
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def decimal_value(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def is_paid_order(order: dict[str, Any], statuses: set[str] | None = None) -> bool:
    allowed = statuses or PAID_STATUSES
    return bool(order.get("date_paid")) and str(order.get("status") or "").lower() in allowed


def product_endpoint(product_id: int | str, variation_id: int | str | None = 0) -> str:
    variation = int(variation_id or 0)
    base = f"products/{int(product_id)}"
    return f"{base}/variations/{variation}" if variation else base


def price_payload(rate: Any) -> dict[str, str]:
    value = decimal_value(rate)
    normalized = format(value.quantize(Decimal("0.01")), "f").rstrip("0").rstrip(".")
    return {"regular_price": normalized or "0"}


def stock_payload(actual_qty: Any) -> dict[str, Any]:
    qty = decimal_value(actual_qty)
    numeric = int(qty) if qty == qty.to_integral() else float(qty)
    return {
        "manage_stock": True,
        "stock_quantity": numeric,
        "stock_status": "instock" if qty > 0 else "outofstock",
    }


def new_product_payload(
    *,
    item_code: str,
    item_name: str,
    regular_price: Any | None,
    actual_qty: Any,
    image_url: str | None = None,
    category_id: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": item_name,
        "sku": item_code,
        "type": "simple",
        "status": "draft",
        **stock_payload(actual_qty),
    }
    if regular_price is not None:
        payload.update(price_payload(regular_price))
    if image_url:
        payload["images"] = [{"src": image_url}]
    if category_id:
        payload["categories"] = [{"id": int(category_id)}]
    return payload


def retry_delays(max_retries: int, base_seconds: float = 1.0) -> list[float]:
    return [base_seconds * (2**attempt) for attempt in range(max(0, int(max_retries)))]


def mapping_key(channel: str, product_id: Any, variation_id: Any = 0) -> tuple[str, int, int]:
    return channel, int(product_id), int(variation_id or 0)


def order_idempotency_key(channel: str, order_id: Any) -> str:
    return f"order:{channel}:{int(order_id)}"


def refund_idempotency_key(channel: str, refund_id: Any) -> str:
    return f"refund:{channel}:{int(refund_id)}"


def verify_regular_price(readback: dict[str, Any], expected: Any) -> None:
    got = decimal_value(readback.get("regular_price"))
    want = decimal_value(expected)
    if got != want:
        raise ValueError(f"regular_price read-back mismatch: expected {want}, got {got}")


def verify_stock_readback(readback: dict[str, Any], expected_qty: Any) -> None:
    got = decimal_value(readback.get("stock_quantity"))
    want = decimal_value(expected_qty)
    status = "instock" if want > 0 else "outofstock"
    if got != want or readback.get("stock_status") != status:
        raise ValueError(
            f"stock read-back mismatch: expected {want}/{status}, got {got}/{readback.get('stock_status')}"
        )


def refund_line_quantities(refund: dict[str, Any]) -> dict[str, Decimal]:
    return {
        str(row.get("id")): abs(decimal_value(row.get("quantity")))
        for row in refund.get("line_items") or []
        if row.get("id") is not None
    }


def safe_summary(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("secret", "password", "token", "authorization", "consumer_key")):
                out[key] = "[REDACTED]"
            elif lowered in {"billing", "shipping"} and isinstance(item, dict):
                out[key] = {
                    "first_name": item.get("first_name"),
                    "last_name": item.get("last_name"),
                    "company": item.get("company"),
                    "email_present": bool(item.get("email")),
                    "phone_present": bool(item.get("phone")),
                    "address_present": bool(item.get("address_1") or item.get("city")),
                }
            else:
                out[key] = safe_summary(item, depth + 1)
        return out
    if isinstance(value, list):
        return [safe_summary(item, depth + 1) for item in value[:100]]
    if isinstance(value, str) and len(value) > 1000:
        return value[:1000] + "…"
    return value


def payment_entries_are_enabled() -> bool:
    """Hard safety boundary: this integration never creates Payment Entries."""
    return False
