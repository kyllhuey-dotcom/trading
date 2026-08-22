"""
Structured JSON logging helpers (LOT A — observability).

- JsonFormatter: one JSON object per log line (NDJSON), with standard
  record attributes + any custom `extra={...}` fields, exception info
  included when an exception is logged.
- setup_json_file_handler: rotating JSON file handler (size-based rotation,
  bounded backup count) so structured logs never grow unbounded.
- structured_log: convenience helper to emit a log record carrying custom
  fields without risking collisions with LogRecord reserved attributes.

Compatibility: the human-readable console format and the legacy text log
are preserved; this module is purely additive.
"""
import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

# LogRecord attributes that are already handled explicitly — never re-emitted
# from `extra` to avoid clobbering the payload.
_RESERVED = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
})

_STANDARD_KEYS = ("module", "funcName", "lineno", "process", "threadName", "pathname")


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON documents."""

    def __init__(self, include_exc: bool = True, ensure_ascii: bool = False):
        super().__init__()
        self.include_exc = include_exc
        self.ensure_ascii = ensure_ascii

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in _STANDARD_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        if self.include_exc and record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        # Custom structured fields passed via logger.log(..., extra={...})
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in payload or key.startswith("_"):
                continue
            payload[key] = value

        return json.dumps(payload, ensure_ascii=self.ensure_ascii, default=_json_default)


def _json_default(value: Any) -> Any:
    """Last-resort serializer for non-JSON-native values (sets, datetimes…)."""
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return str(value)
    except Exception:  # pragma: no cover - defensive only
        return f"<unserializable {type(value).__name__}>"


def setup_json_file_handler(path: str = "data/trading_bot.jsonl",
                            max_bytes: int = 5 * 1024 * 1024,
                            backup_count: int = 5,
                            level: int = logging.INFO) -> RotatingFileHandler:
    """Create a size-rotating NDJSON file handler (creates parent dirs)."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count,
                                  encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    return handler


def structured_log(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """
    Emit a structured log record carrying custom fields.

    Field names that collide with LogRecord reserved attributes are prefixed
    with "field_" instead of raising, so callers never crash the trading loop.
    """
    safe_fields = {("field_" + k if k in _RESERVED else k): v for k, v in fields.items()}
    try:
        logger.log(level, message, extra=safe_fields)
    except Exception:  # pragma: no cover - never let logging break the caller
        logger.log(level, "%s (fields: %r)", message, fields)
