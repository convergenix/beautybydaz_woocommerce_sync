app_name = "beautybydaz_woocommerce_sync"
app_title = "Beauty By Daz WooCommerce Sync"
app_publisher = "Convergenix"
app_description = "Audited ERPNext and WooCommerce order, refund, price, stock and item sync"
app_email = "ai-agent@convergenix.ng"
app_license = "MIT"
required_apps = ["erpnext"]

after_install = "beautybydaz_woocommerce_sync.install.after_install"
after_migrate = "beautybydaz_woocommerce_sync.install.after_migrate"

scheduler_events = {
    "cron": {
        "* * * * *": ["beautybydaz_woocommerce_sync.jobs.poll_paid_orders"],
        "*/5 * * * *": ["beautybydaz_woocommerce_sync.jobs.reconcile_catalog"],
    }
}

doc_events = {
    "Item Price": {
        "after_insert": "beautybydaz_woocommerce_sync.events.item_price_changed",
        "on_update": "beautybydaz_woocommerce_sync.events.item_price_changed",
    },
    "Item": {
        "after_insert": "beautybydaz_woocommerce_sync.events.item_created",
    },
    "Stock Entry": {"on_submit": "beautybydaz_woocommerce_sync.events.stock_document_submitted"},
    "Purchase Receipt": {"on_submit": "beautybydaz_woocommerce_sync.events.stock_document_submitted"},
    "Delivery Note": {"on_submit": "beautybydaz_woocommerce_sync.events.stock_document_submitted"},
    "Stock Reconciliation": {"on_submit": "beautybydaz_woocommerce_sync.events.stock_document_submitted"},
    "Sales Invoice": {"on_submit": "beautybydaz_woocommerce_sync.events.sales_invoice_submitted"},
}
