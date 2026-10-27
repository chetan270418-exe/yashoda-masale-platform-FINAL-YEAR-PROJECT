# Yashoda Masale --- Implementation Roadmap

This roadmap sequences work by dependency. Protect the core workflow
before optional features.

## Phase 1 --- Shared foundation

-   Confirm all members can clone and run the repository.
-   Document environment variables; keep secrets local.
-   Start PostgreSQL with Docker Compose.
-   Run the FastAPI health endpoint.
-   Start Next.js and connect it to the backend.
-   Add initial migrations and synthetic seed data.

**Acceptance check:** A teammate can follow the README on a clean
environment and see the frontend receive a successful API response.

## Phase 2 --- Product catalogue and access control

-   Products, categories, pack-size variants and price lists.
-   Authentication and role/record-level authorization.
-   Public catalogue and internal product management.

**Acceptance check:** Product data comes from PostgreSQL through
FastAPI; protected endpoints reject unauthorized users.

## Phase 3 --- Procurement, production and quality

-   Suppliers, raw materials, receipts and material lots.
-   Production plans and unique batch codes.
-   Production-stage and quality-check records.
-   Batch states: Draft, In Production, Quality Pending, Released, On
    Hold or Rejected.

**Acceptance check:** Only Released batches contribute to sellable
stock. Sponsor-defined quality limits must be used; demo placeholders
must be labelled.

## Phase 4 --- Inventory integrity

-   Inventory by product, batch and warehouse.
-   Stock movement records, reservations and releases.
-   Low-stock and expiry alerts.
-   FEFO selection for eligible stock.

**Acceptance checks:** Held, rejected and expired batches cannot be
dispatched; simultaneous orders cannot oversell; an active reservation
is released exactly once on cancellation.

## Phase 5 --- Orders and fulfilment

-   Consumer cart and checkout with simulated payment.
-   B2B bulk and repeat orders.
-   Order lifecycle, invoices, shipment events and returns.
-   Return inspection before returned goods become sellable.

**Acceptance check:** One order can be followed through reservation,
packing, dispatch and delivery. Invalid state transitions are rejected.

## Phase 6 --- QR traceability and reports

-   Unique QR token for each batch.
-   Public page showing approved recorded events.
-   Unknown token returns "No record found".
-   Role-specific dashboards, sales reports, slow-moving products and
    audit history.

**Acceptance check:** QR displays correct recorded events without
exposing private data. QR alone must not be claimed as proof of physical
authenticity.

## Phase 7 --- AI decision support

### Forecasting

-   Build a simple baseline first.
-   Split data chronologically to avoid future-data leakage.
-   Compare candidate models against baseline.
-   Report MAE, RMSE and MAPE where valid.
-   Label synthetic-data experiments honestly.

### Recommendations

-   Show inputs behind reorder suggestions.
-   Respect stock eligibility and reservation rules.
-   Start expiry-risk flags with transparent rules.

### Grounded assistant

-   Retrieve approved documents or permission-checked records.
-   Show sources where possible.
-   Say when approved information is unavailable.

### Dealer reorder agent

-   Read only the authorized dealer's history.
-   Check current prices and eligible stock through backend tools.
-   Create a draft only; require human approval.
-   Log tool calls.

**Acceptance check:** AI does not invent missing business data, bypass
permissions, run arbitrary SQL or submit an order without approval.

## Phase 8 --- Testing and demo

-   Unit, integration, permission and end-to-end tests.
-   Responsive and accessibility checks.
-   Docker-based reproducible setup and CI checks.
-   Deployment configuration, secrets management and tested database
    restore.
-   User manual, diagrams, API documentation, AI evaluation report and
    presentation.

**Acceptance check:** A clean environment can run the app and the
complete demo workflow succeeds.

## Recommended first-week targets

1.  Read and agree on the PRD, architecture and rules.
2.  Run the repository setup on all four computers.
3.  Agree on API conventions and initial database schema.
4.  Connect a frontend page to the backend health endpoint.
5.  Implement a database-backed product listing.
6.  Open pull requests and review changes.
7.  Demonstrate the connected slice to the team.

## Defer until the core works

Blockchain, packaging image screening, 3D-heavy UI, a separate mobile
app, voice assistant, WhatsApp/ERP integration and real payment
integration.
