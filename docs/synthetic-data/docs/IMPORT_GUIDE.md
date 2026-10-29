# Yashoda Masale synthetic data package

## Data disclaimer
Every record in this package is fictional, generated from a fixed random seed, and is **not actual sponsor data**.

## Included CSVs
- `products.csv`: 100 products
- `variants.csv`: 500 sellable pack variants
- `suppliers.csv`: 12 fictional suppliers
- `warehouses.csv`: 2 warehouses
- `production_batches.csv`: 200 fictional batches with mixed approved/held/rejected status and expiry dates
- `inventory_balances.csv` and `stock_movements.csv`: opening stock and matching synthetic receipt entries for eligible batches
- `dealers.csv`: 150 fictional dealers
- `orders.csv`: 10,000 orders across 730 days
- `order_items.csv`: order lines linked to orders and variants
- `sales_history_daily.csv`: 73,000 daily observations (100 products x 730 days)
- `manifest.json`: seed, disclaimer, and record counts

## Run (Python 3.10+, standard library only)
From the package root:
```powershell
python scripts/generate_data.py --output data --seed 42
python scripts/validate_data.py data
python scripts/preview_import.py
```
Same seed reproduces the same logical dataset. Generator overwrites CSVs in the target folder.

## PostgreSQL import safety
The importer is deliberately a **read-only preview**. Exact database schema was not inspected, so this package does not automatically write to PostgreSQL. Your repository has existing SQLAlchemy models, Alembic migrations, and seed scripts; use those rather than creating duplicate tables.

Before implementing a DB importer:
1. Back up your development database.
2. Inspect `backend/alembic/versions/` and `backend/app/` models plus existing `backend/scripts/seed_*.py`.
3. Check `alembic current` and `alembic heads` in the project's configured environment.
4. Map these external IDs (e.g. `PRD-0001`) to real database PKs if the schema uses integer IDs.
5. Adapt CSV field names, enum/status values, foreign keys, money types, and quantity units to the existing models.
6. Import in FK order: categories/products -> variants -> suppliers/warehouses -> batches -> balances/movements -> users/dealers -> orders -> order items -> sales history.
7. Use a dedicated development database and a transaction. Make the importer idempotent or only clear/reseed a disposable DB. Never run destructive reset commands on production/sponsor data.
8. Preserve inventory rules: `on_hand >= 0`, `reserved >= 0`, `reserved <= on_hand`; only released, unexpired, quality-approved batches may be reserved.
9. Run existing tests and reconcile stock balances against the movement ledger after importing.

## Important interpretation notes
- Inventory quantities use `packs` for demo simplicity. Convert to your actual unit system if the models store grams or kilograms.
- `sales_history_daily.csv` is simulated separately from orders. Do not add its revenue to order totals as though they are the same transactions.
- Batch quality and expiry values are testing fixtures, not official food-safety requirements.
- `.example.test` emails and fictional phone numbers are placeholders.

## Suggested report wording
"The application was developed and tested using a reproducible synthetic dataset because the sponsor's operational data was not made available. Synthetic records were used to evaluate workflow correctness, inventory invariants, order processing, and forecasting pipeline behavior. These records do not represent actual sponsor transactions or quality outcomes."
