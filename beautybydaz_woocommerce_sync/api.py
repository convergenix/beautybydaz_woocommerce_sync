from __future__ import annotations

import json
from pathlib import Path

import frappe

from .catalog import reconcile, sync_item_price, sync_item_stock, sync_new_item, verify_mapping
from .client import WooClient
from .config import get_channel
from .mapping import import_mapping_csv as _import_mapping_csv
from .mapping import upsert_mapping
from .orders import import_paid_order, poll_channel

ROUTING = {
    "beautybydaz": {
        "company": "BeautybyDaz",
        "warehouse": "Retail NG Warehouse - BBD",
        "price_list": "Retail Pricelist",
        "customer_group": "Retail",
        "income_account": "4130 - Retail Income - BBD",
        "shipping_income_account": "4130 - Retail Income - BBD",
        "cost_center": "Main - BBD",
        "order_floor": 158334,
        "allow_negative_stock_on_paid_orders": 1,
    },
    "beautybydaz_wholesale_ngn": {
        "company": "BeautybyDaz",
        "warehouse": "Wholesale NG Warehouse - BBD",
        "price_list": "BBD Wholesale NGN",
        "customer_group": "Wholesale Customer",
        "income_account": "4140 - Wholesale Income - BBD",
        "shipping_income_account": "4140 - Wholesale Income - BBD",
        "cost_center": "Main - BBD",
        "order_floor": 62513,
        "allow_negative_stock_on_paid_orders": 1,
    },
}


def _system_manager():
    if frappe.session.user not in ("Administrator", "Guest"):
        frappe.only_for("System Manager")


@frappe.whitelist()
def configure_from_file(path: str, enable: int = 0):
    _system_manager()
    source = Path(path).resolve()
    data = json.loads(source.read_text(encoding="utf-8"))
    result = {}
    for alias, secrets in data.items():
        if alias not in ROUTING:
            frappe.throw(f"Unsupported channel alias {alias}")
        values = {**ROUTING[alias], **secrets}
        required = ["base_url", "consumer_key", "consumer_secret"]
        missing = [key for key in required if not values.get(key)]
        if missing:
            frappe.throw(f"Channel {alias} is missing {', '.join(missing)}")
        for doctype, value in (
            ("Company", values["company"]),
            ("Warehouse", values["warehouse"]),
            ("Price List", values["price_list"]),
            ("Customer Group", values["customer_group"]),
            ("Account", values["income_account"]),
            ("Cost Center", values["cost_center"]),
        ):
            if not frappe.db.exists(doctype, value):
                frappe.throw(f"Required {doctype} does not exist: {value}")
        doc = frappe.get_doc("BBD Woo Channel", alias) if frappe.db.exists("BBD Woo Channel", alias) else frappe.new_doc("BBD Woo Channel")
        doc.alias = alias
        doc.update({key: value for key, value in values.items() if key not in {"consumer_key", "consumer_secret"}})
        doc.consumer_key = values["consumer_key"]
        doc.consumer_secret = values["consumer_secret"]
        doc.enabled = int(bool(int(enable)))
        doc.order_sync_enabled = 1
        doc.refund_sync_enabled = 1
        doc.price_sync_enabled = 1
        doc.stock_sync_enabled = 1
        doc.new_item_sync_enabled = 1
        doc.timeout_seconds = 45
        doc.max_retries = 3
        doc.save(ignore_permissions=True) if doc.name else doc.insert(ignore_permissions=True)
        # Some production Woo installations return HTTP 500 from system_status
        # even while the authenticated product/order API is healthy. Validate the
        # exact API surface this app uses instead.
        response = WooClient(doc).request("GET", "products", params={"per_page": 1})
        result[alias] = {"configured": True, "enabled": bool(doc.enabled), "api_reachable": isinstance(response, list)}
    return result


@frappe.whitelist()
def enable_sync():
    _system_manager()
    for name in frappe.get_all("BBD Woo Channel", pluck="name"):
        frappe.db.set_value("BBD Woo Channel", name, "enabled", 1, update_modified=False)
    settings = frappe.get_single("BBD Woo Sync Settings")
    settings.enabled = 1
    settings.payment_entries_enabled = 0
    settings.save(ignore_permissions=True)
    return health_check()


@frappe.whitelist()
def disable_sync():
    _system_manager()
    settings = frappe.get_single("BBD Woo Sync Settings")
    settings.enabled = 0
    settings.save(ignore_permissions=True)
    return {"enabled": False}


@frappe.whitelist()
def import_mapping_csv(path: str):
    _system_manager()
    return _import_mapping_csv(path)


@frappe.whitelist()
def import_order(channel_alias: str, order_id: int):
    _system_manager()
    channel = get_channel(channel_alias)
    order = WooClient(channel).get_order(int(order_id))
    return import_paid_order(channel, order)


@frappe.whitelist()
def poll(channel_alias: str | None = None):
    _system_manager()
    if channel_alias:
        return {channel_alias: poll_channel(get_channel(channel_alias))}
    return {name: poll_channel(get_channel(name)) for name in frappe.get_all("BBD Woo Channel", filters={"enabled": 1, "order_sync_enabled": 1}, pluck="name")}


@frappe.whitelist()
def sync_price(item_code: str, channel_alias: str | None = None):
    _system_manager()
    return sync_item_price(item_code, channel_alias)


@frappe.whitelist()
def sync_stock(item_code: str, channel_alias: str | None = None):
    _system_manager()
    return sync_item_stock(item_code, channel_alias)


@frappe.whitelist()
def create_draft_products(item_code: str):
    _system_manager()
    return sync_new_item(item_code)


@frappe.whitelist()
def reconcile_now(limit: int | None = None):
    _system_manager()
    return reconcile(limit)


@frappe.whitelist()
def seed_known_mapping():
    _system_manager()
    result = []
    if not frappe.db.exists("Item", "56088"):
        frappe.throw("Required ERP Item 56088 does not exist")
    result.append(upsert_mapping("beautybydaz", "56088", 56088, 0, "122-1-byd", 1, False).name)
    result.append(upsert_mapping("beautybydaz_wholesale_ngn", "56088", 59803, 0, "122-1-byd", 1, False).name)
    return result


@frappe.whitelist()
def verify_known_mapping():
    _system_manager()
    names = frappe.get_all("BBD Woo Item Mapping", filters={"erp_item": "56088", "enabled": 1}, pluck="name")
    return [verify_mapping(frappe.get_doc("BBD Woo Item Mapping", name)) for name in names]


@frappe.whitelist(allow_guest=True)
def health_check():
    settings = frappe.get_single("BBD Woo Sync Settings")
    channels = frappe.get_all(
        "BBD Woo Channel",
        fields=["name", "alias", "enabled", "warehouse", "price_list", "order_sync_enabled", "price_sync_enabled", "stock_sync_enabled", "new_item_sync_enabled", "last_poll_at"],
        order_by="name",
    )
    return {
        "enabled": bool(settings.enabled),
        "payment_entries_enabled": False,
        "channels": channels,
        "mapping_count": frappe.db.count("BBD Woo Item Mapping", {"enabled": 1}),
        "failed_logs": frappe.db.count("BBD Woo Sync Log", {"status": "Failed"}),
    }
