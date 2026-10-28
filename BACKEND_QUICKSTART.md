# Backend quick start (T-020)

```bash
cp .env.example .env
docker compose up -d db            # start PostgreSQL
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload      # http://localhost:8000/docs
pytest -q && ruff check . && black --check .
```
- `GET /health` -> process is up
- `GET /api/v1/health/db` -> database reachable (503 in the standard error format if not)
- Everything at once in Docker: `docker compose up --build` (API on port 8000)

## Adding a module (example: catalog)
Create `router.py`, `schemas.py`, `service.py`, `repository.py`, `models.py` inside `app/catalog/`,
register the router in `app/main.py`, and import the models in `alembic/env.py`.
Then: `alembic revision --autogenerate -m "add products"` and `alembic upgrade head`.
