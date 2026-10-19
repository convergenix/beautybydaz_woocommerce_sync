from pathlib import Path


def test_schema_indexes_use_frappe_migration_helpers():
    source = (Path(__file__).parents[1] / "beautybydaz_woocommerce_sync" / "install.py").read_text()
    assert "frappe.db.add_unique" in source
    assert "frappe.db.add_index" in source
    assert "ALTER TABLE" not in source
    assert '_add_unique("tabSales Order", "uniq_bbd_woo_order"' in source
    assert '_add_index("tabDelivery Note", "idx_bbd_woo_dn_order"' in source
    assert '_add_index("tabSales Invoice", "idx_bbd_woo_si_order"' in source


def test_channel_configuration_checks_live_product_api_not_system_status():
    source = (Path(__file__).parents[1] / "beautybydaz_woocommerce_sync" / "api.py").read_text()
    assert 'request("GET", "products", params={"per_page": 1})' in source
    assert 'request("GET", "system_status")' not in source