import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request

SESSION_COOKIE = "playmatch_session"
VISITOR_COOKIE = "playmatch_visitor"
password_hasher = PasswordHasher(time_cost=2, memory_cost=32768, parallelism=2)
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(24))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def capability(visitor: str, run_id: str) -> str:
    return hmac.new(visitor.encode(), run_id.encode(), hashlib.sha256).hexdigest()


def verify_password(hashed: str, password: str) -> bool:
    try:
        return password_hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def validate_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in request.app.state.settings.origins:
        raise HTTPException(403, "跨站请求已拒绝")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "跨站请求已拒绝")


class RateLimiter:
    """Single-process limiter. Deployment intentionally uses one API process."""

    def __init__(self):
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: int = 60) -> None:
        now = time.monotonic()
        with self._lock:
            queue = self._events[key]
            while queue and queue[0] <= now - window:
                queue.popleft()
            if len(queue) >= limit:
                raise HTTPException(
                    429, "请求过于频繁，请稍后重试", headers={"Retry-After": str(window)}
                )
            queue.append(now)
            if len(self._events) > 5000:
                stale = [
                    k
                    for k, values in self._events.items()
                    if not values or values[-1] < now - window
                ]
                for stale_key in stale:
                    self._events.pop(stale_key, None)
