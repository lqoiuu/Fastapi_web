import pytest
from pydantic import ValidationError

from ticketing.core.config import Settings
from ticketing.tasks.email import ConsoleEmailSender, email_sender_from_settings
from ticketing.tasks.execution import BackgroundJobExecutor


def test_background_job_time_limits_require_a_larger_hard_limit() -> None:
    with pytest.raises(ValidationError, match="hard time limit"):
        Settings(
            background_job_soft_time_limit_seconds=60,
            background_job_hard_time_limit_seconds=60,
        )


def test_background_job_retry_maximum_cannot_be_below_base() -> None:
    with pytest.raises(ValidationError, match="retry maximum"):
        Settings(
            background_job_retry_base_seconds=10,
            background_job_retry_max_seconds=5,
        )


def test_smtp_backend_requires_a_host() -> None:
    with pytest.raises(ValidationError, match="smtp_host"):
        Settings(email_backend="smtp")


def test_console_email_backend_is_explicitly_a_simulation() -> None:
    sender = email_sender_from_settings(Settings(email_backend="console"))

    assert isinstance(sender, ConsoleEmailSender)
    assert sender.backend_name == "console_simulation"


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_ticket_export_guards_against_spreadsheet_formula_injection(prefix: str) -> None:
    value = f"{prefix}SUM(1,1)"

    assert BackgroundJobExecutor._safe_csv_cell(value) == f"'{value}"
