"""v3.3.1 — Request/order correlation IDs.

Every mutating API request gets a correlation ID (client-supplied
``X-Correlation-ID`` or a generated one). It is:
- echoed back on every response (``X-Correlation-ID`` header);
- attached to audit-log metadata and broker order intents, so an order can be
  traced end-to-end: HTTP request → order intent → broker order → audit.

The active ID lives in a contextvar so concurrent requests never mix IDs.
"""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

from fastapi import Request

CORRELATION_HEADER = "X-Correlation-ID"

# Client-supplied IDs are constrained: 8..64 chars of [A-Za-z0-9._-].
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")

_current_correlation_id: ContextVar[str] = ContextVar(
    "qtp_correlation_id", default="")


def new_correlation_id() -> str:
    """Generate a fresh correlation ID (qtp-<32 hex>)."""
    return f"qtp-{uuid.uuid4().hex}"


def current_correlation_id() -> str:
    """The correlation ID bound to the current context ('' outside a request)."""
    return _current_correlation_id.get()


def sanitize_correlation_id(raw: object) -> str:
    """Return a safe client-supplied ID, or '' when absent/invalid.

    Invalid input is NEVER echoed (log-injection / header-splitting guard):
    a fresh ID is not generated here — the caller decides.
    """
    candidate = str(raw or "").strip()
    if not candidate:
        return ""
    return candidate if _SAFE_ID_RE.match(candidate) else ""


async def bind_correlation_id(request: Request) -> str:
    """Resolve the correlation ID for a request and bind it to the context.

    Priority: sanitized client header > freshly generated ID.
    Returns the bound ID.
    """
    correlation_id = sanitize_correlation_id(request.headers.get(CORRELATION_HEADER))
    if not correlation_id:
        correlation_id = new_correlation_id()
    _current_correlation_id.set(correlation_id)
    return correlation_id


def audit_details(extra: object = None) -> dict:
    """Merge the current correlation ID into audit-log metadata."""
    details: dict = dict(extra) if isinstance(extra, dict) else {}
    details.setdefault("correlation_id", current_correlation_id() or None)
    return details
