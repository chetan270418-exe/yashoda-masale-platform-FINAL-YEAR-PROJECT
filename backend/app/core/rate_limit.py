"""In-memory thread-safe rate limiter (rules.md section 4: Rate-limit login and AI endpoints)."""

import threading
import time
from collections import defaultdict

from fastapi import Request

from app.core.errors import AppError


class RateLimiter:
    """Sliding-window in-memory rate limiter per client IP.

    Usage::

        login_rate_limiter = RateLimiter(max_requests=5, window_seconds=60)

        @router.post("/login", dependencies=[Depends(login_rate_limiter)])
        def login(...):
            ...
    """

    def __init__(
        self,
        max_requests: int = 5,
        window_seconds: int = 60,
        name: str = "rate_limit",
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.name = name
        self._history: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def __call__(self, request: Request) -> None:
        client_ip = request.client.host if request.client else "127.0.0.1"
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()

        now = time.time()
        window_start = now - self.window_seconds

        with self._lock:
            # Keep only timestamps within the current sliding window
            valid_timestamps = [ts for ts in self._history[client_ip] if ts > window_start]
            if len(valid_timestamps) >= self.max_requests:
                earliest = valid_timestamps[0]
                retry_after = max(1, int(self.window_seconds - (now - earliest)))
                raise AppError(
                    "RATE_LIMIT_EXCEEDED",
                    f"Too many requests. Please try again in {retry_after} seconds.",
                    status_code=429,
                    details={"retry_after_seconds": retry_after, "limit": self.max_requests},
                )
            valid_timestamps.append(now)
            self._history[client_ip] = valid_timestamps

    def reset(self) -> None:
        """Clear rate limit history (used in unit tests)."""
        with self._lock:
            self._history.clear()
