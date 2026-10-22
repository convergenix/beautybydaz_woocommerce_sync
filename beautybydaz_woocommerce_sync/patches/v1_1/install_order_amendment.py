from beautybydaz_woocommerce_sync.install import (
    backfill_order_roots,
    ensure_custom_fields,
    ensure_indexes,
    ensure_role,
    ensure_settings,
)


def execute():
    ensure_role()
    ensure_custom_fields()
    ensure_settings()
    ensure_indexes()
    backfill_order_roots()
