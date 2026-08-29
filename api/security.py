"""Production security helpers for the trading dashboard.

This is a hardened baseline (timing-safe secrets, session cookies, lockout,
security headers). It is not a substitute for a dedicated WAF, SSO, or HSM.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Any, Dict, Optional, Tuple

SESSION_COOKIE = "qtp_session"
SESSION_TTL_S = int(os.getenv("SESSION_TTL_S", "43200"))  # 12 hours
AUTH_MAX_FAILURES = int(os.getenv("AUTH_MAX_FAILURES", "8"))
AUTH_LOCKOUT_S = float(os.getenv("AUTH_LOCKOUT_S", "300"))

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "0",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}


def api_key_matches(provided: Optional[str], expected: str) -> bool:
    """Constant-time compare. Empty expected never matches a provided value."""
    if not expected:
        return False
    got = str(provided or "")
    if not got:
        return False
    return hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8"))


def _secret_bytes(admin_api_key: str) -> bytes:
    extra = os.getenv("FERNET_KEY") or os.getenv("SESSION_SIGNING_KEY") or ""
    material = f"{admin_api_key}|{extra}".encode("utf-8")
    return hashlib.sha256(material).digest()


def issue_session_token(admin_api_key: str, ttl_s: int = SESSION_TTL_S) -> str:
    exp = int(time.time()) + max(60, int(ttl_s))
    nonce = secrets.token_hex(16)
    body = f"{exp}.{nonce}"
    sig = hmac.new(_secret_bytes(admin_api_key), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_session_token(token: Optional[str], admin_api_key: str) -> bool:
    if not token or not admin_api_key or token.count(".") != 2:
        return False
    exp_s, nonce, sig = token.split(".", 2)
    if not nonce or len(nonce) < 16:
        return False
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    body = f"{exp_s}.{nonce}"
    expected = hmac.new(
        _secret_bytes(admin_api_key), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    return True


def credential_is_valid(provided: Optional[str], admin_api_key: str) -> bool:
    if not admin_api_key:
        return True
    if api_key_matches(provided, admin_api_key):
        return True
    return verify_session_token(provided, admin_api_key)


def extract_credential(header_key: Optional[str], cookie_token: Optional[str],
                       query_token: Optional[str] = None) -> Optional[str]:
    for value in (header_key, cookie_token, query_token):
        if value:
            return str(value)
    return None


def client_id_from_request(request: Any, trust_proxy: bool = False) -> str:
    if trust_proxy:
        forwarded = ""
        try:
            forwarded = request.headers.get("x-forwarded-for") or ""
        except Exception:
            forwarded = ""
        if forwarded:
            return forwarded.split(",")[0].strip() or "unknown"
    try:
        if request.client and request.client.host:
            return request.client.host
    except Exception:
        pass
    return "unknown"


class AuthGuard:
    """Per-client failed-auth lockout (in-memory, per process)."""

    def __init__(self, max_failures: int = AUTH_MAX_FAILURES,
                 lockout_s: float = AUTH_LOCKOUT_S, clock: Any = time.monotonic):
        self.max_failures = max(3, int(max_failures))
        self.lockout_s = max(30.0, float(lockout_s))
        self.clock = clock
        self._failures: Dict[str, int] = {}
        self._locked_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def is_locked(self, client_id: str) -> bool:
        now = self.clock()
        with self._lock:
            until = self._locked_until.get(client_id)
            if until is None:
                return False
            if now >= until:
                self._locked_until.pop(client_id, None)
                self._failures.pop(client_id, None)
                return False
            return True

    def note_failure(self, client_id: str) -> Tuple[bool, int]:
        """Return (locked, remaining_attempts)."""
        now = self.clock()
        with self._lock:
            count = self._failures.get(client_id, 0) + 1
            self._failures[client_id] = count
            if count >= self.max_failures:
                self._locked_until[client_id] = now + self.lockout_s
                return True, 0
            return False, self.max_failures - count

    def note_success(self, client_id: str) -> None:
        with self._lock:
            self._failures.pop(client_id, None)
            self._locked_until.pop(client_id, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()
