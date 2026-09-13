import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as SMTPMessage
from typing import Protocol

from ticketing.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmailMessage:
    recipient: str
    subject: str
    body: str
    message_id: str


class EmailSender(Protocol):
    backend_name: str

    def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    """A local simulation that records success without contacting an email provider."""

    backend_name = "console_simulation"

    def send(self, message: EmailMessage) -> None:
        logger.info(
            "Simulated email delivery",
            extra={"message_id": message.message_id, "email_backend": self.backend_name},
        )


class SMTPEmailSender:
    backend_name = "smtp"

    def __init__(self, settings: Settings) -> None:
        if settings.smtp_host is None:
            raise ValueError("SMTP email backend requires smtp_host")
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._username = settings.smtp_username
        self._password = (
            None if settings.smtp_password is None else settings.smtp_password.get_secret_value()
        )
        self._starttls = settings.smtp_starttls
        self._sender = str(settings.email_from)

    def send(self, message: EmailMessage) -> None:
        email = SMTPMessage()
        email["From"] = self._sender
        email["To"] = message.recipient
        email["Subject"] = message.subject
        email["Message-ID"] = message.message_id
        email.set_content(message.body)
        with smtplib.SMTP(self._host, self._port, timeout=15) as client:
            if self._starttls:
                client.starttls()
            if self._username is not None:
                client.login(self._username, self._password or "")
            client.send_message(email)


def email_sender_from_settings(settings: Settings) -> EmailSender:
    if settings.email_backend == "smtp":
        return SMTPEmailSender(settings)
    return ConsoleEmailSender()
