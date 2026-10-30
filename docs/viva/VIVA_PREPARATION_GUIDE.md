# Yashoda SpiceChain — Mock Viva & Comprehensive Defense Guide
**Milestone:** M11 (Final) | **Task:** T-111  
**Project:** Yashoda Masale ERP & AI Platform  
**Target:** Engineering Degree Final Year Project Oral Defense (Viva Voce)  

---

## 1. Team Role Allocation & Presentation Responsibilities

Every team member must be prepared to answer core questions on the architecture and defend their specialized subsystem:

| Member / Role | Focus Area | Key Defense Modules |
| :--- | :--- | :--- |
| **M1: Frontend & UX** | User Experience, Design System | Role-based navigation, dealer chat UX, store catalog, order tracking |
| **M2: Backend & Security** | Data Models, DB Invariants | 6-role RBAC, Alembic migrations 0001–0009, row locks, idempotency |
| **M3: AI & Machine Learning** | RAG Assistant, Forecasting | FEFO expiry risk math, WAPE/RMSE forecasting metrics, draft-only AI |
| **M4: Integration & QA** | Test Suite, Deployment | 444 automated tests, Docker compose staging, backup/restore, CI/CD |

---

## 2. Top 15 Viva Questions & Gold-Standard Answers

### Q1: Why did you choose a Modular Monolith instead of Microservices?
> **Answer:** For an enterprise of Yashoda Masale's scale (single manufacturing plant, regional distributor network), microservices introduce severe distributed systems overhead: distributed transactions (two-phase commit or saga pattern), network latency between services, and complex cluster orchestration. A **Modular Monolith** in FastAPI gives us strict domain boundary isolation through Python packages (`app/orders`, `app/inventory`, `app/catalog`) while executing inside a single transactional boundary (`Session.commit()` with ACID guarantees) on a single PostgreSQL instance.

---

### Q2: How does the system prevent two users from reserving the last spice unit at the same time?
> **Answer:** We employ pessimistic row-level locking in PostgreSQL:
> 1. When an order operation is triggered, we lock the order record using `.with_for_update(of=Order)`.
> 2. The stock reservation queries the earliest-expiring batch balances and executes within the same atomic transaction with `commit=False`.
> 3. If two concurrent requests arrive, the database serializes them at the row lock. The first request successfully allocates the available stock (`on_hand - reserved >= requested_qty`) and advances the order status to `reserved`.
> 4. The second request wakes up, checks the order's state machine, and is immediately rejected because `current_status != 'pending'` (or fails with `INSUFFICIENT_STOCK`). No double-allocation can ever occur.

---

### Q3: Why FEFO (First-Expired, First-Out) instead of standard FIFO?
> **Answer:** Ground spices contain volatile essential oils (e.g., curcumin in turmeric, capsaicin in chilli) that degrade over time. In a multi-batch manufacturing plant, a batch produced yesterday might have an earlier expiry date than a batch produced today due to differing raw material harvest moisture levels or organic preservatives. Standard FIFO only looks at production date, which risks dispatching older stock too late. **FEFO sorts strictly by `expiry_date ASC`**, ensuring inventory nearest its expiration date is dispatched first, minimizing waste.

---

### Q4: Why is your AI Assistant strictly "Draft-Only" (Invariant B11)?
> **Answer:** Large Language Models are probabilistic and prone to hallucinations, prompt injections, or unintended parameter shifts. If an AI agent were permitted to directly commit mutations to physical inventory or alter order statuses, an injection attack or misunderstanding could exhaust warehouse stock or corrupt pricing. By enforcing **Invariant B11**, the AI only creates an `OrderDraft` in `draft` status. A human dealer must explicitly review the itemized variant SKUs, quantities, and prices before submitting an approval call.

---

### Q5: Why is floating-point arithmetic banned for currency and quantities (Invariant B12)?
> **Answer:** IEEE 754 floating-point numbers represent fractions in binary, introducing rounding errors (e.g., `0.1 + 0.2 = 0.30000000000000004`). In a commercial spice enterprise processing thousands of kilograms and monetary values with GST taxes, these rounding errors compound into accounting discrepancies and audit failures. We enforce Python `decimal.Decimal` and PostgreSQL `Numeric(12, 2)` across all monetary fields and `Numeric(12, 3)` across weights.

---

### Q6: How does the system handle Idempotency across different users?
> **Answer:** Initially, idempotency keys were globally unique across all orders. However, if Consumer A generated a generic key (e.g. `client-order-001`) and Consumer B generated the same key, Consumer B received a confusing `409 Conflict`. We resolved this by creating a **composite unique constraint**: `UNIQUE (customer_id, idempotency_key)`. This isolates idempotency per user: Consumer B can safely use their own key, while Consumer A replaying their request gets the exact cached order response without re-triggering stock deductions.

---

### Q7: Why are payments simulated rather than using a real Stripe or Razorpay gateway?
> **Answer:** Per Architectural Rule 91 and Decision D-009, this project is designed for enterprise internal operational validation. Accepting live credit cards or UPI without formal PCI-DSS compliance and banking merchant licenses creates security liabilities. Our payment architecture models real-world status progressions (`pending -> simulated_upi -> completed`) and stores mock reference tokens, allowing full end-to-end testing without exposing cardholder data.

---

### Q8: How is the public QR traceability endpoint protected from exposing private business secrets?
> **Answer:** The public QR endpoint (`GET /api/v1/traceability/public/{batch_code}`) uses a **read-only projection schema** (`PublicBatchTrace`). It exposes only consumer transparency events: harvest origin, factory roasting/grinding dates, and passing laboratory quality checks. Internal commercial fields (supplier purchase price, wholesale margin, factory yield, staff worker IDs) are completely excluded from the SQLAlchemy query and Pydantic response model.

---

### Q9: How do you evaluate your demand forecasting models?
> **Answer:** We implemented a chronological train-test split (first 18 months training, last 6 months testing) across 24 months of synthetic demand. We evaluate using standard retail metrics:
> - **WAPE (Weighted Absolute Percentage Error):** Robust to zero-sale periods common in intermittent wholesale orders.
> - **RMSE (Root Mean Squared Error):** Penalizes large forecasting errors heavily.
> - **MAE (Mean Absolute Error):** Measures average unit deviation.
> We compare our candidate seasonal exponential smoothing model against a moving-average baseline to prove predictive uplift.

---

### Q10: How do you verify database migrations and schema consistency?
> **Answer:** We have automated tests in `tests/test_migrations.py` that execute a full upgrade and downgrade roundtrip through all Alembic migrations (`0001` through `0009`). Additionally, our standalone script `scripts/check_migrations.py` inspects the SQLAlchemy declarative metadata against the live Alembic head schema, verifying that all 35 tables, check constraints, and composite unique keys match 100%.

---

## 3. Architecture Decision Log Summary (Key Decisions)

| Decision | Summary | Why Chosen Over Alternative |
| :--- | :--- | :--- |
| **D-001** | Python 3.11 + FastAPI Monolith | Native async performance, Pydantic v2 data validation, OpenAPI autodoc |
| **D-002** | PostgreSQL 16 Primary DB | Robust ACID transactions, JSONB, native row locks (`FOR UPDATE`) |
| **D-003** | Decimal for All Currency & Weights | Eliminates binary floating-point representation discrepancies (Rule B12) |
| **D-005** | Append-Only Stock Movement Ledger | Full auditability, prevents fraudulent inventory record updates (Rule B5) |
| **D-007** | Strict FEFO Stock Rotation | Minimizes spice essential oil degradation and expiry waste (Rule B3) |
| **D-009** | Simulated Payment Records | Eliminates PCI-DSS liability while testing full order-to-cash lifecycle |
| **D-014** | Grounded RAG with Strict Refusal | Prevents AI hallucination; answers only from verified product knowledge |
| **D-025** | Dedicated Order Lifecycle Endpoints | Blocks bypass of stock deductions via generic PATCH status calls |
| **D-030** | Composite Idempotency per Customer | Prevents accidental cross-tenant 409 collisions on identical keys |

---

## 4. Final Rehearsal & Presentation Script

1. **Introduction (1 minute - M1/All):** Introduce Yashoda Masale, Satara, the business context, and the 4 core problems of spice manufacturing.
2. **Architecture & Live Demo (3 minutes - M2):** Demonstrate role-based login, factory batch creation, laboratory quality release, and FEFO order fulfillment.
3. **AI Assistant & Forecasting Demo (2 minutes - M3):** Showcase dealer conversational reorder, draft generation, human approval, and demand forecast curves.
4. **Security, Testing & Quality (2 minutes - M4):** Present the 444 automated test results, OWASP security audit findings, and Docker staging deployment.
5. **Q&A Defense (2 minutes - All):** Confident, direct answers referencing the architecture decisions and business invariants.
