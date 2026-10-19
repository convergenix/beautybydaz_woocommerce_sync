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


def test_audit_operations_match_doctype_select_options():
    root = Path(__file__).parents[1] / "beautybydaz_woocommerce_sync"
    combined = (root / "orders.py").read_text() + (root / "catalog.py").read_text()
    for obsolete in ("Order Import", "Refund Import", "Price Sync", "Stock Sync", "Draft Product Create"):
        assert f'create_log("{obsolete}"' not in combined
    for valid in ("Order", "Refund", "Price", "Stock", "New Item"):
        assert f'create_log("{valid}"' in combined


def test_paid_order_negative_stock_override_is_scoped_and_audit_is_durable():
    root = Path(__file__).parents[1] / "beautybydaz_woocommerce_sync"
    orders = (root / "orders.py").read_text()
    audit = (root / "audit.py").read_text()
    assert "allow_negative_stock_on_paid_orders" in orders
    assert "SellingController.update_stock_ledger(self, allow_negative_stock=True)" in orders
    assert audit.count("frappe.db.commit()") >= 2


def test_new_item_creation_defers_sku_until_after_draft_readback():
    source = (Path(__file__).parents[1] / "beautybydaz_woocommerce_sync" / "catalog.py").read_text()
    assert 'sku = create_payload.pop("sku")' in source
    assert 'client.create_product(create_payload)' in source
    assert 'client.update_product(readback["id"], 0, {"sku": sku})' in source


def test_reconciliation_rotates_in_bounded_long_queue_batches():
    root = Path(__file__).parents[1] / "beautybydaz_woocommerce_sync"
    catalog = (root / "catalog.py").read_text()
    jobs = (root / "jobs.py").read_text()
    assert "last_verification_time asc" in catalog
    assert "min(max(int(limit or settings.reconcile_limit or 50), 1), 100)" in catalog
    assert 'queue="long"' in jobs
    assert 'job_id="bbd-woo:catalog-reconcile"' in jobs


def test_refunds_map_using_woocommerce_refunded_item_metadata():
    orders = (Path(__file__).parents[1] / "beautybydaz_woocommerce_sync" / "orders.py").read_text()
    assert '"_refunded_item_id"' in orders
    assert "refund_lines[source_id or str(detail.get(\"id\"))]" in orders