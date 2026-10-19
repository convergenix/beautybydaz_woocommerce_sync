from __future__ import annotations

import frappe

from .catalog import reconcile, sync_item_price, sync_item_stock, sync_new_item
from .config import get_channels, get_settings
from .orders import poll_channel


def poll_paid_orders():
    settings = get_settings()
    if not settings.enabled:
        return {"status": "disabled"}
    result = {}
    for channel in get_channels("order_sync_enabled"):
        try:
            result[channel.alias] = poll_channel(channel)
        except Exception as exc:
            frappe.log_error(title=f"BBD Woo order polling failed: {channel.alias}", message=frappe.get_traceback())
            result[channel.alias] = {"errors": [str(exc)]}
    return result


def reconcile_catalog():
    if not get_settings().enabled:
        return {"status": "disabled"}
    return reconcile()


def _enqueue(method, **kwargs):
    frappe.enqueue(method, queue="short", enqueue_after_commit=True, job_id="bbd-woo:" + method + ":" + ":".join(str(v) for v in kwargs.values()), **kwargs)


def on_item_price_change(doc, method=None):
    if get_settings().enabled and getattr(doc, "selling", 0) and frappe.db.exists("BBD Woo Channel", {"enabled": 1, "price_list": doc.price_list}):
        _enqueue("beautybydaz_woocommerce_sync.catalog.sync_item_price", item_code=doc.item_code)


def on_item_insert(doc, method=None):
    if get_settings().enabled:
        _enqueue("beautybydaz_woocommerce_sync.catalog.sync_new_item", item_code=doc.name)


def _affected_items(doc):
    return sorted({row.item_code for row in getattr(doc, "items", []) if getattr(row, "item_code", None)})


def on_stock_transaction(doc, method=None):
    if not get_settings().enabled:
        return
    for item_code in _affected_items(doc):
        _enqueue("beautybydaz_woocommerce_sync.catalog.sync_item_stock", item_code=item_code)


def on_stock_transaction_cancel(doc, method=None):
    on_stock_transaction(doc, method)
