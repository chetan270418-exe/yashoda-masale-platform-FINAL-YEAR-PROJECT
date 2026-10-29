# Yashoda SpiceChain AI — AI Services Subsystem (Standalone Prototype Demo)

> ⚠️ **STANDALONE PROTOTYPE DEMO / SANDBOX NOTICE**
>
> **Important Architectural Distinction:** This directory (`ai-services/`) serves exclusively as an **independent, offline prototype sandbox and demonstration package** developed for academic evaluation, algorithmic prototyping, and Member 4 viva demonstrations.
>
> The **authoritative, active production AI subsystem** for Yashoda SpiceChain AI lives directly inside the modular monolith backend at:
> ➡️ **`backend/app/ai/`** (FastAPI routers, live PostgreSQL ORM models, Alembic migrations, sliding-window rate limiting, and ACID row-level locking).

Assigned Primary Owner: **Member 4 — Chetan Sonawane** (*Backend Architecture and AI*).

---

## 1. Prototype Subsystem Overview

The prototype AI package in this directory is structured as a zero-bloat, portable Python package designed to demonstrate core algorithms (M7 demand forecasting and M8 grounded RAG / safe whitelisted agent tooling) offline without requiring a running database server.

```
ai-services/
├── demo.py                      # Standalone CLI demonstration runner
├── requirements.txt             # Dependencies
├── forecasting/                 # Milestone M7: Demand forecasting & reorder intelligence
│   ├── generator.py             # Deterministic 24-month synthetic sales history (seed=42)
│   ├── engine.py                # Chronological backtesting: Seasonal Naive, MA-3, Linear Trend
│   └── reorder.py               # Transparent reorder formulas & batch expiry risk analysis
├── rag/                         # Milestone M8: Grounded RAG Assistant
│   ├── retriever.py             # BM25/TF-IDF lexical search with stop-word filtering
│   ├── service.py               # Answer synthesis with strict source citations & refusal fallback
│   └── knowledge_base/          # Approved company documentation
│       ├── spice_catalog.md
│       ├── storage_and_shelf_life.md
│       └── dealer_order_policy.md
├── llm/                         # Milestone M8: Provider-agnostic LLM adapter
│   └── adapter.py               # Abstract base LLMAdapter, MockLLMAdapter, LiveHTTPLLMAdapter
├── agent/                       # Milestone M8: Safe agent tools & draft orders
│   └── tools.py                 # Whitelisted tool schemas, RBAC permission checks, draft rules
└── tests/                       # Standalone test suite
    ├── test_forecasting.py
    ├── test_rag.py
    └── test_adapter.py
```

---

## 2. Invariants & AI Safety Rules

Following [`spec-pack/rules.md`](../spec-pack/rules.md) (Rules 60–66):

- **Rule 60 (No Model-Generated SQL):** The model never writes or executes arbitrary SQL queries. All interactions occur via predetermined, whitelisted Python tool functions.
- **Rule 61 (User-Permission Binding):** Tool calls execute strictly under the authenticated user's session role (RBAC). A dealer cannot invoke owner-only sales aggregations or slow-mover analyses.
- **Rule 62 (Human-in-the-Loop):** Any operation that mutates state (such as placing an order) produces a `DRAFT` record. No automated order is finalized without explicit human review and approval.
- **Rule 63 (Mandatory Citations & Exact Refusal):** The RAG Assistant answers questions strictly using facts cited from approved knowledge base files. If no matching ground truth exists, it returns the exact string:
  > `"Approved information not available."`
- **Rule 64 (Untrusted Model Output):** Model output is treated as untrusted text. Prompt injection attempts (e.g., "Ignore previous instructions") are neutralized and refused.
- **Rule 65 (Auditability):** Every AI tool run is logged with caller identity, parameters, execution time, and status.
- **Rule 66 & Decision D-015 (Honest Forecasting):** Models are evaluated against baselines on a chronological train/test split (18 months train, 6 months held-out test). Metrics (MAE, RMSE, MAPE) are honestly reported.

---

## 3. Architectural Comparison: Prototype vs Production

The system maintains a clean boundary between the standalone demonstration environment and the live production system:

| Architectural Dimension | Standalone Prototype Demo (`ai-services/`) | Active Production Backend (`backend/app/ai/`) |
|---|---|---|
| **Primary Role & Purpose** | Academic evaluation, viva demonstration, offline algorithm testbed | Real-time web application AI API serving consumers, dealers, and staff |
| **Data Storage & Persistence** | In-memory synthetic dicts & deterministic generators | Live PostgreSQL database with SQLAlchemy ORM and Alembic migrations |
| **Transactions & Concurrency** | Single-threaded CLI / In-memory operations | Full ACID transactions with `SELECT ... FOR UPDATE` row-level locking |
| **Authentication & RBAC** | Mock role objects for demonstration | Cryptographic JWT authentication, permission checks, audit logging to `ai_tool_runs` |
| **Draft Orders Lifecycle** | Terminal console output / CLI approval simulation | Persisted `order_drafts` records with `/api/v1/ai/drafts/{id}/approve` endpoint |
| **Rate Limiting** | None | Per-client sliding-window `RateLimiter` (20 req/60s chat, 30 req/60s agent query) |
| **Integration Pattern** | Zero-dependency standalone Python package (`demo.py`) | Integrated FastAPI router (`router.py`) in backend modular monolith |

---

## 4. How to Run the Prototype Demo

### Interactive CLI Demonstration
Run the demonstration script to inspect forecasting evaluations, reorder calculations, RAG retrieval, and agent security:

```powershell
cd ai-services
python demo.py
```

### Running Prototype Unit Tests
Execute the standalone test suite:

```powershell
cd ai-services
python -m pytest -v
```

---

## 5. Production API Endpoints in Backend

In the live production application, the backend FastAPI service (`backend/app/ai`) exposes hardened endpoints:
- `POST /api/v1/ai/chat` — Grounded RAG query answering (rate-limited to 20 req/60s)
- `POST /api/v1/ai/agent/query` — Natural language query routed to safe whitelisted tools (rate-limited to 30 req/60s)
- `POST /api/v1/ai/drafts/{id}/approve` — Human confirmation for AI drafts with `FOR UPDATE` concurrency locking
- `GET /api/v1/ai/forecasts` — Forecast predictions & backtesting metrics
- `GET /api/v1/ai/recommendations/reorder` — Transparent reorder quantities & batch expiry risk
- `GET /api/v1/ai/tool-runs` — Owner audit trail of tool runs
