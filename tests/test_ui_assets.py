from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "beautybydaz_woocommerce_sync"


def test_sales_order_and_amendment_form_scripts_are_registered():
    hooks = runpy.run_path(str(PACKAGE / "hooks.py"))
    assert hooks["doctype_js"] == {
        "Sales Order": "public/js/sales_order.js",
        "BBD Order Amendment": "public/js/bbd_order_amendment.js",
    }
    for relative_path in hooks["doctype_js"].values():
        assert (PACKAGE / relative_path).is_file()


def test_sales_order_button_uses_secure_server_entrypoint():
    script = (PACKAGE / "public/js/sales_order.js").read_text(encoding="utf-8")
    assert '__("Amend Website Order")' in script
    assert "beautybydaz_woocommerce_sync.amendments.start_sales_order_amendment" in script
    assert 'frappe.set_route("bbd-order-amendment")' in script


def test_amendment_workspace_supports_review_and_rollback_actions():
    page = (
        PACKAGE
        / "beauty_by_daz_woocommerce_sync/page/bbd_order_amendment/bbd_order_amendment.js"
    ).read_text(encoding="utf-8")
    form = (PACKAGE / "public/js/bbd_order_amendment.js").read_text(encoding="utf-8")
    assert 'frappe.pages["bbd-order-amendment"].on_page_show' in page
    assert "get_pending_amendment" in page
    assert "chain_fingerprint" in page
    assert "Continue Amendment" in form
    assert "rollback_order_amendment" in form
