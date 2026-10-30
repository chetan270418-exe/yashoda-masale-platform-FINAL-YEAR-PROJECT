"""Batch traceability module (Milestone M6 / T-060).

Provides the public QR lookup page data, the batch event ledger and the
verification scan log.

Business rules enforced here:
- B11: the public lookup returns an allow-listed payload only; an unknown
  token yields a "No record found" answer and never reveals whether the
  token ever existed.
- rules.md section 12: a QR code never proves a product is genuine, so no
  response field or public text may make an authenticity claim.
"""

from app.traceability.router import router

__all__ = ["router"]
