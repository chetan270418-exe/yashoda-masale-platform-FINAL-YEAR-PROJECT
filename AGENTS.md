# Instructions for AI coding tools

Before ANY change:
1. Read `spec-pack/rules.md` (mandatory rules, invariants B1-B12) and `spec-pack/memory.md` (decisions, status).
2. Work on ONE task from `spec-pack/task.md`. State a short plan first.
3. Write tests in the same change. Never invent requirements; ask if unclear.
4. When finished, add a line to the session log in `spec-pack/memory.md`.

Architecture: `spec-pack/architecture.md`. Schema, FEFO and AI guides: `spec-pack/implementation.md`.
Backend lives in `backend/` (run `pytest` and `ruff check .` from there).
