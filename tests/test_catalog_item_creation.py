import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

try:
    import frappe
except ImportError:  # pragma: no cover - exercised in the Frappe bench test run
    frappe = None

if frappe:
    from beautybydaz_woocommerce_sync import catalog


def item(**overrides):
    values = {
        "name": "ITEM-1",
        "item_code": "ITEM-1",
        "item_name": "Item 1",
        "disabled": 0,
        "is_sales_item": 1,
        "has_variants": 0,
        "variant_of": "",
        "image": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def channel():
    return SimpleNamespace(
        alias="retail",
        price_list="Standard Selling",
        warehouse="Retail - BBD",
        default_category_id=None,
    )


@unittest.skipIf(frappe is None, "requires Frappe bench context")
class CatalogItemCreationTests(unittest.TestCase):
    def test_variant_template_is_not_created_as_simple_product(self):
        with (
            mock.patch.object(catalog, "get_item_mappings", return_value=[]),
            mock.patch.object(catalog.frappe, "get_doc", return_value=item(has_variants=1)),
            mock.patch.object(catalog, "WooClient") as woo_client,
        ):
            result = catalog.create_draft_product("ITEM-1", channel())

        self.assertEqual(result, {"status": "skipped", "reason": "item is a variant template"})
        woo_client.assert_not_called()

    def test_variant_child_is_not_created_as_simple_product(self):
        with (
            mock.patch.object(catalog, "get_item_mappings", return_value=[]),
            mock.patch.object(catalog.frappe, "get_doc", return_value=item(variant_of="ITEM-TEMPLATE")),
            mock.patch.object(catalog, "WooClient") as woo_client,
        ):
            result = catalog.create_draft_product("ITEM-1", channel())

        self.assertEqual(result, {"status": "skipped", "reason": "item is a variant child"})
        woo_client.assert_not_called()

    def test_normal_simple_item_still_creates_draft_product(self):
        mapping = SimpleNamespace(name="retail-ITEM-1")
        client = mock.Mock()
        client.create_product.return_value = {"id": 321, "status": "draft"}
        client.update_product.return_value = {"id": 321, "status": "draft", "sku": "ITEM-1"}

        with (
            mock.patch.object(catalog, "get_item_mappings", return_value=[]),
            mock.patch.object(catalog.frappe, "get_doc", return_value=item()),
            mock.patch.object(catalog, "item_price", return_value=1000),
            mock.patch.object(catalog, "actual_qty", return_value=2),
            mock.patch.object(
                catalog.frappe,
                "cache",
                SimpleNamespace(lock=mock.Mock(return_value=nullcontext())),
            ),
            mock.patch.object(catalog, "create_log", return_value=SimpleNamespace(name="LOG-1")),
            mock.patch.object(catalog, "finish_log"),
            mock.patch.object(catalog, "WooClient", return_value=client),
            mock.patch.object(catalog, "upsert_mapping", return_value=mapping),
        ):
            result = catalog.create_draft_product("ITEM-1", channel())

        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["product_id"], 321)
        self.assertEqual(result["mapping"], mapping.name)
        create_payload = client.create_product.call_args.args[0]
        self.assertNotIn("sku", create_payload)
        self.assertEqual(create_payload["type"], "simple")
        client.update_product.assert_called_once_with(321, 0, {"sku": "ITEM-1"})


if __name__ == "__main__":
    unittest.main()