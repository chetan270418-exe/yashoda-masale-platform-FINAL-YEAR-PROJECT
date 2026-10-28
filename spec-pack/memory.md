# Project Memory

Read this at the start of every session. Update it at the end. Keep it short and factual.

## 1. Project in one paragraph
**Yashoda SpiceChain AI** is a web-first platform for the spice business Yashoda Masale. It records raw-material receipt, production batches, quality release, warehouse stock with FEFO, dealer/distributor and consumer orders, and QR batch traceability. A business AI layer adds forecasting, reorder and expiry-risk suggestions, a RAG assistant, a dealer reorder agent and natural-language analytics. It is a Diploma (Computer Engineering) final-year project with a 4-member team and a 24-week plan.

## 2. Status
- Current milestone: M2 (Foundation complete — all review fixes applied)
- Last session summary: Fixed all 8 review issues (pinned bcrypt, dropped passlib/python-jose for bcrypt+PyJWT, hardened JWT secret validation, fixed rate-limiter proxy bypass, deactivated-user anonymous fallback, consolidated to 6 roles, added CI PostgreSQL + migration test, cleaned repo). 81 tests passing.
- Next task: M4 Inventory — T-040 (Warehouses, balances, stock movements ledger). PostgreSQL CI is ready.
- Note: T-023 (catalog) was built by Chetan but is Srushti's lane. She should take M3 factory records (T-030–T-032) instead.

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
2026-10-28 | Review fixes | Pinned bcrypt==4.0.1, dropped passlib/python-jose for bcrypt+PyJWT, hardened JWT secret (reject default in non-local), fixed rate-limiter X-Forwarded-For bypass (TRUST_PROXY_HEADERS), deactivated users treated as anonymous, consolidated to 6 roles (removed factory_manager/warehouse_manager aliases), added CI PostgreSQL service + migration test, removed zip from repo, removed pydantic duplicate | 14 files | 81 passed | Next: T-040 inventory
2026-10-28 | T-022, T-025 | Auth hardening: RateLimiter on /login (HTTP 429), require_roles dependency with alias expansion, deactivated user rejection, owner dealer/distributor account creation, and 40-case permission matrix | backend/app/core/rate_limit.py, backend/app/auth/dependencies.py, backend/tests/test_permissions.py | 74 passed | Next: T-030
2026-10-28 | T-023 | Catalog module: categories, products, variants, price lists (MRP, DEALER, DISTRIBUTOR), role-based price resolver, price change audit logging, seed script, Alembic migration 0002 | backend/app/catalog/*, alembic/versions/0002_create_catalog_and_price_lists.py, backend/scripts/seed_catalog.py, backend/tests/test_catalog.py | 30 passed | Next: T-025 / T-030
2026-10-28 | T-020, T-021, T-022 | Initialized backend, auth models (User, Role), JWT auth, RBAC dependencies, audit logging, Alembic migration, role seed script | backend/app/auth/*, backend/app/audit/*, alembic/versions/0001_initial_auth_and_audit.py, backend/tests/test_auth.py | 19 passed | Next: T-023
YYYY-MM-DD | task id | what changed | files | tests | follow-ups
```
