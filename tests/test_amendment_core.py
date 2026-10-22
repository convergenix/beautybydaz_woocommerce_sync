from decimal import Decimal

import pytest

from beautybydaz_woocommerce_sync.amendment_core import (
    AmendmentValidationError,
    build_woo_update,
    change_metrics,
    confirmation_token,
    normalize_request,
    order_snapshot,
    payload_hash,
)


ORDER = {
    "id": 123,
    "number": "123",
    "status": "processing",
    "date_created": "2026-10-21T09:00:00",
    "date_paid": "2026-10-21T09:01:00",
    "currency": "NGN",
    "total": "3500.00",
    "discount_total": "0.00",
    "shipping_total": "500.00",
    "shipping_tax": "0.00",
    "payment_method": "paystack",
    "billing": {"first_name": "Test", "last_name": "Buyer", "email": "buyer@example.test"},
    "line_items": [
        {
            "id": 11,
            "product_id": 101,
            "variation_id": 0,
            "name": "Test Item",
            "quantity": 2,
            "subtotal": "3000.00",
            "subtotal_tax": "0.00",
            "total": "3000.00",
            "total_tax": "0.00",
            "tax_class": "",
        }
    ],
    "shipping_lines": [{"id": 91, "method_id": "flat_rate", "method_title": "Shipping", "total": "500.00", "total_tax": "0.00"}],
}


def request(**overrides):
    value = {
        "reason": "Manager approved correction",
        "status": "processing",
        "payment_action": "preserve",
        "confirmed_payment_amount": "3500",
        "shipping_total": "500",
        "discount_total": "0",
        "customer": {"first_name": "Test", "last_name": "Buyer", "email": "buyer@example.test"},
        "items": [{"line_item_id": "11", "item_code": "ITEM-1", "quantity": "2", "unit_rate": "1500", "tax_class": ""}],
        "material_change_confirmed": True,
        "fulfilment_override_confirmed": True,
        "reconciliation_review_confirmed": True,
    }
    value.update(overrides)
    return value


def test_snapshot_is_bounded_and_hashable():
    snapshot = order_snapshot(ORDER, {"11": "ITEM-1"})
    assert snapshot["payment_state"] == "paid"
    assert snapshot["items"][0]["unit_rate"] == "1500.00"
    assert "consumer_key" not in str(snapshot)
    assert len(payload_hash(snapshot)) == 64


def test_normalize_requires_specific_reason():
    with pytest.raises(AmendmentValidationError):
        normalize_request(request(reason="short"))


def test_normalize_rejects_order_date_amendment():
    with pytest.raises(AmendmentValidationError, match="does not safely support order-date amendments"):
        normalize_request(request(order_date="2026-10-21T09:00:00"))


def test_build_update_preserves_line_and_shipping():
    normalized = normalize_request(request())
    payload, expected = build_woo_update(ORDER, normalized, {"ITEM-1": {"product_id": 101, "variation_id": 0}})
    assert payload["line_items"] == [{"quantity": 2.0, "subtotal": "3000.00", "total": "3000.00", "tax_class": "", "id": 11}]
    assert payload["shipping_lines"] == [{"id": 91, "total": "500.00"}]
    assert expected["expected_total_before_tax"] == "3500.00"


def test_build_update_rejects_product_replacement():
    normalized = normalize_request(request(items=[{"line_item_id": "11", "item_code": "ITEM-2", "quantity": "1", "unit_rate": "2000", "tax_class": ""}]))
    with pytest.raises(AmendmentValidationError, match="Replacing Woo line"):
        build_woo_update(
            ORDER,
            normalized,
            {
                "ITEM-1": {"product_id": 101, "variation_id": 0},
                "ITEM-2": {"product_id": 202, "variation_id": 0},
            },
        )


def test_build_update_rejects_line_addition_or_removal():
    added = normalize_request(
        request(
            items=[
                {"line_item_id": "11", "item_code": "ITEM-1", "quantity": "2", "unit_rate": "1500", "tax_class": ""},
                {"line_item_id": "", "item_code": "ITEM-2", "quantity": "1", "unit_rate": "2000", "tax_class": ""},
            ]
        )
    )
    with pytest.raises(AmendmentValidationError, match="Adding or removing"):
        build_woo_update(
            ORDER,
            added,
            {
                "ITEM-1": {"product_id": 101, "variation_id": 0},
                "ITEM-2": {"product_id": 202, "variation_id": 0},
            },
        )

    with pytest.raises(AmendmentValidationError, match="At least one order line must remain"):
        normalize_request(
            request(items=[{"line_item_id": "11", "item_code": "ITEM-1", "quantity": "0", "unit_rate": "1500", "tax_class": ""}])
        )


def test_discount_is_allocated_to_line_total():
    normalized = normalize_request(request(discount_total="300"))
    payload, expected = build_woo_update(ORDER, normalized, {"ITEM-1": {"product_id": 101, "variation_id": 0}})
    assert payload["line_items"][0]["subtotal"] == "3000.00"
    assert payload["line_items"][0]["total"] == "2700.00"
    assert expected["expected_total_before_tax"] == "3200.00"


def test_change_metrics_and_confirmation_are_deterministic():
    metrics = change_metrics("3500", "3850")
    assert metrics == {"old_total": Decimal("3500.00"), "new_total": Decimal("3850.00"), "delta": Decimal("350.00"), "percent": Decimal("10.00")}
    normalized = normalize_request(request())
    assert confirmation_token("abc", normalized, "manager@example.test") == confirmation_token("abc", normalized, "manager@example.test")
