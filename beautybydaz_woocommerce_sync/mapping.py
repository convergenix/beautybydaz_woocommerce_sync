from __future__ import annotations

import csv
from pathlib import Path

import frappe
from frappe.utils import now_datetime


def get_mapping(channel: str, product_id: int, variation_id: int = 0, required: bool = True):
    name = frappe.db.get_value(
        "BBD Woo Item Mapping",
        {"channel": channel, "woo_product_id": int(product_id), "variation_id": int(variation_id or 0), "enabled": 1},
        "name",
    )
    if not name and required:
        frappe.throw(f"No enabled mapping for channel={channel}, product={product_id}, variation={variation_id or 0}")
    return frappe.get_doc("BBD Woo Item Mapping", name) if name else None


def get_item_mappings(item_code: str, channel: str | None = None):
    filters = {"erp_item": item_code, "enabled": 1}
    if channel:
        filters["channel"] = channel
    names = frappe.get_all("BBD Woo Item Mapping", filters=filters, pluck="name", order_by="channel")
    return [frappe.get_doc("BBD Woo Item Mapping", name) for name in names]


def upsert_mapping(channel, erp_item, product_id, variation_id=0, sku=None, enabled=1, verified=True):
    name = frappe.db.get_value("BBD Woo Item Mapping", {"channel": channel, "erp_item": erp_item}, "name")
    doc = frappe.get_doc("BBD Woo Item Mapping", name) if name else frappe.new_doc("BBD Woo Item Mapping")
    doc.update(
        {
            "channel": channel,
            "erp_item": erp_item,
            "woo_product_id": int(product_id),
            "variation_id": int(variation_id or 0),
            "sku": sku or erp_item,
            "enabled": int(bool(enabled)),
            "last_verification_time": now_datetime() if verified else doc.last_verification_time,
        }
    )
    doc.save(ignore_permissions=True) if name else doc.insert(ignore_permissions=True)
    return doc


def import_mapping_csv(path: str):
    source = Path(path).resolve()
    if not source.is_file():
        frappe.throw(f"Mapping CSV not found: {source}")
    result = {"created": 0, "updated": 0, "skipped": 0, "errors": []}
    with source.open(newline="", encoding="utf-8-sig") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), 2):
            try:
                if not int(row.get("enabled") or 0) or not row.get("erp_item"):
                    result["skipped"] += 1
                    continue
                existed = bool(frappe.db.get_value("BBD Woo Item Mapping", {"channel": row["channel"], "erp_item": row["erp_item"]}, "name"))
                upsert_mapping(
                    row["channel"], row["erp_item"], row["woo_product_id"], row.get("variation_id") or 0, row.get("woo_sku") or row["erp_item"], 1, False
                )
                result["updated" if existed else "created"] += 1
            except Exception as exc:
                result["errors"].append({"line": line_number, "error": str(exc)})
    if result["errors"]:
        frappe.throw(f"Mapping import failed for {len(result['errors'])} rows: {result['errors'][:10]}")
    return result
