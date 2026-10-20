# Beauty By Daz ERPNext ↔ WooCommerce Sync

Production Frappe v15 app for deterministic, channel-separated synchronization between ERPNext and the Beauty By Daz retail and wholesale WooCommerce sites.

## Safety boundaries

- Imports paid orders only.
- Uses explicit per-channel product/variation mappings; no fuzzy matching.
- Keeps retail and wholesale price lists and warehouses separate.
- Creates Payment Entries only when explicitly enabled after validation and only for mapped Paystack/Nomba paid orders.
- Never defaults an unknown payment gateway and never creates automatic cash-refund payments.
- Writes only `regular_price` for price synchronization.
- Reads back and validates every WooCommerce write.
- Creates all new WooCommerce products as drafts.
- Uses Redis locks, ERP document lookups, and unique database indexes for idempotency.
- Stores Woo credentials only in encrypted Frappe Password fields.

## Schedules

- Every minute: paid order and refund polling.
- Every five minutes: deterministic price and stock reconciliation.
- Item Price, Item, and submitted/cancelled stock transaction hooks enqueue asynchronous synchronization.

## Tests

```bash
python -m pytest -q
```

The test suite covers paid-status validation, channel/order/payment idempotency, Paystack and Nomba routing, unknown gateways, payment amount/currency validation, duplicate/existing Payment Entry decisions, retail/wholesale stock isolation, variations, retry behavior, refunds, draft-only product creation, and failed read-back detection.

## Deployment

1. Take a complete site backup.
2. Install the app and migrate the target site only.
3. Configure encrypted channel credentials with `beautybydaz_woocommerce_sync.api.configure_from_file` while sync remains disabled.
4. Import the reviewed dry-run mapping CSV.
5. Run manual acceptance tests.
6. Disable the legacy external order poller.
7. Enable the app with `beautybydaz_woocommerce_sync.api.enable_sync`.

See production deployment records for exact backup and rollback paths. Never commit credential files.
