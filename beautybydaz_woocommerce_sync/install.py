from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
    "Customer": [
        {"fieldname": "custom_woocommerce_customer_id", "label": "WooCommerce Customer ID", "fieldtype": "Data", "insert_after": "customer_name"},
        {"fieldname": "custom_woocommerce_email", "label": "WooCommerce Email", "fieldtype": "Data", "insert_after": "custom_woocommerce_customer_id"},
        {"fieldname": "custom_woo_channel", "label": "Woo Channel", "fieldtype": "Data", "insert_after": "custom_woocommerce_email"},
        {"fieldname": "custom_woo_site", "label": "Woo Site", "fieldtype": "Data", "insert_after": "custom_woo_channel"},
    ],
    "Contact": [{"fieldname": "custom_woocommerce_customer_id", "label": "WooCommerce Customer ID", "fieldtype": "Data", "insert_after": "user"}],
    "Address": [{"fieldname": "custom_woocommerce_customer_id", "label": "WooCommerce Customer ID", "fieldtype": "Data", "insert_after": "address_title"}],
    "Item": [
        {"fieldname": "custom_woocommerce_product_id", "label": "WooCommerce Product ID", "fieldtype": "Data", "insert_after": "item_code"},
        {"fieldname": "custom_woocommerce_variation_id", "label": "WooCommerce Variation ID", "fieldtype": "Data", "insert_after": "custom_woocommerce_product_id"},
        {"fieldname": "custom_woocommerce_permalink", "label": "WooCommerce Permalink", "fieldtype": "Small Text", "insert_after": "custom_woocommerce_variation_id"},
    ],
    "Sales Order": [
        {"fieldname": "custom_woocommerce_order_id", "label": "WooCommerce Order ID", "fieldtype": "Data", "insert_after": "po_no", "read_only": 1},
        {"fieldname": "custom_woocommerce_order_root_id", "label": "WooCommerce Root Order ID", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_id", "read_only": 1},
        {"fieldname": "custom_bbd_order_amendment", "label": "BBD Order Amendment", "fieldtype": "Link", "options": "BBD Order Amendment", "insert_after": "custom_woocommerce_order_root_id", "read_only": 1},
        {"fieldname": "custom_woo_customer_name", "label": "Woo Customer Name", "fieldtype": "Data", "insert_after": "custom_bbd_order_amendment", "read_only": 1},
        {"fieldname": "custom_woo_customer_email", "label": "Woo Customer Email", "fieldtype": "Data", "insert_after": "custom_woo_customer_name", "read_only": 1},
        {"fieldname": "custom_woo_customer_phone", "label": "Woo Customer Phone", "fieldtype": "Data", "insert_after": "custom_woo_customer_email", "read_only": 1},
        {"fieldname": "custom_woocommerce_order_number", "label": "WooCommerce Order Number", "fieldtype": "Data", "insert_after": "custom_woo_customer_phone", "read_only": 1},
        {"fieldname": "custom_woo_channel", "label": "Woo Channel", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_number", "read_only": 1},
        {"fieldname": "custom_woo_site", "label": "Woo Site", "fieldtype": "Data", "insert_after": "custom_woo_channel", "read_only": 1},
    ],
    "Delivery Note": [
        {"fieldname": "custom_woocommerce_order_id", "label": "WooCommerce Order ID", "fieldtype": "Data", "insert_after": "po_no", "read_only": 1},
        {"fieldname": "custom_woocommerce_order_root_id", "label": "WooCommerce Root Order ID", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_id", "read_only": 1},
        {"fieldname": "custom_bbd_order_amendment", "label": "BBD Order Amendment", "fieldtype": "Link", "options": "BBD Order Amendment", "insert_after": "custom_woocommerce_order_root_id", "read_only": 1},
        {"fieldname": "custom_woo_customer_name", "label": "Woo Customer Name", "fieldtype": "Data", "insert_after": "custom_bbd_order_amendment", "read_only": 1},
        {"fieldname": "custom_woo_customer_email", "label": "Woo Customer Email", "fieldtype": "Data", "insert_after": "custom_woo_customer_name", "read_only": 1},
        {"fieldname": "custom_woo_customer_phone", "label": "Woo Customer Phone", "fieldtype": "Data", "insert_after": "custom_woo_customer_email", "read_only": 1},
        {"fieldname": "custom_woocommerce_order_number", "label": "WooCommerce Order Number", "fieldtype": "Data", "insert_after": "custom_woo_customer_phone", "read_only": 1},
        {"fieldname": "custom_woocommerce_refund_id", "label": "WooCommerce Refund ID", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_number", "read_only": 1},
        {"fieldname": "custom_woo_channel", "label": "Woo Channel", "fieldtype": "Data", "insert_after": "custom_woocommerce_refund_id", "read_only": 1},
        {"fieldname": "custom_woo_site", "label": "Woo Site", "fieldtype": "Data", "insert_after": "custom_woo_channel", "read_only": 1},
    ],
    "Sales Invoice": [
        {"fieldname": "custom_woocommerce_order_id", "label": "WooCommerce Order ID", "fieldtype": "Data", "insert_after": "po_no", "read_only": 1},
        {"fieldname": "custom_woocommerce_order_root_id", "label": "WooCommerce Root Order ID", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_id", "read_only": 1},
        {"fieldname": "custom_bbd_order_amendment", "label": "BBD Order Amendment", "fieldtype": "Link", "options": "BBD Order Amendment", "insert_after": "custom_woocommerce_order_root_id", "read_only": 1},
        {"fieldname": "custom_woo_customer_name", "label": "Woo Customer Name", "fieldtype": "Data", "insert_after": "custom_bbd_order_amendment", "read_only": 1},
        {"fieldname": "custom_woo_customer_email", "label": "Woo Customer Email", "fieldtype": "Data", "insert_after": "custom_woo_customer_name", "read_only": 1},
        {"fieldname": "custom_woo_customer_phone", "label": "Woo Customer Phone", "fieldtype": "Data", "insert_after": "custom_woo_customer_email", "read_only": 1},
        {"fieldname": "custom_woocommerce_order_number", "label": "WooCommerce Order Number", "fieldtype": "Data", "insert_after": "custom_woo_customer_phone", "read_only": 1},
        {"fieldname": "custom_woocommerce_refund_id", "label": "WooCommerce Refund ID", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_number", "read_only": 1},
        {"fieldname": "custom_woo_channel", "label": "Woo Channel", "fieldtype": "Data", "insert_after": "custom_woocommerce_refund_id", "read_only": 1},
        {"fieldname": "custom_woo_site", "label": "Woo Site", "fieldtype": "Data", "insert_after": "custom_woo_channel", "read_only": 1},
    ],
    "Payment Entry": [
        {"fieldname": "custom_woocommerce_order_root_id", "label": "WooCommerce Root Order ID", "fieldtype": "Data", "insert_after": "reference_date", "read_only": 1},
        {"fieldname": "custom_bbd_order_amendment", "label": "BBD Order Amendment", "fieldtype": "Link", "options": "BBD Order Amendment", "insert_after": "custom_woocommerce_order_root_id", "read_only": 1},
        {"fieldname": "custom_woo_channel", "label": "Woo Channel", "fieldtype": "Data", "insert_after": "custom_bbd_order_amendment", "read_only": 1}
    ],
    "Sales Order Item": [{"fieldname": "custom_woocommerce_line_item_id", "label": "WooCommerce Line Item ID", "fieldtype": "Data", "insert_after": "item_code", "read_only": 1}],
    "Delivery Note Item": [{"fieldname": "custom_woocommerce_line_item_id", "label": "WooCommerce Line Item ID", "fieldtype": "Data", "insert_after": "item_code", "read_only": 1}],
    "Sales Invoice Item": [{"fieldname": "custom_woocommerce_line_item_id", "label": "WooCommerce Line Item ID", "fieldtype": "Data", "insert_after": "item_code", "read_only": 1}],
}


def after_install():
    ensure_role()
    ensure_custom_fields()
    ensure_settings()
    ensure_indexes()
    backfill_order_roots()


def after_migrate():
    ensure_role()
    ensure_custom_fields()
    ensure_settings()
    ensure_indexes()
    backfill_order_roots()


def ensure_role():
    if not frappe.db.exists("Role", "BBD Order Amendment Manager"):
        frappe.get_doc({"doctype": "Role", "role_name": "BBD Order Amendment Manager", "desk_access": 1}).insert(
            ignore_permissions=True
        )


def ensure_custom_fields():
    create_custom_fields(CUSTOM_FIELDS, update=True)
    frappe.clear_cache()


def ensure_settings():
    settings = frappe.get_single("BBD Woo Sync Settings")
    if not settings.company and frappe.db.exists("Company", "BeautybyDaz"):
        settings.company = "BeautybyDaz"
    if not settings.safe_paid_statuses:
        settings.safe_paid_statuses = "processing,completed,on-hold"
    if not settings.order_poll_limit:
        settings.order_poll_limit = 100
    if not settings.reconcile_limit:
        settings.reconcile_limit = 50
    if not settings.order_amendment_warning_percent:
        settings.order_amendment_warning_percent = 5
    if not settings.order_amendment_limit_percent:
        settings.order_amendment_limit_percent = 20
    if not settings.order_amendment_max_value_change:
        settings.order_amendment_max_value_change = 500000
    if not settings.order_amendment_max_age_days:
        settings.order_amendment_max_age_days = 30
    if not settings.order_amendment_require_proof_above:
        settings.order_amendment_require_proof_above = 50000
    settings.save(ignore_permissions=True)


def backfill_order_roots():
    for doctype in ("Sales Order", "Delivery Note", "Sales Invoice"):
        table = f"tab{doctype}"
        frappe.db.sql(
            f"""UPDATE `{table}`
                SET custom_woocommerce_order_root_id = custom_woocommerce_order_id
                WHERE COALESCE(custom_woocommerce_order_root_id, '') = ''
                  AND COALESCE(custom_woocommerce_order_id, '') != ''"""
        )


def _index_exists(table, name):
    return bool(frappe.db.sql(f"SHOW INDEX FROM `{table}` WHERE Key_name=%s", name))


def _add_unique(table, name, columns):
    if not _index_exists(table, name):
        frappe.db.add_unique(table.removeprefix("tab"), columns, constraint_name=name)


def _add_index(table, name, columns):
    if not _index_exists(table, name):
        frappe.db.add_index(table.removeprefix("tab"), columns, index_name=name)


def ensure_indexes():
    _add_unique("tabBBD Woo Item Mapping", "uniq_bbd_channel_item", ["channel", "erp_item"])
    _add_unique("tabBBD Woo Item Mapping", "uniq_bbd_channel_product", ["channel", "woo_product_id", "variation_id"])
    _add_unique("tabSales Order", "uniq_bbd_woo_order", ["custom_woo_site", "custom_woocommerce_order_id"])
    # Historical order 158337 has duplicate submitted DN/SI rows. Preserve those
    # accounting records and index lookups non-uniquely; the Sales Order unique
    # key plus Redis lock and application lookup prevent new duplicates.
    _add_index("tabDelivery Note", "idx_bbd_woo_dn_order", ["custom_woo_site", "custom_woocommerce_order_id"])
    _add_index("tabSales Invoice", "idx_bbd_woo_si_order", ["custom_woo_site", "custom_woocommerce_order_id"])
    _add_unique("tabDelivery Note", "uniq_bbd_woo_dn_refund", ["custom_woo_site", "custom_woocommerce_refund_id"])
    _add_unique("tabSales Invoice", "uniq_bbd_woo_si_refund", ["custom_woo_site", "custom_woocommerce_refund_id"])
    _add_unique("tabBBD Order Amendment", "uniq_bbd_amendment_revision", ["channel", "woo_order_id", "revision"])
    _add_index("tabBBD Woo Sync Log", "idx_bbd_idempotency", ["idempotency_key"])
