# Yashoda SpiceChain AI — AI Services Subsystem

This directory contains the AI & Machine Learning services for **Yashoda SpiceChain AI**, fulfilling **Milestone M7** (Sales Demand Forecasting & Reorder Intelligence) and **Milestone M8** (LLM Adapter, Grounded RAG Assistant & Safe Whitelisted Tools).

Assigned Primary Owner: **Member 4 — Chetan Sonawane** (*Backend Architecture and AI*).

---

## 1. Subsystem Architecture

The AI subsystem is structured as a zero-bloat, portable Python package that integrates directly into the modular monolith backend (`backend/app/ai`) while also remaining completely runnable standalone for evaluations, CI/CD, and viva demonstrations.

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

- **Rule 60 (No Model-Generated SQL):** The model never writes or executes arbitrary SQL queries. All database interactions occur via predetermined, whitelisted Python tool functions.
- **Rule 61 (User-Permission Binding):** Tool calls execute strictly under the authenticated user's session role (RBAC). A dealer cannot invoke owner-only sales aggregations or slow-mover analyses.
- **Rule 62 (Human-in-the-Loop):** Any operation that mutates state (such as placing an order) produces a `DRAFT` record. No automated order is finalized without explicit human review and approval.
- **Rule 63 (Mandatory Citations & Exact Refusal):** The RAG Assistant answers questions strictly using facts cited from approved knowledge base files. If no matching ground truth exists, it returns the exact string:
  > `"Approved information not available."`
- **Rule 64 (Untrusted Model Output):** Model output is treated as untrusted text. Prompt injection attempts (e.g., "Ignore previous instructions") are neutralized and refused.
- **Rule 65 (Auditability):** Every AI tool run is logged with caller identity, parameters, execution time, and status.
- **Rule 66 & Decision D-015 (Honest Forecasting):** Models are evaluated against baselines on a chronological train/test split (18 months train, 6 months held-out test). Metrics (MAE, RMSE, MAPE) are honestly reported.

---

## 3. How to Run

### Interactive CLI Demonstration
Run the demonstration script to inspect forecasting evaluations, reorder calculations, RAG retrieval, and agent security:

```powershell
cd ai-services
python demo.py
```

### Running Tests
Execute the standalone test suite:

```powershell
cd ai-services
python -m pytest -v
```

---

## 4. Integration with Backend Monolith

The backend FastAPI application (`backend/app/ai`) exposes these capabilities via REST endpoints:
- `POST /api/v1/ai/chat` — Grounded RAG query answering
- `POST /api/v1/ai/agent/query` — Natural language query routed to safe whitelisted tools
- `POST /api/v1/ai/drafts/{id}/approve` — Human confirmation for AI-generated drafts
- `GET /api/v1/ai/forecasts` — Forecast predictions & backtesting metrics
- `GET /api/v1/ai/recommendations/reorder` — Transparent reorder quantities & batch expiry risk
- `GET /api/v1/ai/tool-runs` — Owner audit trail of tool runs
