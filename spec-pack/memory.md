# Project Memory

Read this at the start of every session. Update it at the end. Keep it short and factual.

## 1. Project in one paragraph
**Yashoda SpiceChain AI** is a web-first platform for the spice business Yashoda Masale. It records raw-material receipt, production batches, quality release, warehouse stock with FEFO, dealer/distributor and consumer orders, and QR batch traceability. A business AI layer adds forecasting, reorder and expiry-risk suggestions, a RAG assistant, a dealer reorder agent and natural-language analytics. It is a Diploma (Computer Engineering) final-year project with a 4-member team and a 24-week plan.

## 2. Status
- Current milestone: M2 (Foundation)
- Last session summary: Completed T-020, T-021, T-022, and T-023 (backend foundation, auth & RBAC, audit log, catalog with role-based price resolver, migrations, and 30 passing tests).
- Next task: T-025 (Permission matrix test) / T-030 (Factory & Procurement records).

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
Owner/Super Admin, Factory Manager, Warehouse Manager, Distributor, Dealer/Retailer, Consumer.

## 6. Known issues and gotchas
- (none yet) Add entries like: "Reservation test fails under concurrency when X."

## 7. Open questions
- Real quality parameters and limits from the sponsor?
- Hosting allowed by college?
- LLM provider and key?
- Number of dealers/warehouses in the demo?

## 8. Session log (newest first)
```
2026-10-28 | T-023 | Catalog module: categories, products, variants, price lists (MRP, DEALER, DISTRIBUTOR), role-based price resolver, price change audit logging, seed script, Alembic migration 0002 | backend/app/catalog/*, alembic/versions/0002_create_catalog_and_price_lists.py, backend/scripts/seed_catalog.py, backend/tests/test_catalog.py | 30 passed | Next: T-025 / T-030
2026-10-28 | T-020, T-021, T-022 | Initialized backend, auth models (User, Role), JWT auth, RBAC dependencies, audit logging, Alembic migration, role seed script | backend/app/auth/*, backend/app/audit/*, alembic/versions/0001_initial_auth_and_audit.py, backend/tests/test_auth.py | 19 passed | Next: T-023
YYYY-MM-DD | task id | what changed | files | tests | follow-ups
```
