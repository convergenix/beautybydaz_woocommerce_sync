from __future__ import annotations

import frappe


def get_settings():
    return frappe.get_single("BBD Woo Sync Settings")


def safe_statuses(settings=None) -> set[str]:
    settings = settings or get_settings()
    raw = settings.safe_paid_statuses or "processing,completed,on-hold"
    return {value.strip().lower() for value in raw.split(",") if value.strip()}


def get_channel(alias: str, require_enabled: bool = True):
    doc = frappe.get_doc("BBD Woo Channel", alias)
    if require_enabled and not doc.enabled:
        frappe.throw(f"WooCommerce channel {alias} is disabled")
    return doc


def get_channels(workflow_field: str | None = None):
    filters = {"enabled": 1}
    if workflow_field:
        filters[workflow_field] = 1
    names = frappe.get_all("BBD Woo Channel", filters=filters, pluck="name", order_by="name")
    return [frappe.get_doc("BBD Woo Channel", name) for name in names]
