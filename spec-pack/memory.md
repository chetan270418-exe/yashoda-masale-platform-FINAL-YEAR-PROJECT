# Project Memory

Read this at the start of every session. Update it at the end. Keep it short and factual.

## 1. Project in one paragraph
**Yashoda SpiceChain AI** is a web-first platform for the spice business Yashoda Masale. It records raw-material receipt, production batches, quality release, warehouse stock with FEFO, dealer/distributor and consumer orders, and QR batch traceability. A business AI layer adds forecasting, reorder and expiry-risk suggestions, a RAG assistant, a dealer reorder agent and natural-language analytics. It is a Diploma (Computer Engineering) final-year project with a 4-member team and a 24-week plan.

## 2. Status
- Current milestone: M5 & M8 Integration (Ordering Service, AI Draft Approval Linkage, & Synthetic Data Verification completed & verified)
- Last session summary: Completed Milestone M5 Ordering Service (T-050, T-051, T-055), AI Order Draft Approval Integration (R3), and Synthetic Dataset Documentation & Verification (R5). Connected `POST /api/v1/ai/drafts/{id}/approve` directly to M5 order creation with price snapshots (B8, B12), idempotency keying (Rule 48), and automatic FEFO inventory batch reservation; added comprehensive dataset documentation in `docs/synthetic-data/DATASET_SPECIFICATION.md` detailing mathematical assumptions, multiplicative decomposition, schemas, and Rule 44/89 disclaimers; authored automated integration test suites `test_ai_draft_m5_integration.py` and `test_synthetic_datasets.py`; formatted all adversarial and empirical tests to strict ruff standards. All 302 backend tests passing on PostgreSQL 18 with 0 failures and 0 linter issues.
- Next task: M6 (QR Batch Traceability & Public Lookup: T-060, T-062).


## 3. Decisions log
| ID | Decision | Reason |
|---|---|---|
| D-001 | Modular monolith, not microservices | Simpler to build, test and explain |
| D-002 | Web first. Mobile later using the same APIs | Reduce risk |
| D-003 | PostgreSQL is the source of truth. pgvector only if needed | One database to run and explain |
| D-004 | Stock is an append-only ledger plus derived balances | Auditability, no silent edits |
| D-005 | Order items store price snapshots | Old orders must not change |
| D-006 | FEFO excludes held, rejected, expired batches | Food safety and business rule |
| D-007 | All demo data is synthetic and labelled | No real sponsor data assumed |
| D-008 | Quality parameters are placeholders until the sponsor supplies them | Do not invent food-safety limits |
| D-009 | Payments are simulated. No card data stored | Scope and safety |
| D-010 | LLM provider behind an adapter; keys server-side only | Switch providers easily, protect keys |
| D-011 | AI never writes SQL; whitelisted tools only | Safety and permissions |
| D-012 | AI actions that change data are drafts needing human approval | Human in the loop |
| D-013 | QR page proves a record exists, not that a pack is genuine | Honest claims |
| D-014 | Telegram bot before WhatsApp if a chat channel is added | WhatsApp needs business verification |
| D-015 | Forecast evaluated against a baseline on a chronological split | Honest evaluation |
| D-016 | Dropped passlib and python-jose; use bcrypt 4.0.1 and PyJWT 2.10.1 directly | passlib breaks with bcrypt>=4.1; python-jose has CVEs; fewer deps |
| D-017 | 6 canonical roles: owner, factory, warehouse, distributor, dealer, consumer | Spec says 6; alias code removed for simplicity |
| D-018 | X-Forwarded-For trusted only when TRUST_PROXY_HEADERS=true | Prevents rate-limiter bypass via spoofed headers |
| D-019 | JWT_SECRET must be non-default in non-local environments | App refuses to start if secret is empty or dev default outside local |
| D-020 | AI never generates SQL; agent invokes whitelisted, permission-bound tools only | Security and safety (Rules 60, 61) |
| D-021 | AI mutations generate reviewable drafts requiring explicit human confirmation | Human in the loop (Rule 62) |
| D-022 | RAG strictly cites approved knowledge files or returns exact refusal string | Groundedness and truthfulness (Rule 63) |
| D-023 | Forecast backtesting evaluates candidate against baselines on chronological split | Honest metrics (Rule 66, D-015) |

## 4. Glossary
- **Batch:** one production run with a unique code, dates and quality status.
- **FEFO:** First-Expired, First-Out stock picking.
- **Lot:** a received quantity of a raw material.
- **Reservation:** stock held for an order but not yet dispatched.
- **Ledger:** append-only list of stock movements.
- **RAG:** the AI retrieves approved documents before answering.
- **Tool (AI):** a safe, whitelisted function the AI may ask the server to run.
- **Synthetic data:** generated demo data, not real business records.
- **Baseline:** a simple forecast used to check whether the model adds value.
- **MAE / RMSE / MAPE:** forecast error measures.

## 5. Roles
Owner, Factory, Warehouse, Distributor, Dealer, Consumer (6 canonical, no aliases).

## 6. Known issues and gotchas
- Tests run on SQLite locally (PostgreSQL only in CI). FOR UPDATE, CHECK constraints and Alembic migrations are only fully tested in CI.
- JSONB columns use `.with_variant(JSONB, "postgresql")` for SQLite compat.

## 7. Open questions
- Real quality parameters and limits from the sponsor?
- Hosting allowed by college?
- LLM provider and key?
- Number of dealers/warehouses in the demo?

## 8. Session log (newest first)
```
2026-10-29 | M5 & M8 Integration (Ordering & AI Draft Approval) | Completed Milestone M5 Ordering Service (T-050, T-051, T-055), AI Order Draft Approval Integration (R3), and Synthetic Dataset Documentation & Verification (R5): connected approve_order_draft to active M5 order creation with price snapshots (B8, B12), idempotency keying (Rule 48), and automatic FEFO stock reservation; added dataset documentation in docs/synthetic-data/DATASET_SPECIFICATION.md detailing multiplicative demand model, schema mappings, and Rule 44/89 disclaimers; authored automated integration test suites test_ai_draft_m5_integration.py and test_synthetic_datasets.py; formatted all empirical tests to strict ruff standards | backend/app/ai/router.py, docs/synthetic-data/DATASET_SPECIFICATION.md, backend/tests/test_ai_draft_m5_integration.py, backend/tests/test_synthetic_datasets.py, backend/tests/test_empirical_m2_*.py, spec-pack/task.md, spec-pack/memory.md | 302 passed, 4 skipped in backend (0 failures), 100% ruff clean | Next: M6 (QR Batch Traceability & Public Lookup: T-060, T-062)
2026-10-29 | M2 (T-051) Fulfillment Endpoints & Inventory Lifecycle | Implemented fulfillment service methods (reserve_order with FEFO and multi-item atomic rollback, pack_order, dispatch_order with B5 ledger movements and balance decrements, cancel_order with B6 atomic CAS release and customer/staff role check, return_order with B7 quarantine policy, resolve_target_warehouse), Pydantic schemas (OrderReserveRequest, OrderDispatchRequest, OrderCancelRequest, OrderReturnRequest, StockReservationRead), router endpoints under /api/v1/orders/ (/reserve, /pack, /dispatch, /cancel, /return, /deliver, /status) with RBAC singletons; authored comprehensive automated test suite in tests/test_orders_fulfillment.py (15 tests covering B1-B7 invariants, full lifecycle, cancellations, and RBAC matrix) | backend/app/orders/service.py, backend/app/orders/schemas.py, backend/app/orders/router.py, backend/tests/test_orders_fulfillment.py, spec-pack/memory.md | 281 passed, 4 skipped in backend (0 failures), ruff clean | Next: M3 (AI Order Draft Approval Integration)
2026-10-29 | M1 (T-050) Orders Service Foundation | Implemented order models, Alembic migration 0005 with SQLite downgrade normalization, Pydantic schemas (string Decimal serialization B12), orders service (state machine Rule 49, idempotency replay/conflict Rule 48, role-based price snapshots B8/B9), router endpoints (POST/GET /api/v1/orders), main.py routing, and inventory backward-compatibility re-export; added 10 automated tests in test_orders_models_and_creation.py | backend/app/orders/*, backend/app/inventory/models.py, backend/alembic/versions/0005_m5_ordering_service.py, backend/alembic/env.py, backend/app/main.py, backend/tests/test_orders_models_and_creation.py, spec-pack/memory.md | 228 passed, 3 skipped in backend (0 failures), ruff clean | Next: T-051 (Order Fulfillment & Inventory Lifecycle)

2026-10-29 | AI Subsystem Hardening (Review Fixes) | Hardened AI tools, routing, concurrency, and rate limiting: implemented role-based price resolution (B9) and Decimal strings (B12) in tool_create_order_draft with empty/inactive/unpriced validation and duplicate variant line merging; added period (last_7_days, last_30_days, last_90_days, all_time) and group_by (variant, product, category) filtering with cancelled order exclusion in tool_get_sales_summary; implemented threshold_days filter with FEFO expiry sorting in tool_get_slow_movers; added row-level locking (with_for_update) on OrderDraft query in approve_order_draft; attached in-memory sliding-window RateLimiters (20 req/60s chat, 30 req/60s agent query) and wired conftest.py reset fixtures; documented ai-services/ as a standalone prototype demo with architectural comparison; created test_ai_review_fixes.py (18 tests) and test_ai_approval_concurrency.py (7 tests); updated test_ai_tools.py | backend/app/ai/tools.py, backend/app/ai/router.py, backend/tests/conftest.py, ai-services/README.md, backend/tests/test_ai_tools.py, backend/tests/test_ai_review_fixes.py, backend/tests/test_ai_approval_concurrency.py, spec-pack/memory.md | 177 passed, 3 skipped in backend (0 failures) | Next: Frontend UI (T-044, M5, or T-073/T-084)
2026-10-28 | ai-services/ package | Populated standalone ai-services/ package conforming to README.md and TEAM_ROLES_AND_WORKFLOW.md (Member 4 - Chetan Sonawane): forecasting/ (generator, engine, reorder), rag/ (retriever, service, knowledge_base), llm/ (LLMAdapter, MockLLMAdapter, LiveHTTPLLMAdapter), agent/ (tools, RBAC checks), standalone CLI demo (demo.py), requirements.txt, comprehensive documentation, and 14 unit tests; 100% ruff/black clean; all 14 tests passing in 0.14s | ai-services/*, spec-pack/memory.md | 14 passed (ai-services), 158 passed (backend) | Next: Frontend UI / Ordering
2026-10-28 | M7 & M8 (T-070-074, T-080-085) | Implemented Yashoda SpiceChain AI subsystem: Alembic migration 0004_ai_subsystem (ai_tool_runs, sales_forecasts, forecast_evaluations, order_drafts), models, MockLLMAdapter & LiveHTTPLLMAdapter, RAG BM25 retriever & service with mandatory source citations and exact refusal fallback, 5 safe whitelisted tools (check_stock, get_sales_summary, get_slow_movers, get_dealer_history, create_order_draft) with user-permission binding and audit logging, 24-month synthetic demand generator, 3 forecasting models (seasonal naive, moving average 3m, linear trend seasonal) with MAE/RMSE/MAPE backtesting, reorder math with batch expiry risk, draft-order human approval workflow, 6 API endpoints (/api/v1/ai/*); ruff/black formatted; 22 new tests passing | backend/app/ai/*, backend/alembic/versions/0004_ai_subsystem.py, backend/tests/test_ai_*.py | 158 passed on live PostgreSQL 18 | Next: Frontend UI (T-044, M5, or T-073/T-084)
2026-10-28 | T-042, T-045 Review | Review hardening: fixed broken SQLite test runner by skipping multithreaded test on SQLite; fixed Invariant B2 violation in low-stock alert query by excluding expired batches from available stock via conditional case aggregation; fixed missing OUT_OF_STOCK reporting for variants with held batches; added 3 new edge case tests (expired batch exclusion, held batch isolation, negative days until expiry) | backend/app/inventory/service.py, backend/tests/test_inventory_service.py, backend/tests/test_inventory_alerts.py, spec-pack/memory.md | 136 passed (PostgreSQL), 131 passed / 2 skipped (SQLite) | Next: T-044 / T-050
2026-10-28 | T-042, T-045 | Expiry & low-stock alert query endpoints (/alerts/expiry, /alerts/low-stock) with lookahead, threshold, warehouse filtering, and RBAC; manual adjustments endpoint (/adjustments) with reason validation and audit log; multithreaded PostgreSQL concurrency test suite for B4 single-unit race, multi-batch parallel allocation, atomic CAS cancel vs dispatch, and held-batch isolation; jsonable_encoder fix in validation_error handler | backend/app/inventory/*, backend/app/main.py, backend/app/core/errors.py, backend/tests/test_inventory_alerts.py, backend/tests/test_concurrency.py | 133 passed (live PostgreSQL) | Next: T-044 / T-050
2026-10-28 | T-041, T-043 Review | Review hardening: concurrency race-condition protection (SQL expression reserved update + IntegrityError/StaleDataError catch), variant mismatch check, negative min_shelf_days check, inactive warehouse and cancelled order checks, exception hierarchy cleanup, idempotent receive_stock on reference_id, 6 new test scenarios | backend/app/inventory/service.py, backend/tests/test_inventory_service.py, spec-pack/memory.md | 114 passed | Next: T-042 / T-045
2026-10-28 | T-041, T-043 | Transactional FEFO inventory service: reserve_stock_fefo with deterministic locking and multi-batch allocation, cancel_reservation and dispatch_reservation with atomic CAS (B1, B6), adjust_stock with reason checks and audit log (B5, T-043), receive_stock helper, comprehensive 21 unit & invariant tests | backend/app/inventory/service.py, backend/tests/test_inventory_service.py, backend/app/audit/service.py | 108 passed | Next: T-042 / T-045
2026-10-28 | T-040 Step 1 | FEFO Inventory models & migration: Warehouse, ProductionBatch, Order, OrderItem, InventoryBalance, StockReservation, StockMovement with CHECK constraints, unique operation_key idempotency, Alembic migration 0003, warehouse seed script | backend/app/inventory/models.py, backend/alembic/versions/0003_fefo_inventory.py, backend/scripts/seed_inventory.py, backend/tests/test_inventory_models.py | 87 passed | Next: T-041 FEFO service
2026-10-28 | Review fixes | Pinned bcrypt==4.0.1, dropped passlib/python-jose for bcrypt+PyJWT, hardened JWT secret (reject default in non-local), fixed rate-limiter X-Forwarded-For bypass (TRUST_PROXY_HEADERS), deactivated users treated as anonymous, consolidated to 6 roles (removed factory_manager/warehouse_manager aliases), added CI PostgreSQL service + migration test, removed zip from repo, removed pydantic duplicate | 14 files | 81 passed | Next: T-040 inventory
2026-10-28 | T-022, T-025 | Auth hardening: RateLimiter on /login (HTTP 429), require_roles dependency with alias expansion, deactivated user rejection, owner dealer/distributor account creation, and 40-case permission matrix | backend/app/core/rate_limit.py, backend/app/auth/dependencies.py, backend/tests/test_permissions.py | 74 passed | Next: T-030
2026-10-28 | T-023 | Catalog module: categories, products, variants, price lists (MRP, DEALER, DISTRIBUTOR), role-based price resolver, price change audit logging, seed script, Alembic migration 0002 | backend/app/catalog/*, alembic/versions/0002_create_catalog_and_price_lists.py, backend/scripts/seed_catalog.py, backend/tests/test_catalog.py | 30 passed | Next: T-025 / T-030
2026-10-28 | T-020, T-021, T-022 | Initialized backend, auth models (User, Role), JWT auth, RBAC dependencies, audit logging, Alembic migration, role seed script | backend/app/auth/*, backend/app/audit/*, alembic/versions/0001_initial_auth_and_audit.py, backend/tests/test_auth.py | 19 passed | Next: T-023
YYYY-MM-DD | task id | what changed | files | tests | follow-ups
```
