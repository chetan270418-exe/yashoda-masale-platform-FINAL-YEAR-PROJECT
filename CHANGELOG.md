# Changelog
All notable changes to the **Yashoda SpiceChain** platform will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-10-30

### Added
- **Ordering Service Hardening & Invoices (T-052):**
  - Added ORM models `Invoice`, `Shipment`, and `ShipmentEvent` with full cascading relationships and constraints.
  - Added simulated payment processing (`POST /api/v1/orders/invoices/{id}/pay`) supporting `simulated_upi`, `simulated_netbanking`, and `simulated_cash`.
  - Added automated tax invoice and shipment generation on order dispatch (`POST /api/v1/orders/{id}/dispatch`).
  - Added logistics milestone tracking endpoints (`PATCH /api/v1/orders/shipments/{id}/status`, `POST /api/v1/orders/shipments/{id}/events`).
- **Alembic Migration 0009:**
  - Tightened `ck_orders_status` constraint removing legacy `open`, `shipped`, `partially_shipped` values.
  - Replaced global `idempotency_key` unique index with composite constraint `uq_order_customer_idempotency` on `(customer_id, idempotency_key)`.
  - Created tables `invoices`, `shipments`, and `shipment_events`.
- **Database Backup & Disaster Recovery (T-090):**
  - Added `backend/scripts/backup_db.py` supporting online SQLite backups and PostgreSQL dumps with gzip compression and SHA-256 checksums.
  - Added `backend/scripts/restore_db.py` with mandatory SHA-256 integrity validation and atomic target replacement.
- **Health Checks & Telemetry Probes (T-091):**
  - Added `/api/v1/health/live` (liveness probe).
  - Added `/api/v1/health/ready` (readiness probe verifying database connection pool).
  - Added `/api/v1/health/detailed` (system telemetry, uptime, database status).
- **Staging Deployment Configuration (T-090 / T-092):**
  - Added `docker-compose.staging.yml` with PostgreSQL 16, multi-worker Uvicorn, and Caddy reverse proxy with automatic TLS.
  - Added `docs/deployment/DEPLOYMENT_GUIDE.md` detailing clean machine bootstrap, environment secrets, and operational runbooks.
- **Security Audit & Automated Hardening (T-100):**
  - Added `backend/tests/test_security_hardening.py` testing SQL injection immunity, JWT tampering rejection, expired token rejection, and BOLA isolation.
  - Added `docs/security/OWASP_SECURITY_AUDIT.md` mapping defenses against OWASP API Security Top 10 (2023).
- **Requirements Traceability & Test Execution (T-101):**
  - Added `docs/testing/REQUIREMENTS_TRACEABILITY_MATRIX.md` mapping invariants B1–B12 and all tasks to test files.
  - Added `docs/testing/TEST_EXECUTION_REPORT.md` documenting 444 automated test cases with 100% pass rate.
- **Project Documentation & Viva Preparation (T-110 / T-111):**
  - Added `docs/user-manual/USER_MANUAL.md` with step-by-step role guides for all 6 personas.
  - Added `docs/project-report/FINAL_PROJECT_REPORT.md` synthesizing the technical final year project report.
  - Added `docs/viva/VIVA_PREPARATION_GUIDE.md` covering architecture decisions D-001 through D-035, FEFO math, and examiner Q&A.

### Changed
- **Pessimistic Row-Level Locking:** Added `.with_for_update(of=Order)` across all order state mutations (`reserve`, `pack`, `dispatch`, `cancel`, `return`), eliminating concurrent double-allocation race conditions.
- **Status Endpoint Restriction:** Restricted `PATCH /api/v1/orders/{id}/status` strictly to `delivered`. All other transitions return HTTP 400 with `USE_DEDICATED_ENDPOINT`.
- **Atomic Multi-Line Transactions:** Threaded `commit=False` into child inventory operations so multi-line order reservation fails atomically if any line is unfulfillable.
- **Order Limits:** Enforced maximum 50 line items per order and maximum 10,000 units per line item.
- **Normalized Status Names:** Replaced legacy status `"shipped"` with canonical `"dispatched"` across all test fixtures and AI query filters.

### Removed
- Removed legacy unused statuses `open`, `shipped`, `partially_shipped` from models and database check constraints.
- Removed SQLite-only `rowid` ordering hack from price list lookup in `orders/service.py`.
- Removed loose temporary zip and text files from repository root.
