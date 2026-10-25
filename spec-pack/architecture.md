# Architecture

## 1. Style
A **modular monolith**: one backend application split into clear modules, one database, one frontend. No microservices. Add Redis or queues only if a real need appears and the team can explain it.

## 2. System overview
```
Browser (Next.js + React + TypeScript)
        |  HTTPS, JSON
        v
FastAPI backend  (/api/v1)
  - auth, permissions, validation
  - business modules (catalog, production, inventory, orders, traceability, analytics)
  - AI module (forecast, RAG, agent tools, LLM adapter)
        |                         |
        v                         v
PostgreSQL (source of truth)   Object storage (images, PDFs)
  + pgvector (only if needed)
        |
        v
Hosted LLM API (server-side only, behind an adapter)
```

## 3. Layers
1. **Presentation:** Next.js pages, forms, charts, QR page.
2. **Application:** FastAPI routers, request validation, authorization, workflow rules.
3. **Data:** PostgreSQL tables, foreign keys, constraints, transactions, audit logs.
4. **AI:** forecasting, retrieval of approved content, whitelisted tools, LLM adapter.
5. **Platform:** Docker, CI/CD, secrets, backups, HTTPS.

## 4. Backend module layout
```
backend/
  app/
    main.py
    core/            # config, security, db session, errors, logging
    auth/            # login, tokens, permissions, dependencies
    catalog/         # products, variants, price lists
    procurement/     # suppliers, raw materials, lots
    production/      # plans, batches, stages, quality checks
    inventory/       # balances, movements, reservations, FEFO
    orders/          # cart, orders, invoices, shipments, returns
    traceability/    # QR tokens, batch events, scan log
    analytics/       # sales reports, slow movers
    ai/
      llm/           # adapter interface + provider implementations
      forecast/      # baseline + candidate models + evaluation
      rag/           # ingestion, retrieval, answer builder
      tools/         # whitelisted business tools used by the agent
    audit/           # audit log writer
  alembic/           # migrations
  tests/             # unit, integration, permission tests
  scripts/           # synthetic data generator, seed
```
Each module has: `router.py`, `schemas.py`, `service.py`, `repository.py`, `models.py`.
- Router: HTTP only.
- Service: business rules.
- Repository: database access.
Modules may call another module's **service**, never its repository or tables.

## 5. Frontend layout (Next.js App Router)
```
frontend/
  app/
    (public)/        # home, catalogue, product, cart, checkout, login, qr/[token]
    (consumer)/      # my orders, addresses, reviews
    (partner)/       # dealer + distributor portal
    (warehouse)/     # inventory, dispatch, returns
    (factory)/       # materials, plans, batches, quality
    (owner)/         # dashboard, users, prices, reports, AI, audit
  components/        # ui primitives, tables, forms, charts
  lib/               # api client, auth helpers, types
```
Route groups are guarded by role. The server still checks every call.

## 6. Core data rules
- Money: fixed-precision decimal. Never float.
- Stock: current balance is derived from, and checked against, an append-only `stock_movements` ledger.
- Price: the price used on an order item is copied into the item (snapshot).
- Batch states: `Draft -> InProduction -> QualityPending -> Released | OnHold | Rejected`.
- Order states: `Placed -> Confirmed -> Reserved -> Packed -> Dispatched -> Delivered`, plus `Cancelled` and `Returned`.
- All state changes go through service functions that reject illegal transitions.

## 7. Concurrency and consistency
- Reservation runs in one transaction: lock the candidate batch balance rows (`SELECT ... FOR UPDATE`), check available = on_hand - reserved, then insert reservation and movement.
- `CHECK (on_hand >= 0)` and `CHECK (reserved >= 0)` on balances as a last line of defence.
- Order creation accepts an idempotency key so a double-click does not create two orders.
- Cancellation releases the reservation only if its status is still `Active`.

## 8. Security architecture
- Passwords hashed with a modern algorithm (argon2 or bcrypt).
- Short-lived access tokens, refresh handling, secure cookie or header strategy decided once and documented.
- Authorization = role permission + record-level check (e.g. dealer sees own orders only).
- Input validation with Pydantic. Parameterised queries only.
- File uploads: type and size checks, random filenames.
- Rate limiting on login and AI endpoints.
- Secrets in environment variables or a secret manager. Never in the repo or the frontend.
- Public QR page returns an allow-listed set of fields only.
- Audit log for price changes, role changes, stock adjustments, approvals.

## 9. AI architecture
```
User question
   |
   v
API (auth + permission check)
   |
   +--> RAG: retrieve approved docs -> build prompt -> LLM -> answer + sources
   |        (if no source found -> "approved information not available")
   |
   +--> Agent/NL analytics: LLM proposes a tool call
            -> server validates arguments + user permission
            -> runs a whitelisted function (read-only, or "create draft")
            -> returns result; human approves any write
```
- **LLM adapter:** one interface (`generate`, `embed`). Provider and model chosen by environment settings.
- **Tool whitelist:** e.g. `get_sales_summary`, `get_slow_movers`, `get_dealer_history`, `check_stock`, `create_order_draft`. No free SQL from the model.
- **Logging:** every tool run stored in `ai_tool_runs` with user, inputs, outputs, time.
- **Forecast:** baseline (seasonal naive or moving average) vs candidate; chronological split; metrics saved in `forecast_evaluations`.

## 10. Deployment
```
docker compose:
  web   (Next.js)
  api   (FastAPI)
  db    (PostgreSQL)
  (optional) proxy (Caddy/Nginx for HTTPS)
```
- Environments: local, staging, demo.
- CI (GitHub Actions): lint, type-check, tests, build images.
- Backups: scheduled database dump, and one tested restore.
- Config via `.env` files that are never committed. Provide `.env.example`.

## 11. Observability
Structured JSON logs with request id. Error tracking. Health endpoint `/health`. Basic metrics (request count, latency, AI call count, AI failures).

## 12. Architecture decisions
See `memory.md` for the decision log (ADR-style).
