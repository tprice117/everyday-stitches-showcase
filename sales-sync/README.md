# sales-sync

Serverless pipeline that pulls orders from the sales channels once a month and
UPSERTs them into the existing RDS Postgres database (the same tables the Django
app and invoices read from). First source built: **WooCommerce**.

Lives inside the `ESAppProject` repo as a self-contained subfolder. It is a
separate deployable (AWS Lambda) from the Django app (EC2) — nothing here is
imported by Django; they only share the database.

See the [architecture sketch](https://claude.ai/code/artifact/4cfbbae6-d53c-4619-a49b-d208a260aace)
for the big picture.

## Layout

```
sales-sync/
├─ migrations/001_add_external_id.sql   schema change (apply once to RDS)
├─ shared/
│  ├─ params.py    SSM Parameter Store get/put (env-var fallback for local dev)
│  ├─ db.py        psycopg2 connection + idempotent upsert_order()
│  └─ mapping.py   WooCommerce order JSON  ->  our schema (pure, testable)
├─ functions/woo_sync/handler.py         Lambda entry point + WC HTTP client
├─ layer/requirements.txt                requests, psycopg2-binary (Lambda layer)
└─ tests/
   ├─ sample_woo_order.json              a representative WC order
   ├─ test_mapping.py                    mapping only (no network/DB)
   └─ test_upsert_rds.py                 full upsert vs RDS, rolled back
```

## One-time schema change

`migrations/001_add_external_id.sql` adds `orders.external_id` plus a
`unique(source, external_id)` index — the key the upsert uses to avoid
duplicates. It's additive and nullable, so existing manually-entered orders are
untouched. Already reflected in `db-mockup/schema_postgres.sql` and the Django
`Order` model.

Apply it to RDS with psql or via the Django connection, then it's permanent.

## Config (SSM Parameter Store, or `SALES_SYNC_WOO_*` env vars locally)

| Parameter | Meaning |
|---|---|
| `/sales-sync/woo/site_url` | e.g. `https://example-store.com` |
| `/sales-sync/woo/consumer_key` | WooCommerce REST API key (read scope) |
| `/sales-sync/woo/consumer_secret` | WooCommerce REST API secret |
| `/sales-sync/woo/channel` | **fallback** channel only — `R` (default) |
| `/sales-sync/woo/watermark` | ISO timestamp of last successful run |

The watermark advances only after a clean, committed run, so a failed run is
safe to retry.

### Retail vs. wholesale is per-order (one store, one key)

Retail and wholesale orders come from the **same** WooCommerce store and API key
— there is no second credential set. Each order's channel is detected from the
WWPP plugin's `_wwpp_order_type` meta: `wholesale` → `W`, otherwise `R`
(`shared.mapping.order_channel`). The `channel` config above is only the fallback
for an order with no such meta. Wholesale customers are created `type='W',
pct_of_retail=50`. Wholesale bundles need their own `W` rows in the `bundles`
table (retail `R` rows don't apply to wholesale orders).

## Testing locally

DB credentials are read from the Django app's `.env` (same RDS the app uses).

```
cd ESAppProject/sales-sync
python -m tests.test_mapping       # mapping logic, no DB
python -m tests.test_upsert_rds    # full upsert vs RDS, transaction rolled back
```

## Pulling live orders (`tools/woo_pull.py`)

Runs the real pull from your machine, **dry run by default** — it fetches from
the store and reports exactly what would be written, but writes nothing until
you pass `--commit`. Put the API credentials in `ESAppProject/.env` alongside
the `DB_*` vars (that file is gitignored — keys never enter the repo):

```
SALES_SYNC_WOO_SITE_URL=https://yourstore.com
SALES_SYNC_WOO_CONSUMER_KEY=ck_...
SALES_SYNC_WOO_CONSUMER_SECRET=cs_...
SALES_SYNC_WOO_CHANNEL=R
```

Then, from `sales-sync/`:

```
python -m tools.woo_pull --check                       # do the keys work?
python -m tools.woo_pull --since 2026-06-01 --limit 5 --verbose
python -m tools.woo_pull --since 2026-06-01            # full dry run for the window
python -m tools.woo_pull --since 2026-06-01 --commit   # import for real
```

The dry run resolves every pulled SKU against `patterns` first, so unmatched
SKUs (whose line items would be silently skipped) are visible *before* anything
lands. The tool deliberately does not touch the watermark — re-running the same
window is always safe, and the Lambda handler stays the only thing that advances it.

Generate the key in WooCommerce under **WooCommerce → Settings → Advanced → REST
API**, with **Read** permission. Basic auth only works over HTTPS.

## Scheduling it (cron on the EC2 box)

The monthly import runs from cron on the existing EC2 instance rather than as a
Lambda. The box already sits in the VPC with RDS access, already has the code and
the `.env`, and runs 24/7 — so this costs nothing extra and needs no IAM role,
layer, or NAT Gateway. (A Lambda *inside* the VPC would need a NAT Gateway at
~$32/month to reach the WooCommerce API — far more than the job is worth. The
Lambda handler in `functions/` is still there if this ever outgrows the instance.)

**Prerequisite — the server `.env` needs the WooCommerce keys.** It currently has
only `DB_*`. Add the four `SALES_SYNC_WOO_*` vars (see the Config table above) to
`/home/ubuntu/ESAppProject/.env`, or the script exits immediately saying the
config is missing.

Install the schedule:

```
crontab -e
# 3am on the 1st of each month:
0 3 1 * * bash /home/ubuntu/ESAppProject/sales-sync/tools/monthly_sync.sh
```

`tools/monthly_sync.sh` wraps `woo_pull --commit` with what cron needs: absolute
paths (cron has almost no environment), a `flock` so a manual run and the cron
run can never overlap, a size-capped log at `<app>/logs/woo_sync.log`, and a
non-zero exit on failure.

It pulls a **two-month** window each run, not one. Overlap is free — the upsert is
keyed on `(source, external_id)` so re-pulled orders update in place instead of
duplicating — and it catches orders edited after the month they were placed,
which a tight one-month window would silently miss.

Check on it:

```
tail -n 40 /home/ubuntu/ESAppProject/logs/woo_sync.log
```

Anything the import couldn't place shows up on the dashboard's **Import Audit**
page, so a skipped SKU doesn't just vanish into the log.

## Bundles

Some store products are bundles sold as one SKU (e.g. `ES-FPP-BNL`, the Notions
PDF Bundle = five patterns). The **`bundles` table** (migration 004) defines each
bundle as a set of component patterns; `shared/bundles.py` reads it via
`load_bundles(cur, source)` and expands a bundle line into one line item per
component. Each component's line total is its stored `alt_price × quantity`.

Edit bundle definitions in the table (or the Django admin) — no code change:

| column | meaning |
|---|---|
| `bundle_sku` | the SKU the bundle sells under |
| `item_sku` | a component pattern's canonical SKU (FK to `patterns.sku`) |
| `alt_price` | per-unit price recorded for that component (fixed catalog allocation) |
| `description` | human label for the component |
| `source` | `sales_channels` id the definition applies to (default `R`) |
| `physical` | `FALSE` for digital/PDF components |

Only bundles whose components are all real patterns can live here (the `item_sku`
FK enforces it). "Pick any 3 of your choice" bundles (`ES-510-CRV-BDL`) and
template-set bundles (`ES-000-TSB`) are deliberately absent — contents aren't
constant / aren't patterns — so they stay logged as unmatched.

## Decisions baked in

- **Idempotent**: keyed on `(source, external_id)`; re-runs update, never duplicate.
- **Line items replaced** on each import, so edits on the source reflect through.
- **SKU → pattern** by SKU (case-insensitive); unmatched SKUs are returned/logged,
  never silently dropped. The `(PP)`/`(PDF)` format tag is read off the raw SKU
  *before* normalization and stored as `line_items.physical` (PP→physical,
  PDF→digital); the raw SKU itself is kept verbatim in `line_items.source_sku`
  (migration 005) for traceability back to the submitted line.
- **Customers deduped** by email; created (`type=R`, `pct_of_retail=100`) when new.
- **Real `line_total`** is stored from WooCommerce — imported orders don't need the
  revenue estimate the migrated rows rely on.

## Not done yet (next steps)

- SAM template (`template.yaml`) — EventBridge schedule + function + IAM + params.
- Push the credentials to SSM Parameter Store (as `SecureString`) so the
  deployed Lambda can read them; `.env` only covers local runs.
- PayPal and Etsy functions (Etsy adds the OAuth refresh-token rotation).
- Business rules to confirm: retail-vs-wholesale detection, digital (PDF) line
  items → `physical=false`, how to surface unmatched SKUs for review.
