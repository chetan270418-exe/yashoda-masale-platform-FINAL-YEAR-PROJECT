# Product Requirements Document (PRD)
**Product:** Yashoda SpiceChain AI  |  **Type:** Final-year sponsored project (Diploma, Computer Engineering)  |  **Version:** 1.0

## 1. Overview
A web-first platform that records and controls the path from raw-material receipt, to factory production and quality release, to warehouse stock, to dealer/distributor and consumer orders, with QR batch traceability and a business-focused AI layer.

Factory work is done by people. The software records it and enforces rules. It does not control machines.

## 2. Problem
Small spice businesses keep products, batches, stock, expiry dates and orders in separate spreadsheets, paper and chats. This causes overselling, expired stock, no single view, slow reordering and no traceability.

## 3. Goals and non-goals
**Goals**
- G1. One connected record from raw material to delivery.
- G2. Stock that is always consistent (no overselling, no dispatch of held/expired batches).
- G3. Server-enforced roles and prices.
- G4. QR page that shows recorded batch events.
- G5. AI that is measured against a baseline and approved by a human.
- G6. A reproducible, deployed prototype with full documentation.

**Non-goals**
- Controlling factory machinery.
- Claiming a pack is genuine from a photo or QR alone.
- Real payment, WhatsApp or ERP integration unless separately approved.
- Claiming real-world revenue gains from synthetic-data experiments.

## 4. Users and roles
| Role | Main needs |
|---|---|
| Owner / Super Admin | Users, permissions, price lists, business metrics, audit log, AI recommendations |
| Factory Manager | Raw materials, production plans, batches, quality decisions |
| Warehouse Manager | Stock, movements, expiry alerts, picking, packing, dispatch, returns |
| Distributor | Distributor prices, bulk orders, assigned dealers, invoices |
| Dealer / Retailer | Dealer prices, repeat orders, own invoices, reorder suggestions |
| Consumer | Browse, cart, order, track, review, scan QR |

## 5. Scope
**Must (core)**: auth and roles, catalogue and prices, suppliers and raw materials, production batches and quality, inventory ledger, FEFO, reservations, orders (B2B and consumer), invoices, shipments, QR traceability, dashboards, audit log.
**Should (AI)**: demand forecast vs baseline, reorder and expiry-risk recommendations, RAG assistant, dealer reorder draft with approval, restricted natural-language analytics.
**Optional**: Flutter app, Telegram/WhatsApp ordering, Hindi/Marathi voice, vision inspection prototype, FSSAI label draft generator, ONDC.

## 6. Functional requirements
IDs are used in tests and the traceability matrix.

### Authentication and access
- FR-AUTH-01 Login, logout, token refresh.
- FR-AUTH-02 Password reset by email link.
- FR-AUTH-03 Every protected endpoint checks role AND record ownership.
- FR-AUTH-04 Public sign-up creates Consumer only. Staff accounts are created by the Owner.
- FR-AUTH-05 Role changes and price changes are written to the audit log.

### Catalogue and pricing
- FR-CAT-01 Products with categories, variants (pack sizes), images, active flag.
- FR-CAT-02 Price lists: MRP, dealer price, distributor price.
- FR-CAT-03 The server returns only the price the logged-in role may see.
- FR-CAT-04 Order items store a price snapshot.~

### Procurement and production
- FR-PRD-01 Suppliers, raw materials, receipts and lots.
- FR-PRD-02 Production plans and production batches with unique batch codes.
- FR-PRD-03 Stages recorded (cleaning, grinding, blending, packaging) with actor and time.
- FR-PRD-04 Quality checks with configurable parameters and limits (values come from the sponsor; demo values labelled).
- FR-PRD-05 Batch decision: Released, On hold, Rejected. Only Released stock becomes sellable.

### Inventory and warehouse
- FR-INV-01 Stock is tracked by product, batch and warehouse.
- FR-INV-02 Every change is a stock movement (receipt, output, reserve, release, dispatch, return, adjustment).
- FR-INV-03 Reservations are transactional. No overselling under concurrent orders.
- FR-INV-04 FEFO: earliest-expiring eligible batch is picked first. Expired, held or rejected batches are excluded.
- FR-INV-05 Low-stock and expiry alerts.
- FR-INV-06 Manual adjustments need a reason and are audited.

### Orders and fulfilment
- FR-ORD-01 Consumer cart and checkout (simulated payment).
- FR-ORD-02 B2B bulk orders and one-click repeat order.
- FR-ORD-03 Order lifecycle: Placed, Confirmed, Reserved, Packed, Dispatched, Delivered, Cancelled, Returned. Invalid transitions are rejected.
- FR-ORD-04 Cancellation releases the reservation exactly once.
- FR-ORD-05 Returned goods go to a quality/warehouse decision before becoming sellable.
- FR-ORD-06 Invoices and payment status records.
- FR-ORD-07 Shipment events and order tracking.

### Traceability
- FR-QR-01 Each batch has a public token and QR code.
- FR-QR-02 Public page shows approved fields and recorded events only.
- FR-QR-03 Unknown token shows "No record found". It never shows "Verified".
- FR-QR-04 Scans are logged. Unusual scan patterns are flagged for review.

### Dashboards and analytics
- FR-DSH-01 Role-specific dashboards.
- FR-DSH-02 Sales by product, dealer, region and month, with CSV export.
- FR-DSH-03 Slow-moving products report.

### AI
- FR-AI-01 Forecast per product, compared with a baseline on a chronological test set (MAE, RMSE, and MAPE where valid).
- FR-AI-02 Reorder recommendation showing its inputs (forecast, lead time, safety stock, available, reserved).
- FR-AI-03 Expiry-risk flags using transparent rules first.
- FR-AI-04 RAG assistant answers only from approved content, and says so when no source exists.
- FR-AI-05 Dealer reorder agent builds a DRAFT. The backend validates it. A human approves.
- FR-AI-06 Natural-language analytics maps to whitelisted, permission-checked functions.
- FR-AI-07 Every AI tool call is logged. Model output is treated as untrusted.
- FR-AI-08 LLM provider sits behind an adapter. Keys stay on the server.

## 7. Non-functional requirements
- **Security:** follow OWASP ASVS and Top 10 practices. HTTPS in deployment. Secrets only in environment/secret manager.
- **Data integrity:** database transactions, foreign keys, CHECK constraints, decimal money.
- **Performance (prototype targets):** common list/read endpoints under 500 ms at demo load. Targets are measured and reported, not promised.
- **Usability:** responsive layout, keyboard accessible, WCAG AA contrast targets.
- **Maintainability:** modular monolith, typed code, linting, CI.
- **Reproducibility:** `docker compose up` starts the system from a clean machine with seed data.
- **Honesty of data:** all demo data is synthetic and labelled in the UI and report.

## 8. Acceptance criteria
1. All six roles can reach only their permitted screens and records (tested).
2. A held batch cannot be dispatched (tested).
3. Two orders competing for the last stock never oversell (tested).
4. FEFO picks the earlier-expiring eligible batch (tested).
5. QR page shows correct recorded events, and rejects unknown tokens (tested).
6. Forecast beats or is honestly compared with a baseline, with metrics reported.
7. AI assistant refuses when no approved source exists (tested).
8. Dealer agent cannot submit an order without approval (tested).
9. A new machine can run the system from the setup guide.

## 9. Assumptions and constraints
- No access to real sponsor records is assumed. If the sponsor shares real data, replace synthetic data and re-evaluate.
- Quality parameters and limits come from the sponsor. Until then they are placeholders.
- Payment is simulated. No card data is stored.
- Hosted LLM cost and rate limits depend on the chosen provider.

## 10. Risks
| Risk | Mitigation |
|---|---|
| Scope too large | Core first, AI second, optional last. Freeze features in weeks 23–24 |
| Integration left late | Weekly integration demo. Early end-to-end test |
| AI results look fake | Baseline comparison, held-out test set, labelled synthetic data |
| One person owns everything | Pull-request review. Mock viva each week |
| LLM/API limits | Adapter pattern, caching, small demo set, fallback answers |

## 11. Open questions
- Which real quality parameters and limits will the sponsor provide?
- How many warehouses and dealers should the demo show?
- Which hosting will the college allow?
- Which LLM provider and key will be used?
