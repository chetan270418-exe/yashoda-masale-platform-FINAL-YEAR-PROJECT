# Yashoda Masale --- Team Project Briefing

**Working name:** Yashoda SpiceChain AI\
**Repository:**
https://github.com/chetan270418-exe/yashoda-masale-platform-FINAL-YEAR-PROJECT

## What we are building

A web-first platform connecting the spice-business journey: raw-material
receipt → production records → quality decision → warehouse inventory →
dealer/consumer orders → dispatch/delivery → QR batch traceability.

People perform and approve factory work. The software records it and
enforces business rules; it does not control factory machinery or
manufacture spices using AI.

## Problem

Product, batch, inventory, expiry and order information can be spread
across paper records, spreadsheets and chats. This can cause
overselling, missed expiry risks, slow reordering and poor traceability.
The platform connects these records and helps the company make better
operational decisions.

## Users

-   **Owner/Admin:** users, permissions, price lists, reports and audit
    history.
-   **Factory Manager:** materials, production records, batches and
    quality decisions.
-   **Warehouse Manager:** stock movements, expiry, picking, packing,
    dispatch and returns.
-   **Distributor:** bulk orders, assigned dealers and invoices.
-   **Dealer/Retailer:** authorized prices, repeat orders and invoices.
-   **Consumer:** browse, order, track delivery and scan batch QR.

## Core modules

1.  Authentication and role/record-level permissions.
2.  Product catalogue, pack sizes and price lists.
3.  Suppliers, raw materials, receipts and material lots.
4.  Production batches and quality checks.
5.  Inventory ledger, reservations, expiry alerts and FEFO.
6.  Consumer/B2B orders, invoices, shipments and returns.
7.  QR batch traceability with approved public information.
8.  Role-specific dashboards, reports and audit logs.
9.  AI forecasting, recommendations, grounded assistant and
    human-approved dealer reorder drafts.

## Recommended stack

-   Frontend: Next.js + React + TypeScript.
-   Backend: Python + FastAPI.
-   Database: PostgreSQL.
-   AI/ML: Python, pandas and scikit-learn.
-   LLM: hosted model behind a provider-independent server-side adapter.
-   Optional retrieval: pgvector if semantic document search is needed.
-   Development: Docker Compose and GitHub Actions.

Start as a **modular monolith**: one frontend, one backend with clear
modules, and one PostgreSQL database. Do not begin with microservices.

## AI features

### Demand forecasting

Forecast product demand, compare with a simple baseline, use a
chronological test split, and report MAE/RMSE and MAPE where valid.
Never invent accuracy figures.

### Reorder recommendations

Explain recommendations using forecast demand, supplier lead time,
safety stock, available stock and reserved stock.

### Expiry-risk alerts

Begin with transparent rules. AI must not override stock eligibility
rules.

### Grounded business assistant

Answer only from approved company documents and permitted records. If no
source is available, say the information is unavailable.

### Dealer reorder agent

Use order history, current authorized prices and eligible stock to
prepare a draft. A human must approve it before submission.

### Restricted business analytics

Use approved, permission-checked backend functions. Never allow the LLM
to execute arbitrary SQL.

## Non-negotiable rules

-   Only released batches are eligible for sale/dispatch.
-   Held, rejected and expired batches must not be dispatched.
-   Inventory changes must be recorded as stock movements.
-   Reservations must be transactional to prevent overselling.
-   Protected APIs must check role and record ownership.
-   Public QR pages show only approved fields.
-   QR alone does not prove that a physical pack is genuine.
-   AI-generated orders remain drafts until approved by a human.
-   Use synthetic demo data until real sponsor data is authorized.
-   Clearly label synthetic data and placeholder quality limits.
-   Never commit API keys, passwords or `.env` files.

## First milestone

Get a reproducible setup running with a frontend page, FastAPI health
endpoint, PostgreSQL, migrations/seed data, and a frontend request to
the backend. Then build a product catalogue backed by the database.

## Existing project documents

All members should read `spec-pack/prd.md`, `spec-pack/architecture.md`
and `spec-pack/rules.md`. Discuss and update these documents together if
the team agrees to change scope.
