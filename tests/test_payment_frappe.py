from types import SimpleNamespace

import pytest

frappe = pytest.importorskip("frappe")

from beautybydaz_woocommerce_sync import orders


class FakeInvoice:
    name = "ACC-SINV-2026-00045"
    docstatus = 1
    custom_woo_channel = "beautybydaz"
    custom_woocommerce_order_id = "158359"
    outstanding_amount = 0
    currency = "NGN"

    def reload(self):
        return self


def test_existing_payment_entry_reference_is_detected(monkeypatch):
    monkeypatch.setattr(
        orders.frappe.db,
        "sql",
        lambda *args, **kwargs: [SimpleNamespace(name="ACC-PAY-2026-00001")],
    )
    assert orders._existing_payment_entry("ACC-SINV-2026-00045") == "ACC-PAY-2026-00001"


def test_duplicate_retry_reuses_valid_submitted_payment_entry(monkeypatch):
    invoice = FakeInvoice()
    channel = SimpleNamespace(alias="beautybydaz", company="BeautybyDaz")
    order = {
        "id": 158359,
        "number": "158359",
        "date_paid": "2026-10-20T10:19:21",
        "currency": "NGN",
        "total": "92350.00",
        "payment_method": "paystack",
        "payment_method_title": "Paystack Checkout",
    }
    pe = SimpleNamespace(
        name="ACC-PAY-2026-00001",
        docstatus=1,
        mode_of_payment="Paystack",
        paid_to="1121 - Paystack Clearing - BBD",
        references=[
            SimpleNamespace(
                reference_doctype="Sales Invoice",
                reference_name=invoice.name,
                allocated_amount=92350,
            )
        ],
    )

    monkeypatch.setattr(orders, "_validate_payment_master_data", lambda *args: None)
    monkeypatch.setattr(orders, "_existing_payment_entry", lambda *args: pe.name)
    monkeypatch.setattr(orders.frappe, "get_doc", lambda doctype, name: pe)

    returned, route, amount, status = orders._create_payment_entry(channel, order, invoice)

    assert returned is pe
    assert route["gateway"] == "Paystack"
    assert amount == orders.decimal_value("92350.00")
    assert status == "existing"
