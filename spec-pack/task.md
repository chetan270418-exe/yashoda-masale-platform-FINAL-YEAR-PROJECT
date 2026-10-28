# Task List (24 weeks)

Legend: [ ] todo, [x] done. Owners: M1 Frontend/UX, M2 Backend/DB/Security, M3 AI/ML, M4 Integration/QA/Deploy. Change owners to fit your team.
Rule: every task ends with tests, a reviewed PR and a `memory.md` update.

## M0 — Requirements (Weeks 1–2)
- [ ] T-001 (All) Meet sponsor, record workflow, products, price rules, quality checks, dealers. Get sponsor letter.
- [ ] T-002 (M4) Write PRD and SRS draft from `prd.md`.
- [ ] T-003 (M3) Write synthetic-data assumptions (seasonality, festivals, noise, seed).
- [ ] T-004 (All) User stories and acceptance criteria per role. Create backlog board.
**Done when:** PRD/SRS approved by guide; backlog on a board.

## M1 — Design (Weeks 3–4)
- [ ] T-010 (M1) Figma tokens, components and the key flows from `ui-pages.md`.
- [ ] T-011 (M2) ER diagram, data dictionary, table list.
- [ ] T-012 (M2) API contract (OpenAPI skeleton) for auth, products, orders, inventory.
- [ ] T-013 (M4) Architecture diagram, DFD level 0 and 1, use-case diagram.
**Done when:** design review passed; API contract frozen for v1.

## M2 — Foundation (Weeks 5–6)
- [x] T-020 (M4) Repo, folders, Docker compose, CI (lint, test, build), `.env.example`.
- [x] T-021 (M2) Database migrations, user and role tables, seed script skeleton.
- [x] T-022 (M2) Login, tokens, password reset, role/permission dependencies. Audit log writer.
- [x] T-023 (M2) Catalogue: categories, products, variants, price lists with role-based price resolver.
- [ ] T-024 (M1) App shell, login, role-based navigation, design tokens in code.
- [x] T-025 (M2/M4) Permission tests for every role on every route (matrix test).
**Done when:** each role logs in and sees only its menu; dealer cannot read owner endpoints.

## M3 — Factory records (Weeks 7–9)
- [ ] T-030 (M2) Suppliers, raw materials, lots, receipts.
- [ ] T-031 (M2) Production plans, batches, stages, batch status machine.
- [ ] T-032 (M2) Quality parameters, quality checks, release/hold/reject.
- [ ] T-033 (M1) Factory screens: materials, plans, batch detail, quality form.
- [ ] T-034 (M4) Tests: only released batches create sellable stock.
**Done when:** a batch goes from plan to Released and creates stock.

## M4 — Inventory (Weeks 10–12)
- [ ] T-040 (M2) Warehouses, balances, stock movements ledger.
- [ ] T-041 (M2) FEFO selector and reservation service (transactional).
- [ ] T-042 (M2) Expiry and low-stock alert queries.
- [ ] T-043 (M2) Adjustments with reason and audit.
- [ ] T-044 (M1) Warehouse screens: inventory, ledger, expiry, adjustments.
- [ ] T-045 (M4) Concurrency test: two orders, one unit left. FEFO tests. Held-batch tests.
**Done when:** no overselling in the concurrency test; FEFO tests pass.

## M5 — Ordering (Weeks 13–15)
- [ ] T-050 (M2) Orders, items with price snapshot, state machine, idempotency.
- [ ] T-051 (M2) Reserve, pack, dispatch, cancel, return endpoints.
- [ ] T-052 (M2) Invoices, payment status records, shipments and events.
- [ ] T-053 (M1) Store: catalogue, product, cart, checkout, my orders, tracking.
- [ ] T-054 (M1) Partner portal: dashboard, bulk builder, repeat order, invoices.
- [ ] T-055 (M4) End-to-end test: order to delivery with stock checks.
**Done when:** consumer and dealer orders complete end to end with consistent stock.

## M6 — QR and dashboards (Week 16)
- [ ] T-060 (M2) Batch tokens, batch events, public traceability endpoint, scan log.
- [ ] T-061 (M1) QR page, QR print, owner/warehouse/factory dashboards.
- [ ] T-062 (M2) Sales analytics endpoints and CSV export.
- [ ] T-063 (M4) Tests: unknown token shows no record; private fields never exposed.
**Done when:** scanning a QR shows the correct recorded events.

## M7 — Forecasting and reorder (Weeks 17–19)
- [ ] T-070 (M3) Synthetic data generator (reproducible, labelled).
- [ ] T-071 (M3) Baseline and candidate models, chronological split, metrics saved.
- [ ] T-072 (M3) Reorder recommendations with visible inputs. Expiry-risk rules.
- [ ] T-073 (M1) Forecast, evaluation and recommendations screens.
- [ ] T-074 (M4) Tests for reorder maths and metrics code.
**Done when:** evaluation report generated with baseline vs model.

## M8 — AI assistant and agent (Weeks 20–21)
- [ ] T-080 (M3) LLM adapter, approved knowledge base ingestion, retrieval.
- [ ] T-081 (M3) RAG answers with sources and "no source" fallback.
- [ ] T-082 (M3) Tool whitelist, natural-language analytics, tool run logging.
- [ ] T-083 (M3/M2) Dealer reorder draft and approve endpoints.
- [ ] T-084 (M1) Chat UI, draft review screen, NL query screen.
- [ ] T-085 (M4) Tests: no-source refusal, permission-aware tools, draft not submitted without approval, prompt-injection attempts.
**Done when:** the demo AI scenarios work and the refusal tests pass.

## M9 — Deployment (Week 22)
- [ ] T-090 (M4) Staging deploy, HTTPS, secrets, backups, restore test.
- [ ] T-091 (M4) Logging, health check, basic monitoring.
- [ ] T-092 (M4) Setup guide tested on a clean machine.

## M10 — Hardening (Week 23)
- [ ] T-100 (All) Bug fixes, security checklist (OWASP), accessibility pass.
- [ ] T-101 (M4) Test report, requirements traceability matrix.
- [ ] T-102 (All) Feature freeze.

## M11 — Final (Week 24)
- [ ] T-110 (All) Final report, PPT, demo video, user manual.
- [ ] T-111 (All) Rehearse the demo story and mock viva (every member explains every module).
- [ ] T-112 (M4) Release tag, README, change log.

## Always on (every week)
- [ ] Weekly integration demo of a working increment.
- [ ] Pull request reviews done within 24 hours.
- [ ] `memory.md` updated.
- [ ] Mock viva question round (10 minutes).

## Optional (only if the core is finished)
- [ ] Flutter consumer app using the same APIs.
- [ ] Telegram bot for dealer reorder.
- [ ] FSSAI label draft generator (not a compliance claim).
- [ ] Vision inspection prototype on your own labelled photos.
- [ ] Hindi/Marathi typed support, then voice.
