"""Sales analytics module (Milestone M6 / T-062).

Read-only reporting over orders, order items, catalog and stock balances.
No new tables are involved: every number is derived at request time from the
transactional data, so the report can never drift away from the ledger.

The JSON endpoint and the CSV export both call the same service functions, so
there is exactly one source of truth for the query.
"""

from app.analytics.router import router

__all__ = ["router"]
