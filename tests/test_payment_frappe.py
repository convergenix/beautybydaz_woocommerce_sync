import unittest
from types import SimpleNamespace
from unittest import mock

try:
    import frappe
except ImportError:  # pragma: no cover - exercised in the Frappe bench test run
    frappe = None

if frappe:
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


@unittest.skipIf(frappe is None, "requires Frappe bench context")
class PaymentFrappeTests(unittest.TestCase):
    def test_existing_payment_entry_reference_is_detected(self):
        with mock.patch.object(
            orders.frappe.db,
            "sql",
            return_value=[SimpleNamespace(name="ACC-PAY-2026-00001")],
        ):
            self.assertEqual(
                orders._existing_payment_entry("ACC-SINV-2026-00045"),
                "ACC-PAY-2026-00001",
            )

    def test_duplicate_retry_reuses_valid_submitted_payment_entry(self):
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

        with (
            mock.patch.object(orders, "_validate_payment_master_data"),
            mock.patch.object(orders, "_existing_payment_entry", return_value=pe.name),
            mock.patch.object(orders.frappe, "get_doc", return_value=pe),
        ):
            returned, route, amount, status = orders._create_payment_entry(channel, order, invoice)

        self.assertIs(returned, pe)
        self.assertEqual(route["gateway"], "Paystack")
        self.assertEqual(amount, orders.decimal_value("92350.00"))
        self.assertEqual(status, "existing")


if __name__ == "__main__":
    unittest.main()
