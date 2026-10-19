from __future__ import annotations

from decimal import Decimal

import frappe
from frappe.utils import get_url, now_datetime

from .audit import create_log, finish_log
from .client import WooClient, WooReadbackError
from .core import decimal_value, new_product_payload, price_payload, stock_payload
from .mapping import get_item_mappings, upsert_mapping


def item_price(item_code, price_list):
    return frappe.db.get_value(
        "Item Price",
        {"item_code": item_code, "price_list": price_list, "selling": 1},
        "price_list_rate",
        order_by="valid_from desc, modified desc",
    )


def actual_qty(item_code, warehouse):
    return decimal_value(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty") or 0)


def _assert_price(readback, expected):
    got = decimal_value(readback.get("regular_price"))
    want = decimal_value(expected)
    if got != want:
        raise WooReadbackError(f"regular_price read-back mismatch: expected {want}, got {got}")


def _assert_stock(readback, expected_qty):
    got = decimal_value(readback.get("stock_quantity"))
    want = decimal_value(expected_qty)
    expected_status = "instock" if want > 0 else "outofstock"
    if got != want or readback.get("stock_status") != expected_status:
        raise WooReadbackError(f"stock read-back mismatch: expected {want}/{expected_status}, got {got}/{readback.get('stock_status')}")


def sync_price(mapping):
    channel = frappe.get_doc("BBD Woo Channel", mapping.channel)
    key = f"price:{channel.alias}:{mapping.erp_item}"
    with frappe.cache.lock(f"bbd-woo:{key}", timeout=180):
        log = create_log("Price", "ERP to Woo", channel.alias, mapping.erp_item, key, {"item": mapping.erp_item, "price_list": channel.price_list})
        try:
            rate = item_price(mapping.erp_item, channel.price_list)
            if rate is None:
                finish_log(log, "Failed", error=f"No Item Price for {mapping.erp_item} in {channel.price_list}")
                raise frappe.ValidationError(f"No Item Price for {mapping.erp_item} in {channel.price_list}")
            client = WooClient(channel)
            payload = price_payload(rate)
            readback = client.update_product(mapping.woo_product_id, mapping.variation_id or 0, payload)
            _assert_price(readback, rate)
            mapping.db_set("last_verification_time", now_datetime(), update_modified=False)
            result = {"regular_price": readback.get("regular_price"), "product_id": readback.get("id")}
            finish_log(log, "Success", result, "Item", mapping.erp_item)
            return result
        except Exception as exc:
            finish_log(log, "Failed", error=exc, traceback_text=frappe.get_traceback())
            raise


def sync_stock(mapping):
    channel = frappe.get_doc("BBD Woo Channel", mapping.channel)
    key = f"stock:{channel.alias}:{mapping.erp_item}"
    with frappe.cache.lock(f"bbd-woo:{key}", timeout=180):
        qty = actual_qty(mapping.erp_item, channel.warehouse)
        log = create_log("Stock", "ERP to Woo", channel.alias, mapping.erp_item, key, {"item": mapping.erp_item, "warehouse": channel.warehouse, "actual_qty": str(qty)})
        try:
            client = WooClient(channel)
            readback = client.update_product(mapping.woo_product_id, mapping.variation_id or 0, stock_payload(qty))
            _assert_stock(readback, qty)
            mapping.db_set("last_verification_time", now_datetime(), update_modified=False)
            result = {"stock_quantity": readback.get("stock_quantity"), "stock_status": readback.get("stock_status")}
            finish_log(log, "Success", result, "Item", mapping.erp_item)
            return result
        except Exception as exc:
            finish_log(log, "Failed", error=exc, traceback_text=frappe.get_traceback())
            raise


def _image_url(item):
    if not item.image:
        return None
    return item.image if item.image.startswith(("http://", "https://")) else get_url(item.image)


def create_draft_product(item_code, channel):
    existing = get_item_mappings(item_code, channel.alias)
    if existing:
        return {"status": "exists", "mapping": existing[0].name}
    item = frappe.get_doc("Item", item_code)
    if item.disabled or not item.is_sales_item or item.has_variants:
        return {"status": "skipped", "reason": "item is disabled, not for sale, or is a variant template"}
    warnings = []
    rate = item_price(item_code, channel.price_list)
    image = _image_url(item)
    if rate is None:
        warnings.append(f"Missing Item Price in {channel.price_list}")
    if not image:
        warnings.append("Missing item image")
    if not channel.default_category_id:
        warnings.append("Missing default Woo category mapping")
    qty = actual_qty(item_code, channel.warehouse)
    payload = new_product_payload(
        item_code=item.item_code,
        item_name=item.item_name,
        regular_price=rate,
        actual_qty=qty,
        image_url=image,
        category_id=channel.default_category_id,
    )
    key = f"new-item:{channel.alias}:{item_code}"
    with frappe.cache.lock(f"bbd-woo:{key}", timeout=300):
        log = create_log("New Item", "ERP to Woo", channel.alias, item_code, key, {"payload": payload, "warnings": warnings})
        try:
            readback = WooClient(channel).create_product(payload)
            if readback.get("status") != "draft":
                raise WooReadbackError(f"new product {readback.get('id')} is {readback.get('status')}, not draft")
            if str(readback.get("sku") or "") != item.item_code:
                raise WooReadbackError(f"new product SKU read-back mismatch: {readback.get('sku')}")
            mapping = upsert_mapping(channel.alias, item_code, readback["id"], 0, readback.get("sku") or item_code, 1, True)
            result = {"product_id": readback["id"], "status": readback["status"], "mapping": mapping.name, "warnings": warnings}
            finish_log(log, "Success", result, "Item", item_code)
            return result
        except Exception as exc:
            finish_log(log, "Failed", error=exc, traceback_text=frappe.get_traceback())
            raise


def sync_item_price(item_code, channel_alias=None):
    results = []
    for mapping in get_item_mappings(item_code, channel_alias):
        channel = frappe.get_doc("BBD Woo Channel", mapping.channel)
        if channel.enabled and channel.price_sync_enabled:
            results.append({"channel": channel.alias, **sync_price(mapping)})
    return results


def sync_item_stock(item_code, channel_alias=None):
    results = []
    for mapping in get_item_mappings(item_code, channel_alias):
        channel = frappe.get_doc("BBD Woo Channel", mapping.channel)
        if channel.enabled and channel.stock_sync_enabled:
            results.append({"channel": channel.alias, **sync_stock(mapping)})
    return results


def sync_new_item(item_code):
    from .config import get_channels

    return [{"channel": channel.alias, **create_draft_product(item_code, channel)} for channel in get_channels("new_item_sync_enabled")]


def verify_mapping(mapping):
    channel = frappe.get_doc("BBD Woo Channel", mapping.channel)
    product = WooClient(channel).get_product(mapping.woo_product_id, mapping.variation_id or 0)
    if str(product.get("sku") or "") != str(mapping.sku or ""):
        raise WooReadbackError(f"SKU mismatch for {mapping.name}: ERP mapping={mapping.sku}, Woo={product.get('sku')}")
    mapping.db_set("last_verification_time", now_datetime(), update_modified=False)
    return {"mapping": mapping.name, "product_id": product.get("id"), "sku": product.get("sku"), "verified": True}


def reconcile(limit=None):
    settings = frappe.get_single("BBD Woo Sync Settings")
    rows = frappe.get_all("BBD Woo Item Mapping", filters={"enabled": 1}, pluck="name", order_by="modified asc", limit=int(limit or settings.reconcile_limit or 500))
    result = {"price": 0, "stock": 0, "errors": []}
    for name in rows:
        mapping = frappe.get_doc("BBD Woo Item Mapping", name)
        channel = frappe.get_doc("BBD Woo Channel", mapping.channel)
        for operation, enabled, fn in (("price", channel.price_sync_enabled, sync_price), ("stock", channel.stock_sync_enabled, sync_stock)):
            if not channel.enabled or not enabled:
                continue
            try:
                fn(mapping)
                result[operation] += 1
            except Exception as exc:
                result["errors"].append({"mapping": name, "operation": operation, "error": str(exc)})
    return result
