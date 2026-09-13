import json
import logging
import re
from datetime import UTC, datetime
from logging.config import dictConfig
from typing import Any

from ticketing.core.context import get_correlation_id

_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "token",
    "password",
    "secret",
    "email",
    "phone",
    "attachment",
    "filename",
    "object_key",
    "recipient",
)
_TEXT_REDACTIONS = (
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)\b(?:access_token|refresh_token|token|password|secret)\s*[:=]\s*\S+"),
        "[REDACTED_SECRET]",
    ),
    (
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
    (
        re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{8,}\d)(?!\d)"),
        "[REDACTED_PHONE]",
    ),
)


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_text(record.getMessage()),
        }
        correlation_id = get_correlation_id()
        if correlation_id is not None:
            payload["correlation_id"] = correlation_id
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS or key in {"message", "asctime"}:
                continue
            payload[key] = _redact_value(key, value)
        if record.exc_info is not None:
            payload["exception"] = _redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _redact_value(key: str, value: Any) -> object:
    normalized_key = key.lower().replace("-", "_")
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_value(str(item_key), item) for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


def _redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in _TEXT_REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def configure_logging(level: str) -> None:
    """Configure deterministic JSON logs with context propagation and redaction."""
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {"()": RedactingJsonFormatter},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "level": level,
                }
            },
            "root": {"handlers": ["console"], "level": level},
        }
    )
    logging.getLogger(__name__).debug("Logging configured", extra={"log_level": level})
