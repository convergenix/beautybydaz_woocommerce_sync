from decimal import Decimal

import pytest

from beautybydaz_woocommerce_sync.core import (
    is_paid_order,
    mapping_key,
    new_product_payload,
    order_idempotency_key,
    payment_entries_are_enabled,
    price_payload,
    product_endpoint,
    refund_idempotency_key,
    refund_line_quantities,
    retry_delays,
    safe_summary,
    stock_payload,
    verify_regular_price,
    verify_stock_readback,
)


def test_paid_order_requires_date_paid_and_safe_status():
    assert is_paid_order({"status": "processing", "date_paid": "2026-10-19T10:00:00"})
    assert not is_paid_order({"status": "pending", "date_paid": "2026-10-19T10:00:00"})
    assert not is_paid_order({"status": "processing", "date_paid": None})


def test_channels_and_orders_get_distinct_idempotency_keys():
    assert order_idempotency_key("beautybydaz", 158354) == "order:beautybydaz:158354"
    assert order_idempotency_key("beautybydaz_wholesale_ngn", 158354) != order_idempotency_key("beautybydaz", 158354)
    assert refund_idempotency_key("beautybydaz", 77) == "refund:beautybydaz:77"


def test_variation_endpoint_is_deterministic():
    assert product_endpoint(56088, 0) == "products/56088"
    assert product_endpoint(56088, 123) == "products/56088/variations/123"
    assert mapping_key("retail", 56088, 123) == ("retail", 56088, 123)


def test_price_payload_updates_regular_price_only():
    assert price_payload("40500.00") == {"regular_price": "40500"}


def test_channel_stock_payloads_are_independent():
    retail = stock_payload(4)
    wholesale = stock_payload(0)
    assert retail == {"manage_stock": True, "stock_quantity": 4, "stock_status": "instock"}
    assert wholesale == {"manage_stock": True, "stock_quantity": 0, "stock_status": "outofstock"}


def test_new_product_is_always_draft():
    payload = new_product_payload(item_code="TEST-1", item_name="Test", regular_price=1000, actual_qty=2)
    assert payload["status"] == "draft"
    assert payload["sku"] == "TEST-1"


def test_readback_failures_are_detected():
    verify_regular_price({"regular_price": "1000.00"}, 1000)
    verify_stock_readback({"stock_quantity": 2, "stock_status": "instock"}, 2)
    with pytest.raises(ValueError, match="read-back mismatch"):
        verify_regular_price({"regular_price": "999"}, 1000)
    with pytest.raises(ValueError, match="read-back mismatch"):
        verify_stock_readback({"stock_quantity": 1, "stock_status": "instock"}, 2)


def test_refund_quantities_and_retries():
    refund = {"line_items": [{"id": 11, "quantity": -2}, {"id": 12, "quantity": 1}]}
    assert refund_line_quantities(refund) == {"11": Decimal("2"), "12": Decimal("1")}
    assert retry_delays(4, 0.5) == [0.5, 1.0, 2.0, 4.0]


def test_sensitive_values_are_redacted_and_payment_entry_is_disabled():
    value = safe_summary({"consumer_key": "secret", "nested": {"password": "secret"}})
    assert value["consumer_key"] == "[REDACTED]"
    assert value["nested"]["password"] == "[REDACTED]"
    assert payment_entries_are_enabled() is False
