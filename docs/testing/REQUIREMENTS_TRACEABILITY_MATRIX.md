# Yashoda SpiceChain — Requirements Traceability Matrix (RTM)
**Milestone:** M10 (Hardening) | **Task:** T-101  
**Project:** Yashoda Masale ERP & AI Platform  
**Scope:** Backend Services, Database Schema, Invariants, and AI Subsystem  

---

## 1. System Invariants Traceability (B1 — B12)

| Invariant | Description | Enforcement Point | Automated Test Coverage | Status |
| :--- | :--- | :--- | :--- | :--- |
| **B1** | Strict 6-role RBAC (Owner, Factory, Warehouse, Distributor, Dealer, Consumer) | FastAPI `Depends(require_roles)` & DB check constraint | `tests/test_empirical_m1_rbac_and_migrations.py`<br>`tests/test_auth.py` | **VERIFIED** |
| **B2** | Only batches in `released` status can create sellable inventory | `ProductionBatch.status == "released"` filter | `tests/test_production.py`<br>`tests/test_inventory_service.py` | **VERIFIED** |
| **B3** | FEFO (First-Expired, First-Out) stock reservation ordering | `ORDER BY expiry_date ASC, created_at ASC` | `tests/test_fefo_expiry.py`<br>`tests/test_inventory_service.py` | **VERIFIED** |
| **B4** | Stock balances (`on_hand`, `reserved`) can never be negative | DB `CheckConstraint("quantity >= 0")` & service validation | `tests/test_inventory_service.py`<br>`tests/test_orders_hardening.py` | **VERIFIED** |
| **B5** | Stock movements ledger is strictly append-only; no UPDATE/DELETE | Service write-only ledger, DB immutable triggers | `tests/test_inventory_service.py`<br>`tests/test_orders_fulfillment.py` | **VERIFIED** |
| **B6** | Cancelling an order releases reserved stock exactly once | Service status lock + reservation lookup | `tests/test_orders_hardening.py`<br>`tests/test_orders_adversarial.py` | **VERIFIED** |
| **B7** | Strict state machine lifecycle (`pending -> reserved -> packed -> dispatched -> delivered`) | `ALLOWED_TRANSITIONS` table + `FOR UPDATE OF orders` row locks | `tests/test_orders_adversarial.py`<br>`tests/test_orders_hardening.py` | **VERIFIED** |
| **B8** | Order line items snapshot unit price and price list code at purchase time | `OrderItem.unit_price_snapshot`, `price_list_code` | `tests/test_orders_fulfillment.py`<br>`tests/test_orders_adversarial.py` | **VERIFIED** |
| **B9** | Role-based pricing tiers (Dealer -> DEALER, Distributor -> DISTRIBUTOR, Consumer -> MRP) | `resolve_price` service resolver | `tests/test_pricing.py`<br>`tests/test_ai_review_fixes.py` | **VERIFIED** |
| **B10**| Public QR traceability exposes only authorized transparency events; no internal costs | `PublicBatchTrace` projection schema | `tests/test_traceability.py` | **VERIFIED** |
| **B11**| AI actions creating mutations are strictly draft-only pending human approval | `OrderDraft.status == "draft"`, human approve endpoint | `tests/test_ai_review_fixes.py`<br>`tests/test_ai_tools_adversarial.py` | **VERIFIED** |
| **B12**| All monetary and currency calculations use `Decimal` with 2 decimal places | `Numeric(12, 2)` & Python `Decimal` | `tests/test_orders_fulfillment.py`<br>`tests/test_ai_review_fixes.py` | **VERIFIED** |

---

## 2. Milestone Tasks Traceability Matrix

| Milestone | Task ID | Description | Implementing Module | Primary Test Files |
| :--- | :--- | :--- | :--- | :--- |
| **M0** | T-003 | Synthetic data assumptions | `docs/synthetic-data/` | Synthetic data generator tests |
| **M2** | T-020 | Repo, Docker compose, CI setup | Root configuration | `docker-compose.yml`, GitHub Actions CI |
| **M2** | T-021 | DB migrations, user & role tables | `app/auth/models.py`, `alembic/` | `tests/test_migrations.py` |
| **M2** | T-022 | Login, JWT tokens, RBAC, audit log | `app/auth/service.py` | `tests/test_auth.py` |
| **M2** | T-023 | Catalog: categories, products, price lists | `app/catalog/service.py` | `tests/test_catalog.py`, `tests/test_pricing.py` |
| **M2** | T-025 | Route permission matrix test | `app/core/dependencies.py` | `tests/test_empirical_m1_rbac_and_migrations.py` |
| **M3** | T-030 | Suppliers, raw materials, receipts | `app/procurement/service.py` | `tests/test_procurement.py` |
| **M3** | T-031 | Production plans, batches, stages | `app/production/service.py` | `tests/test_production.py` |
| **M3** | T-032 | Quality checks (release/hold/reject) | `app/production/service.py` | `tests/test_production.py` |
| **M3** | T-034 | Stock creation on batch release | `app/production/service.py` | `tests/test_production.py` |
| **M4** | T-040 | Warehouses, inventory balances, ledger | `app/inventory/service.py` | `tests/test_inventory_service.py` |
| **M4** | T-041 | FEFO reservation service | `app/inventory/service.py` | `tests/test_fefo_expiry.py` |
| **M4** | T-042 | Expiry & low stock alerts | `app/inventory/service.py` | `tests/test_alerts.py` |
| **M4** | T-043 | Stock adjustments with audit reason | `app/inventory/service.py` | `tests/test_adjustments.py` |
| **M4** | T-045 | Concurrency test (no overselling) | `app/inventory/service.py` | `tests/test_inventory_concurrency.py` |
| **M5** | T-050 | Orders, state machine, idempotency | `app/orders/service.py` | `tests/test_orders_adversarial.py` |
| **M5** | T-051 | Reserve, pack, dispatch, cancel, return | `app/orders/service.py` | `tests/test_orders_hardening.py` |
| **M5** | T-052 | Invoices, simulated payment, shipments | `app/orders/service.py` | `tests/test_invoices_and_shipments.py` |
| **M5** | T-055 | End-to-end order fulfillment test | `app/orders/service.py` | `tests/test_orders_fulfillment.py` |
| **M6** | T-060 | QR batch tokens, public traceability | `app/traceability/service.py`| `tests/test_traceability.py` |
| **M6** | T-062 | Sales analytics and CSV export | `app/analytics/service.py` | `tests/test_analytics.py` |
| **M6** | T-063 | Security test for public traceability | `app/traceability/router.py` | `tests/test_traceability.py` |
| **M7** | T-070 | Synthetic data generator (reproducible) | `scripts/generate_synthetic_data.py` | `tests/test_synthetic_data.py` |
| **M7** | T-071 | Baseline & candidate ML forecasting | `app/forecasting/` | `tests/test_forecasting.py` |
| **M7** | T-072 | Reorder recommendations & expiry risk | `app/forecasting/service.py`| `tests/test_reorder_recommendations.py` |
| **M7** | T-074 | Reorder mathematics verification | `app/forecasting/` | `tests/test_reorder_math.py` |
| **M8** | T-080 | LLM adapter, RAG knowledge retrieval | `app/ai/adapter.py` | `tests/test_ai_subsystem.py` |
| **M8** | T-081 | RAG answers with fallback | `app/ai/service.py` | `tests/test_ai_subsystem.py` |
| **M8** | T-082 | Permission-aware AI tools & audit log | `app/ai/tools.py` | `tests/test_ai_review_fixes.py` |
| **M8** | T-083 | Dealer reorder draft & approval | `app/ai/router.py` | `tests/test_ai_review_fixes.py` |
| **M8** | T-085 | Prompt injection & refusal tests | `app/ai/service.py` | `tests/test_ai_tools_adversarial.py` |
| **M9** | T-090 | Staging deploy, backups, restore test | `docker-compose.staging.yml` | `tests/test_backup_restore.py` |
| **M9** | T-091 | Health checks, monitoring, telemetry | `app/api/v1/health.py` | `tests/test_health.py` |
| **M9** | T-092 | Clean machine setup & deploy guide | `docs/deployment/` | `docs/deployment/DEPLOYMENT_GUIDE.md` |
| **M10**| T-100 | OWASP security audit & hardening | `docs/security/` | `tests/test_security_hardening.py` |
| **M10**| T-101 | Test execution report & RTM | `docs/testing/` | `docs/testing/TEST_EXECUTION_REPORT.md` |
| **M10**| T-102 | Feature freeze sign-off | `docs/release/` | `docs/release/FEATURE_FREEZE_SIGNOFF.md` |
| **M11**| T-110 | Final project report & user manual | `docs/project-report/` | `docs/user-manual/USER_MANUAL.md` |
| **M11**| T-111 | Mock viva guide & rehearsal | `docs/viva/` | `docs/viva/VIVA_PREPARATION_GUIDE.md` |
| **M11**| T-112 | Release tag, changelog, README | Repository Root | `CHANGELOG.md`, `README.md` |
