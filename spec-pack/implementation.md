# Implementation Guide

Use with `task.md`. Give your AI coding tool one task, plus the matching section here.

## 1. Setup order
1. Create the Git repo. Add `/docs` (this pack), `/backend`, `/frontend`, `docker-compose.yml`, `.env.example`, `.gitignore`.
2. Backend: Python 3.11+, FastAPI, SQLAlchemy 2, Alembic, Pydantic, pytest, ruff, black.
3. Frontend: Next.js (App Router), TypeScript strict, ESLint, Prettier.
4. Database: PostgreSQL in Docker. Run migrations on start.
5. CI: lint, type-check, test, build on every pull request.
6. Run `docker compose up` and confirm `/health` and the login page work before writing features.

## 2. Core tables (shortened)
```sql
users(id, email unique, password_hash, role_id, is_active, created_at)
roles(id, name)                                -- owner, factory, warehouse, distributor, dealer, consumer
products(id, name, category_id, description, is_active)
product_variants(id, product_id, sku unique, pack_size_g, image_url)
price_lists(id, name)                          -- MRP, DEALER, DISTRIBUTOR
price_list_items(id, price_list_id, variant_id, price numeric(12,2), valid_from)
production_batches(id, batch_code unique, variant_id, planned_qty, actual_qty,
                   mfg_date, expiry_date, status, public_token unique, created_by)
quality_checks(id, batch_id, parameter_id, measured_value, passed, checked_by, checked_at)
quality_decisions(id, batch_id, decision, remarks, decided_by, decided_at)
warehouses(id, name, location)
inventory_balances(id, warehouse_id, batch_id, on_hand int, reserved int,
                   CHECK (on_hand >= 0), CHECK (reserved >= 0), CHECK (reserved <= on_hand),
                   UNIQUE (warehouse_id, batch_id))
stock_movements(id, warehouse_id, batch_id, type, qty int, ref_type, ref_id, reason, actor_id, created_at)
stock_reservations(id, order_item_id, balance_id, qty, status)   -- Active, Released, Consumed
orders(id, order_no unique, customer_id, order_type, status, idempotency_key unique, created_at)
order_items(id, order_id, variant_id, qty, unit_price_snapshot numeric(12,2), price_list_code)
invoices(id, order_id, invoice_no unique, total numeric(12,2), payment_status)
shipments(id, order_id, status, carrier, dispatched_at, delivered_at)
batch_events(id, batch_id, event_type, event_at, public_text)
verification_scans(id, batch_id nullable, token_hash, scanned_at, ip_hash, result)
audit_logs(id, actor_id, action, entity, entity_id, before jsonb, after jsonb, at)
ai_tool_runs(id, user_id, tool, args jsonb, result jsonb, status, at)
sales_forecasts(id, variant_id, period, model, value)
forecast_evaluations(id, model, mae, rmse, mape, test_period, notes)
```
Use UUID or bigint ids consistently. Add indexes on foreign keys and on `(status)`, `(expiry_date)`.

## 3. State machines
**Batch:** `Draft -> InProduction -> QualityPending -> Released | OnHold | Rejected`.
`OnHold -> Released | Rejected` after review. `Released` stock may later be put `OnHold` by a Factory/Owner action (audited), which blocks new reservations.

**Order:** `Placed -> Confirmed -> Reserved -> Packed -> Dispatched -> Delivered`.
`Cancelled` allowed from Placed, Confirmed, Reserved, Packed (release reservation once). `Returned` only from Delivered, then a warehouse decision.

Implement as a table of allowed transitions in one place and test every illegal pair.

## 4. Price resolution
```
price_list = DEALER if user.role == dealer
           = DISTRIBUTOR if role == distributor
           = MRP otherwise
unit_price = latest price_list_items.price where valid_from <= now
order_item.unit_price_snapshot = unit_price
```
Owner and staff may see all lists. The API for a dealer must never return distributor prices.

## 5. FEFO and reservation (inside one transaction)
```sql
SELECT ib.id, ib.on_hand - ib.reserved AS available, b.expiry_date
FROM inventory_balances ib
JOIN production_batches b ON b.id = ib.batch_id
WHERE ib.warehouse_id = :wh
  AND b.variant_id = :variant
  AND b.status = 'Released'
  AND b.expiry_date >= CURRENT_DATE + :min_shelf_days
  AND ib.on_hand - ib.reserved > 0
ORDER BY b.expiry_date ASC, b.id ASC
FOR UPDATE OF ib;
```
Pseudocode:
```
need = qty
allocations = []
for row in rows (earliest expiry first):
    take = min(need, row.available)
    allocations.append((row.id, take)); need -= take
    if need == 0: break
if need > 0: rollback, raise InsufficientStock
for (balance_id, take) in allocations:
    update inventory_balances set reserved = reserved + take where id = balance_id
    insert stock_reservations(..., 'Active')
    insert stock_movements(type='RESERVE', qty=take, ...)
commit
```
`min_shelf_days` is a configurable rule (ask the sponsor).

**Dispatch:** set reservation `Consumed`, `on_hand -= qty`, `reserved -= qty`, insert `DISPATCH` movement. **Cancel:** only if reservation is `Active`: `reserved -= qty`, status `Released`, insert `RELEASE` movement.

## 6. Public QR page
- `GET /api/v1/traceability/{public_token}`.
- Look up by token. If not found: return a "No record found" response (and log the scan).
- Return only: product name, batch code, mfg date, expiry date, release status, and `batch_events.public_text`.
- Never return supplier names, customer data, internal ids or costs.
- Use long random tokens (not sequential ids). Rate-limit the endpoint.

## 7. Synthetic data generator (`scripts/generate_data.py`)
- Fixed random seed, saved in the report.
- 24 months of sales per product and region:
  `demand = base * trend * season * festival * noise`
  - `trend`: slow growth, e.g. 1.0 to 1.2 over 24 months
  - `season`: monthly factors (e.g. winter higher for some masalas)
  - `festival`: multipliers around festival months
  - `noise`: random +/- 10%
- Generate dealers, distributors, warehouses, batches (some held/rejected), orders, reviews.
- Add a label column or table `is_synthetic = true` and show a "Demo data" badge.
- Keep a **held-out test period** (last 3 to 6 months) that the model never sees.
- Be honest: festival effects you put into the data and then "discover" only prove the pipeline works, not real-world accuracy.

## 8. Forecasting
1. Baseline: seasonal naive (same month last year) and moving average.
2. Candidate: e.g. gradient boosting or regression with lag features, month, festival flag, trend.
3. Split by time. Train on early months, test on the latest months. Never shuffle.
4. Metrics: MAE, RMSE. Use MAPE only when actuals are not near zero, and explain its limit.
5. Save results in `forecast_evaluations`. Show baseline vs model on the UI.
6. If the model does not beat the baseline, say so. That is a valid result.

## 9. Reorder and expiry risk
```
reorder_point = forecast_demand_lead_time + safety_stock
available = on_hand - reserved
if available <= reorder_point: recommend qty = target_cover - available
```
Show the inputs next to the recommendation. Expiry risk: for each batch compare available stock with forecast demand until expiry, flag batches likely to remain unsold, and suggest actions (discount offer, prioritise in dispatch).

## 10. RAG assistant
1. Prepare approved content: product sheets, FAQs, recipes, policies (written by the team and approved by the sponsor).
2. Split into chunks with titles. Store text, source, and (if used) embeddings.
3. Retrieve top-k by text search (start simple) or vectors (pgvector).
4. Prompt: answer only from the provided context, cite sources, otherwise say "approved information not available."
5. Return answer plus sources. Log the request.
6. Test: unanswerable question, conflicting sources, and prompt-injection text inside a document.

## 11. Agent tools (whitelist)
| Tool | Type | Notes |
|---|---|---|
| `get_sales_summary(period, group_by)` | read | owner only |
| `get_slow_movers(period)` | read | owner only |
| `get_dealer_history(dealer_id)` | read | dealer sees own only |
| `check_stock(variant_id)` | read | |
| `create_order_draft(items)` | write draft | needs human approval |
Validate all arguments on the server. Run with the user's permissions. Log each run. Never execute model-written SQL.

## 12. Dealer reorder agent flow
1. Dealer asks "reorder my usual monthly stock".
2. Tool: fetch own last N orders, compute usual quantities.
3. Tool: check price list and available stock.
4. Create a DRAFT with reasons.
5. Dealer reviews, edits, approves. Only then does the normal order endpoint run.

## 13. API list

All routes live under `/api/v1` and are namespaced by module, so the module a
route belongs to is visible in the path.

| Module | Routes |
|---|---|
| Auth | `/auth/login`, `/auth/me`, `/auth/register`, `/auth/staff` |
| Catalog | `/categories`, `/products`, `/products/{id}/variants`, `/products/variants/{id}/prices/{list}` |
| Procurement | `/procurement/suppliers`, `/procurement/raw-materials`, `/procurement/receipts`, `/procurement/lots` |
| Production | `/production/plans`, `/production/batches`, `/production/batches/{id}/transition`, `/production/batches/{id}/quantity`, `/production/batches/{id}/release`, `/production/batches/{id}/quality-checks`, `/production/batches/{id}/quality-decisions`, `/production/quality-parameters` |
| Inventory | `/inventory`, `/inventory/expiring`, `/inventory/low-stock`, `/inventory/movements`, `/inventory/adjustments` |
| Orders | `/orders`, `/orders/{id}`, `/orders/{id}/reserve`, `/orders/{id}/pack`, `/orders/{id}/dispatch`, `/orders/{id}/cancel`, `/orders/{id}/return`, `/orders/{id}/deliver` |
| Traceability | `/traceability/{public_token}` (public), `/traceability/batches/{id}/events`, `/traceability/batches/{id}/token`, `/traceability/scans` |
| Analytics | `/analytics/sales-summary`, `/analytics/sales-summary.csv`, `/analytics/slow-movers` |
| AI | `/ai/chat`, `/ai/agent/query`, `/ai/forecasts`, `/ai/recommendations/reorder`, `/ai/drafts/{id}/approve`, `/ai/tool-runs` |

The report's original flat list (`/production-plans`, `/batches`, `/analytics/sales`)
was namespaced during implementation so that every route carries its module, the
same convention the other modules already used.

## 14. Prompt templates for coding tools

**Start of every session**
```
Read spec-pack/rules.md and spec-pack/memory.md. Then read task T-0XX in spec-pack/task.md.
Give a short plan first. Do only this task. Follow all business invariants B1-B12.
Write tests in the same change. List files you will change.
```

**Backend module**
```
Implement the <module> module following spec-pack/architecture.md (router, schemas, service, repository, models).
Use the tables in spec-pack/implementation.md section 2. Add Alembic migration and pytest tests.
Enforce role and record-level permissions. Do not change other modules.
```

**FEFO and reservation**
```
Implement FEFO selection and transactional reservation as in spec-pack/implementation.md section 5.
Write tests: earliest expiry first, held/expired excluded, insufficient stock, two concurrent reservations for the last unit, cancel releases exactly once.
```

**Frontend page**
```
Build the <page> page for role <role> using design tokens from spec-pack/design.md.
Use these API endpoints: <list>. Handle loading, empty, error and no-permission states.
Do not hard-code colours. TypeScript strict.
```

**AI feature**
```
Implement <feature> following spec-pack/rules.md section 8. Model never writes SQL. Use whitelisted tools.
Add tests for: no-source refusal, permission checks, draft-not-submitted-without-approval, prompt-injection attempt.
```

**Review prompt (run before merging)**
```
Review this diff against spec-pack/rules.md. List any violation of B1-B12, security rules, missing tests, or missing states. Do not rewrite the code, only report.
```
