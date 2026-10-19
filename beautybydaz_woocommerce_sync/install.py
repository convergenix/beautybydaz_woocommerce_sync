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
        {"fieldname": "custom_woocommerce_order_number", "label": "WooCommerce Order Number", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_id", "read_only": 1},
        {"fieldname": "custom_woo_channel", "label": "Woo Channel", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_number", "read_only": 1},
        {"fieldname": "custom_woo_site", "label": "Woo Site", "fieldtype": "Data", "insert_after": "custom_woo_channel", "read_only": 1},
    ],
    "Delivery Note": [
        {"fieldname": "custom_woocommerce_order_id", "label": "WooCommerce Order ID", "fieldtype": "Data", "insert_after": "po_no", "read_only": 1},
        {"fieldname": "custom_woocommerce_order_number", "label": "WooCommerce Order Number", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_id", "read_only": 1},
        {"fieldname": "custom_woocommerce_refund_id", "label": "WooCommerce Refund ID", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_number", "read_only": 1},
        {"fieldname": "custom_woo_channel", "label": "Woo Channel", "fieldtype": "Data", "insert_after": "custom_woocommerce_refund_id", "read_only": 1},
        {"fieldname": "custom_woo_site", "label": "Woo Site", "fieldtype": "Data", "insert_after": "custom_woo_channel", "read_only": 1},
    ],
    "Sales Invoice": [
        {"fieldname": "custom_woocommerce_order_id", "label": "WooCommerce Order ID", "fieldtype": "Data", "insert_after": "po_no", "read_only": 1},
        {"fieldname": "custom_woocommerce_order_number", "label": "WooCommerce Order Number", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_id", "read_only": 1},
        {"fieldname": "custom_woocommerce_refund_id", "label": "WooCommerce Refund ID", "fieldtype": "Data", "insert_after": "custom_woocommerce_order_number", "read_only": 1},
        {"fieldname": "custom_woo_channel", "label": "Woo Channel", "fieldtype": "Data", "insert_after": "custom_woocommerce_refund_id", "read_only": 1},
        {"fieldname": "custom_woo_site", "label": "Woo Site", "fieldtype": "Data", "insert_after": "custom_woo_channel", "read_only": 1},
    ],
    "Sales Order Item": [{"fieldname": "custom_woocommerce_line_item_id", "label": "WooCommerce Line Item ID", "fieldtype": "Data", "insert_after": "item_code", "read_only": 1}],
    "Delivery Note Item": [{"fieldname": "custom_woocommerce_line_item_id", "label": "WooCommerce Line Item ID", "fieldtype": "Data", "insert_after": "item_code", "read_only": 1}],
    "Sales Invoice Item": [{"fieldname": "custom_woocommerce_line_item_id", "label": "WooCommerce Line Item ID", "fieldtype": "Data", "insert_after": "item_code", "read_only": 1}],
}


def after_install():
    ensure_custom_fields()
    ensure_settings()
    ensure_indexes()


def after_migrate():
    ensure_custom_fields()
    ensure_settings()
    ensure_indexes()


def ensure_custom_fields():
    create_custom_fields(CUSTOM_FIELDS, update=True)
    frappe.clear_cache()


def ensure_settings():
    settings = frappe.get_single("BBD Woo Sync Settings")
    if not settings.company and frappe.db.exists("Company", "BeautybyDaz"):
        settings.company = "BeautybyDaz"
    settings.payment_entries_enabled = 0
    if not settings.safe_paid_statuses:
        settings.safe_paid_statuses = "processing,completed,on-hold"
    if not settings.order_poll_limit:
        settings.order_poll_limit = 100
    if not settings.reconcile_limit:
        settings.reconcile_limit = 500
    settings.save(ignore_permissions=True)


def _index_exists(table, name):
    return bool(frappe.db.sql(f"SHOW INDEX FROM `{table}` WHERE Key_name=%s", name))


def _add_unique(table, name, columns):
    if not _index_exists(table, name):
        frappe.db.sql(f"ALTER TABLE `{table}` ADD UNIQUE INDEX `{name}` ({','.join('`'+c+'`' for c in columns)})")


def _add_index(table, name, columns):
    if not _index_exists(table, name):
        frappe.db.sql(f"ALTER TABLE `{table}` ADD INDEX `{name}` ({','.join('`'+c+'`' for c in columns)})")


def ensure_indexes():
    _add_unique("tabBBD Woo Item Mapping", "uniq_bbd_channel_item", ["channel", "erp_item"])
    _add_unique("tabBBD Woo Item Mapping", "uniq_bbd_channel_product", ["channel", "woo_product_id", "variation_id"])
    _add_unique("tabSales Order", "uniq_bbd_woo_order", ["custom_woo_site", "custom_woocommerce_order_id"])
    _add_unique("tabDelivery Note", "uniq_bbd_woo_dn_order", ["custom_woo_site", "custom_woocommerce_order_id"])
    _add_unique("tabSales Invoice", "uniq_bbd_woo_si_order", ["custom_woo_site", "custom_woocommerce_order_id"])
    _add_unique("tabDelivery Note", "uniq_bbd_woo_dn_refund", ["custom_woo_site", "custom_woocommerce_refund_id"])
    _add_unique("tabSales Invoice", "uniq_bbd_woo_si_refund", ["custom_woo_site", "custom_woocommerce_refund_id"])
    _add_index("tabBBD Woo Sync Log", "idx_bbd_idempotency", ["idempotency_key"])
