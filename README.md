# Yashoda SpiceChain — Enterprise Spice ERP & AI Platform
**Sponsor:** Yashoda Masale, Satara, Maharashtra  
**Domain:** Supply Chain Operations, Quality Traceability, Applied AI & Cloud Architecture  
**Version:** 1.0.0 (Production Ready) | **Test Suite:** 444 Tests (100% Passing)

---

## 1. System Overview

Yashoda SpiceChain is an enterprise supply chain, quality traceability, and demand forecasting platform customized for spice manufacturing. The backend is designed as a **Modular Monolith** using **Python 3.11 / FastAPI**, backed by **PostgreSQL 16** and **SQLAlchemy 2.0 ORM** with **Alembic Migrations (0001–0009)**.

### Core Business Invariants Enforced:
- **B1 (6-Role RBAC):** Strict privilege separation across Owner, Factory, Warehouse, Distributor, Dealer, Consumer.
- **B2 (Factory Gates):** Only batches with `released` quality decision generate sellable warehouse stock.
- **B3 (FEFO Allocation):** Inventory reservations sorted by `expiry_date ASC, created_at ASC`.
- **B4 (Non-Negative Stock):** Database constraints enforce `quantity >= 0` across all warehouse balances.
- **B5 (Append-Only Ledger):** Stock movements ledger is insert-only; updates and deletions are rejected.
- **B6 (Reservation Lifecycle):** Cancelling an order releases reserved stock back to available balances exactly once.
- **B7 (Order State Machine):** Dedicated lifecycle transitions (`pending -> reserved -> packed -> dispatched -> delivered`).
- **B8 (Price Snapshots):** Order items snapshot unit price and price list code at time of creation.
- **B9 (Role-Based Pricing):** Prices resolve automatically to Dealer, Distributor, or Retail MRP tiers.
- **B10 (Public Traceability):** Public QR batch scan exposes only transparency events; internal costs and margins are masked.
- **B11 (Human-in-the-Loop AI):** AI draft mutations require explicit human approval before order instantiation.
- **B12 (Exact Decimal Math):** All financial and inventory amounts use `Decimal` with fixed decimal places; floats are banned.

---

## 2. Repository Layout

```text
frontend/                  Next.js website and user interface (handled by frontend team)
backend/                   FastAPI modular monolith application
  app/
    auth/                  Authentication, user provisioning, 6-role RBAC, JWT
    catalog/               Categories, products, pack sizes, role-based price resolver
    procurement/           Suppliers, spice raw materials, lots, lab receipts
    production/            Production plans, batches, 5 stages, quality gates
    inventory/             Warehouses, balances, append-only ledger, FEFO allocation
    orders/                Orders, row locks, invoices, simulated payments, shipments
    traceability/          Public QR batch tokens, scan logging, timeline events
    analytics/             Sales aggregation, slow-movers queries, CSV export
    ai/                    RAG knowledge assistant, tool execution, order drafts
    api/v1/                API routers and health/telemetry probes
    core/                  Config, database session, errors, logging middleware
  alembic/versions/        Database migrations (0001 through 0009)
  scripts/                 Seed scripts, migration checks, backup & restore utilities
  tests/                   Comprehensive test suite (444 automated tests)
docs/
  deployment/              Deployment guide & staging runbook (DEPLOYMENT_GUIDE.md)
  security/                OWASP API Security Top 10 audit report (OWASP_SECURITY_AUDIT.md)
  testing/                 Requirements traceability matrix (RTM) & Test Execution Report
  user-manual/             Step-by-step role manual for all 6 personas (USER_MANUAL.md)
  viva/                    Mock viva defense & oral exam guide (VIVA_PREPARATION_GUIDE.md)
  project-report/          Academic final technical report, animated presentation, thesis
  synthetic-data/          Synthetic spice data generator and reproducible datasets
docker-compose.yml         Local development database (PostgreSQL 16)
docker-compose.staging.yml Production-grade staging topology with Caddy HTTPS reverse proxy
spec-pack/                 PRD, rules, task list, decisions log, and session memory
```

---

## 3. Quickstart & Local Execution

### 3.1 Start Local Database
```bash
# Start PostgreSQL 16 container
docker compose up -d db

# Verify container is healthy
docker compose ps
```

### 3.2 Install Dependencies & Run Database Migrations
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1

pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run migrations to HEAD (Migration 0009)
alembic upgrade head

# Verify 35 tables and constraints
python scripts/check_migrations.py

# Seed master catalog, roles, and pricing tiers
python scripts/seed.py
```

### 3.3 Start FastAPI Application
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
- Interactive OpenAPI Documentation: `http://localhost:8000/docs`
- Liveness Probe: `http://localhost:8000/api/v1/health/live`
- Readiness Probe: `http://localhost:8000/api/v1/health/ready`
- Telemetry & System Status: `http://localhost:8000/api/v1/health/detailed`

---

## 4. Automated Testing & Verification

Run the full automated test suite (444 tests) from `backend/`:
```bash
cd backend

# 1. Code formatting check (Black)
python -m black --check .

# 2. Linter check (Ruff)
python -m ruff check .

# 3. Execute full test suite
python -m pytest -v
```

---

## 5. Operations & Disaster Recovery Runbooks

- **Staging Deployment:** See [DEPLOYMENT_GUIDE.md](docs/deployment/DEPLOYMENT_GUIDE.md)
- **Database Backup:**
  ```bash
  python backend/scripts/backup_db.py --out-dir backend/backups
  ```
- **Database Restore (with SHA-256 verification):**
  ```bash
  python backend/scripts/restore_db.py backend/backups/backup_sqlite_YYYYMMDD_HHMMSS.db.gz
  ```
- **Security Audit:** See [OWASP_SECURITY_AUDIT.md](docs/security/OWASP_SECURITY_AUDIT.md)
- **Traceability Matrix:** See [REQUIREMENTS_TRACEABILITY_MATRIX.md](docs/testing/REQUIREMENTS_TRACEABILITY_MATRIX.md)
- **User Manual:** See [USER_MANUAL.md](docs/user-manual/USER_MANUAL.md)
- **Viva Preparation Guide:** See [VIVA_PREPARATION_GUIDE.md](docs/viva/VIVA_PREPARATION_GUIDE.md)
