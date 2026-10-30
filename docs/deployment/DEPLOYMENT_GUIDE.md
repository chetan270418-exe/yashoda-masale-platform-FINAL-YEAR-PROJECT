# Yashoda SpiceChain — Deployment & Operations Guide
**Milestone:** M9 (Deployment) | **Tasks:** T-090, T-091, T-092  
**System:** Yashoda Masale ERP & AI Platform (Backend Monolith)  
**Target Environment:** Staging & Production On-Premises / Cloud VM (Docker Engine 26+, PostgreSQL 16)

---

## 1. System Architecture & Prerequisites

### 1.1 Minimum Server Specifications
- **Operating System:** Ubuntu 22.04 LTS / Debian 12 / Windows Server 2022
- **Processor:** 2 vCPU cores (x86_64 or ARM64)
- **Memory (RAM):** 4 GB RAM minimum (8 GB recommended for LLM caching & synthetic datasets)
- **Disk Storage:** 20 GB SSD with NVMe performance
- **Network Ingress:** Ports `80` (HTTP), `443` (HTTPS), and optionally `22` (SSH)

### 1.2 Required Software Packages
```bash
# Docker and Docker Compose plugin
curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh
sudo usermod -aG docker $USER
docker compose version  # Verify Docker Compose v2.20+

# Python 3.11+ for offline script execution (optional on host if using Docker)
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip openssl
```

---

## 2. Environment Configuration & Secret Management

All sensitive secrets must be injected via `.env.staging` and NEVER committed to version control.

### 2.1 Generate Production-Grade Cryptographic Secrets
```bash
# Generate 256-bit secure JWT signing key
JWT_SECRET=$(openssl rand -hex 32)
echo "JWT_SECRET=${JWT_SECRET}"

# Generate strong database password
DB_PASS=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 24)
echo "POSTGRES_PASSWORD=${DB_PASS}"
```

### 2.2 Create `.env.staging` File
Create `.env.staging` in the root repository directory:
```env
# Application Runtime
ENVIRONMENT=staging
APP_NAME=Yashoda SpiceChain [Staging]
STAGING_DOMAIN=staging.yashodamasale.com

# Database Connection
POSTGRES_DB=yashoda_masale_staging
POSTGRES_USER=yashoda_admin
POSTGRES_PASSWORD=replace_with_generated_db_password
POSTGRES_PORT=5432

# Security & Authentication
JWT_SECRET=replace_with_generated_jwt_secret
JWT_ALGORITHM=HS256
JWT_EXPIRATION_MINUTES=120
CORS_ORIGINS=https://staging.yashodamasale.com,http://localhost:3000

# AI Subsystem / LLM Provider
LLM_PROVIDER=rule_fallback
LLM_API_KEY=optional_gemini_api_key
```

---

## 3. Staging Deployment Execution

### 3.1 Build and Launch Staging Stack
```bash
# 1. Pull base images and build backend container
docker compose -f docker-compose.staging.yml --env-file .env.staging build

# 2. Start services in detached mode
docker compose -f docker-compose.staging.yml --env-file .env.staging up -d

# 3. Verify container statuses
docker compose -f docker-compose.staging.yml ps
```

### 3.2 Execute Database Migrations (Alembic)
```bash
# Run all migrations up to HEAD (Migration 0009)
docker compose -f docker-compose.staging.yml exec api alembic upgrade head

# Verify table creation and integrity check
docker compose -f docker-compose.staging.yml exec api python scripts/check_migrations.py
```

### 3.3 Seed Initial Roles and Catalog Data
```bash
# Run catalog and master role seed script
docker compose -f docker-compose.staging.yml exec api python scripts/seed.py
```

---

## 4. Health Checks & Monitoring Verification (T-091)

The backend provides layered probes for process supervision, load balancers, and monitoring tools:

| Endpoint | Purpose | Expected Status | Response |
| :--- | :--- | :--- | :--- |
| `GET /health` | Base Process Ping | `200 OK` | `{"status": "ok", "app": "...", "environment": "staging"}` |
| `GET /api/v1/health/live` | Liveness Probe | `200 OK` | `{"status": "alive", "timestamp": "..."}` |
| `GET /api/v1/health/ready` | Readiness Probe | `200 OK` | `{"status": "ready", "database": "connected"}` |
| `GET /api/v1/health/detailed` | Telemetry & System | `200 OK` | `{"status": "ok", "uptime_seconds": ..., "database": "up", ...}` |

### 4.1 Verification Commands
```bash
# Check Readiness probe
curl -f -s https://staging.yashodamasale.com/api/v1/health/ready | jq .

# Check Detailed System Telemetry
curl -f -s https://staging.yashodamasale.com/api/v1/health/detailed | jq .
```

---

## 5. Automated Database Backup & Disaster Recovery (T-090)

The system includes standalone, zero-dependency backup and restore scripts supporting both PostgreSQL and SQLite.

### 5.1 Creating a Consistent Database Backup
```bash
# Execute backup from inside container or host
docker compose -f docker-compose.staging.yml exec api python scripts/backup_db.py --out-dir /backups

# Output generated:
# 1. backup_pg_yashoda_masale_staging_YYYYMMDD_HHMMSS.sql
# 2. backup_pg_yashoda_masale_staging_YYYYMMDD_HHMMSS.sql.sha256
# 3. backup_pg_yashoda_masale_staging_YYYYMMDD_HHMMSS.sql.meta.json
```

### 5.2 Scheduled Daily Backups via Cron
Add the following line to `/etc/cron.d/yashoda_backup`:
```cron
0 2 * * * root cd /opt/yashoda-masale && docker compose -f docker-compose.staging.yml exec -T api python scripts/backup_db.py --out-dir /backups >> /var/log/yashoda_backup.log 2>&1
```

### 5.3 Restoring from Backup with SHA-256 Checksum Validation
```bash
# 1. List available backups
ls -lh backend/backups/

# 2. Execute restore with mandatory SHA-256 verification
docker compose -f docker-compose.staging.yml exec api python scripts/restore_db.py /backups/backup_pg_yashoda_masale_staging_20261030_120000.sql
```

---

## 6. Disaster Recovery & Rollback Runbook

### 6.1 Database Migration Rollback
```bash
# Rollback single migration step
docker compose -f docker-compose.staging.yml exec api alembic downgrade -1

# Rollback to specific target version
docker compose -f docker-compose.staging.yml exec api alembic downgrade 0008
```

### 6.2 Emergency Container Restart
```bash
docker compose -f docker-compose.staging.yml restart api
```

---

## 7. Clean Machine Verification Checklist (Sign-Off)

- [x] Docker Engine & Compose v2 installed and operational.
- [x] Cryptographic secrets generated and stored in `.env.staging`.
- [x] Database container initialized with persistent volume mount.
- [x] Alembic migrations executed through version 0009 without errors.
- [x] Seed script populated master catalog, pricing, and system roles.
- [x] Liveness (`/api/v1/health/live`) and Readiness (`/api/v1/health/ready`) probes return 200.
- [x] Online database backup executed and verified with SHA-256 checksum.
- [x] Database restoration executed and integrity validated.
- [x] HTTPS reverse proxy enabled with TLS termination via Caddy.
