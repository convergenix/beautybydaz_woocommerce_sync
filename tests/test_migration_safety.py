from pathlib import Path


def test_schema_indexes_use_frappe_migration_helpers():
    source = (Path(__file__).parents[1] / "beautybydaz_woocommerce_sync" / "install.py").read_text()
    assert "frappe.db.add_unique" in source
    assert "frappe.db.add_index" in source
    assert "ALTER TABLE" not in source