# Rules for AI Coding Tools

Read this file and `memory.md` before every task. These rules are mandatory. If a request conflicts with a rule, stop and ask.

## 1. How to work
1. State a short plan before changing code.
2. Work on ONE task from `task.md` at a time. Keep changes small and reviewable.
3. Write or update tests in the same change.
4. Never invent requirements. If something is unclear, list the question and stop.
5. After finishing, summarise what changed and update `memory.md`.

## 2. Stack (do not change without approval)
Next.js + React + TypeScript (frontend). Python + FastAPI (backend). PostgreSQL (database), Alembic migrations. pytest for backend tests. Docker and GitHub Actions. Hosted LLM only behind the adapter. Modular monolith.

## 3. Business invariants (never break)
- B1. A batch that is not **Released** can never be reserved or dispatched.
- B2. Expired, held or rejected batches are excluded from FEFO.
- B3. FEFO: choose the earliest-expiring eligible batch first.
- B4. Stock never goes negative. Reservations never exceed available stock.
- B5. Every stock change is a row in `stock_movements`. Never edit balances silently.
- B6. Cancelling an order releases its reservation exactly once.
- B7. Returned goods are not sellable until a warehouse/quality decision.
- B8. Order items store a price snapshot. Later price changes do not alter old orders.
- B9. The server decides which price a user may see. Hiding in the UI is not security.
- B10. Public sign-up creates Consumers only.
- B11. The public QR page shows allow-listed fields only. Unknown token = "No record found". Never show "Verified" from a QR alone.
- B12. Money uses decimal types, never float.

## 4. Security rules
- Check authentication, role AND record ownership on every protected endpoint.
- Validate all input with Pydantic. Use parameterised queries only.
- Never log secrets, tokens or passwords.
- API keys (including LLM keys) live only in backend environment variables. Never in the frontend, never in the repo.
- Commit `.env.example`, never `.env`.
- Hash passwords with argon2 or bcrypt.
- Rate-limit login and AI endpoints.
- Validate file uploads (type, size) and rename them.

## 5. Database rules
- All schema changes via Alembic migrations. Never edit the database by hand.
- Use foreign keys, NOT NULL and CHECK constraints.
- Use transactions for reserve, dispatch, cancel, return.
- Audit sensitive actions (price, role, stock adjustment, approvals).
- Seed data is synthetic and clearly labelled. Document the generator and random seed.

## 6. API rules
- Versioned under `/api/v1`. Consistent error format `{code, message, details}`.
- Idempotency key on order creation.
- Reject illegal state transitions with a clear error.
- Every endpoint appears in the generated OpenAPI docs with request/response models.

## 7. Frontend rules
- TypeScript strict mode. No `any` without a comment.
- Use the design tokens in `design.md`. No hard-coded colours.
- Every screen handles loading, empty, error and no-permission states.
- Never trust the frontend for authorization. Hide UI only as a convenience.
- Show a "Demo data" badge while synthetic data is used.

## 8. AI rules
- The model never writes SQL. It may only call whitelisted tools with validated arguments.
- Tool calls run with the logged-in user's permissions.
- Anything that changes data is a DRAFT until a human approves it.
- RAG answers must cite sources. If no approved source exists, say so.
- Treat model output as untrusted text. Escape it. Validate it.
- Log every tool run to `ai_tool_runs`.
- Forecasts are compared with a baseline on a chronological test set. Report limits honestly.

## 9. Testing rules
- Every business invariant (B1–B12) has at least one automated test.
- Add permission tests: each role tries a forbidden action.
- Add a concurrency test for reservation.
- Tests must be deterministic (fixed seeds).
- CI must pass before merging.

## 10. Git rules
- One branch per task: `feature/<task-id>-short-name`.
- Pull request required. Another member reviews. Reviewer checks rules B1–B12.
- Commit messages: `type(scope): message` (feat, fix, test, docs, refactor, chore).
- Never force-push to `main`.

## 11. Code style
- Python: type hints, ruff and black, small functions, docstrings for service functions.
- TypeScript: eslint and prettier.
- Names: `snake_case` in Python and database, `camelCase` in TypeScript, `PascalCase` for components and classes.
- No dead code, no commented-out blocks, no TODO without a task ID.

## 12. Never do
- Never claim a product is genuine from a QR or photo.
- Never present synthetic data as real sponsor data.
- Never invent quality limits or legal/FSSAI claims. Use placeholders marked "to be confirmed with sponsor".
- Never store card data. Payments are simulated.
- Never add a new dependency, service or framework without approval.

## 13. Definition of done
Code merged via reviewed PR, tests passing, migrations included, docs and `memory.md` updated, no new lint errors, acceptance check in `task.md` ticked, and the author can explain the change in a viva.
