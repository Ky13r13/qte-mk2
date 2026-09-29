"""Single-user loopback session and request-boundary enforcement."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hmac
import secrets
import time
from typing import Callable

IDLE_TIMEOUT_SECONDS = 1800


class SecurityError(Exception):
    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


@dataclass
class Session:
    cookie: str
    request_token: str
    last_used: float


class SessionController:
    """Own the sole in-memory session, access code, throttle, and expiry."""

    def __init__(self, code_sink: Callable[[str], object], clock: Callable[[], float] = time.monotonic) -> None:
        self._sink = code_sink
        self._clock = clock
        self._session: Session | None = None
        self._failures: deque[float] = deque()
        self._code: str | None = None
        self._issue_code()

    def _issue_code(self) -> None:
        self._code = secrets.token_urlsafe(24)
        self._sink(self._code)

    def login(self, code: object) -> Session:
        now = self._clock()
        while self._failures and now - self._failures[0] >= 60:
            self._failures.popleft()
        if len(self._failures) >= 5:
            raise SecurityError("login_throttled", 429, "Too many login attempts.")
        if not isinstance(code, str) or self._code is None or not _constant_time_equal(code, self._code):
            self._failures.append(now)
            raise SecurityError("invalid_code", 401, "Invalid access code.")
        self._code = None
        self._failures.clear()
        self._session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32), now)
        return self._session

    def authenticate(self, cookie: str | None, *, touch: bool = True) -> Session:
        now = self._clock()
        session = self._session
        if session is None or cookie is None or not _constant_time_equal(cookie, session.cookie):
            raise SecurityError("authentication_required", 401, "Authentication required.")
        if now - session.last_used > IDLE_TIMEOUT_SECONDS:
            self._session = None
            self._issue_code()
            raise SecurityError("session_expired", 401, "Session expired.")
        if touch:
            session.last_used = now
        return session

    def require_token(self, session: Session, token: str | None) -> None:
        if token is None or not _constant_time_equal(token, session.request_token):
            raise SecurityError("request_token_required", 403, "Valid request token required.")

    def logout(self, session: Session) -> None:
        if self._session is not session:
            raise SecurityError("authentication_required", 401, "Authentication required.")
        self._session = None
        self._issue_code()

    @property
    def access_code_for_testing(self) -> str | None:
        return self._code


def unique_header(scope: dict, name: bytes) -> str | None:
    values = [value for key, value in scope.get("headers", ()) if key.lower() == name]
    if len(values) > 1:
        raise SecurityError("malformed_headers", 400, "Malformed request headers.")
    if not values:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        raise SecurityError("malformed_headers", 400, "Malformed request headers.") from None


def _constant_time_equal(candidate: str, expected: str) -> bool:
    """Compare user-controlled text without compare_digest's ASCII str limit."""
    try:
        encoded = candidate.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(encoded, expected.encode("ascii"))
