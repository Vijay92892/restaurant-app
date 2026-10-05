"""Outbound email (password reset only, for now)."""

from __future__ import annotations

import logging
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage

import anyio

from app.core.config import Settings

logger = logging.getLogger("app.mail")


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


class Mailer(ABC):
    @abstractmethod
    async def send(self, message: OutgoingEmail) -> None: ...


class MemoryMailer(Mailer):
    """Test backend: keeps messages in memory."""

    def __init__(self) -> None:
        self.outbox: list[OutgoingEmail] = []

    async def send(self, message: OutgoingEmail) -> None:
        self.outbox.append(message)


class ConsoleMailer(Mailer):
    """Development backend: prints the email so the reset link can be clicked locally.
    Refused in production by Settings validation."""

    async def send(self, message: OutgoingEmail) -> None:
        print(f"\n--- email to {message.to} ---\nSubject: {message.subject}\n\n{message.body}\n")  # noqa: T201


class SmtpMailer(Mailer):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _send_sync(self, message: OutgoingEmail) -> None:
        s = self._settings
        msg = EmailMessage()
        msg["From"] = s.mail_from
        msg["To"] = message.to
        msg["Subject"] = message.subject
        msg.set_content(message.body)
        assert s.smtp_host
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_username and s.smtp_password:
                smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
            smtp.send_message(msg)

    async def send(self, message: OutgoingEmail) -> None:
        await anyio.to_thread.run_sync(self._send_sync, message)


def build_mailer(settings: Settings) -> Mailer:
    if settings.mail_backend == "smtp":
        return SmtpMailer(settings)
    if settings.mail_backend == "memory":
        return MemoryMailer()
    return ConsoleMailer()


async def send_safely(mailer: Mailer, message: OutgoingEmail) -> None:
    """Background-task wrapper: delivery failures are logged, never surfaced to the requester."""
    try:
        await mailer.send(message)
    except Exception:
        logger.exception("email delivery failed")
