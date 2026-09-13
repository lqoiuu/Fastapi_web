import json
import logging

from ticketing.core.context import correlation_context
from ticketing.core.logging import RedactingJsonFormatter


def test_json_formatter_adds_context_and_redacts_sensitive_values() -> None:
    formatter = RedactingJsonFormatter()
    record = logging.LogRecord(
        name="ticketing.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=("Bearer secret-token user@example.com called from +86 138-0013-8000"),
        args=(),
        exc_info=None,
    )
    record.authorization = "Bearer another-secret"
    record.metadata = {
        "password": "correct horse battery staple",
        "email": "private@example.com",
        "phone": "13800138000",
        "attachment_filename": "private-evidence.pdf",
        "safe_value": "visible",
    }

    with correlation_context("test-correlation-123"):
        payload = json.loads(formatter.format(record))

    serialized = json.dumps(payload)
    assert payload["correlation_id"] == "test-correlation-123"
    assert payload["metadata"]["safe_value"] == "visible"
    for secret in (
        "secret-token",
        "another-secret",
        "user@example.com",
        "private@example.com",
        "13800138000",
        "private-evidence.pdf",
        "correct horse battery staple",
    ):
        assert secret not in serialized


def test_json_formatter_keeps_operational_fields_machine_readable() -> None:
    formatter = RedactingJsonFormatter()
    record = logging.LogRecord(
        name="ticketing.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Request failed",
        args=(),
        exc_info=None,
    )
    record.http_status = 503
    record.duration_ms = 12.5

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "WARNING"
    assert payload["http_status"] == 503
    assert payload["duration_ms"] == 12.5
