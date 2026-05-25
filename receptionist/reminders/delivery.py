from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from pathlib import Path

import httpx

from receptionist.config import (
    BusinessConfig,
    FakeSMSProviderConfig,
    TwilioSMSProviderConfig,
)
from receptionist.email.gmail_oauth import GmailOAuthSender
from receptionist.email.resend import ResendSender
from receptionist.email.sender import EmailSender
from receptionist.email.smtp import SMTPSender
from receptionist.reminders.models import AppointmentEvent, ReminderJob, ReminderRecipient
from receptionist.reminders.store import ReminderStore
from receptionist.reminders.templates import (
    build_confirmation_email,
    build_confirmation_sms,
    build_reminder_email,
    build_reminder_sms,
)


class FakeLog:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    async def write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")


class TwilioSMSSender:
    def __init__(self, config: TwilioSMSProviderConfig) -> None:
        self.config = config

    async def send(self, *, to: str, body: str) -> dict:
        sid = os.environ.get(self.config.account_sid_env)
        token = os.environ.get(self.config.auth_token_env)
        if not sid or not token:
            raise RuntimeError("Twilio credentials are not configured")
        payload = {"To": to, "Body": body}
        if self.config.messaging_service_sid:
            payload["MessagingServiceSid"] = self.config.messaging_service_sid
        else:
            payload["From"] = self.config.from_number or ""
        auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                data=payload,
                headers={"Authorization": f"Basic {auth}"},
            )
            response.raise_for_status()
            return response.json()


def build_email_sender(config: BusinessConfig) -> EmailSender:
    if config.email is None:
        raise RuntimeError("email config is required for configured email reminders")
    if config.email.sender.type == "smtp":
        assert config.email.sender.smtp is not None
        return SMTPSender(config.email.sender.smtp)
    if config.email.sender.type == "resend":
        assert config.email.sender.resend is not None
        return ResendSender(config.email.sender.resend)
    if config.email.sender.type == "gmail_oauth":
        assert config.email.sender.gmail_oauth is not None
        return GmailOAuthSender(config.email.sender.gmail_oauth)
    raise RuntimeError(f"unsupported email sender: {config.email.sender.type}")


class ReminderDispatcher:
    def __init__(self, config: BusinessConfig, store: ReminderStore) -> None:
        self.config = config
        self.store = store

    async def dispatch_due(self, *, now_iso: str, limit: int = 100) -> int:
        sent = 0
        for job in self.store.claim_due(now_iso, limit=limit):
            recipient = self.store.get_recipient(job.recipient_id)
            if recipient is None:
                self.store.mark_job(job.id or 0, "skipped", reason="missing_recipient")
                continue
            event = _event_from_job(job)
            try:
                if job.channel == "email":
                    await self._send_email(job, event, recipient)
                elif job.channel == "sms":
                    await self._send_sms(job, event, recipient)
                else:
                    raise RuntimeError(f"unsupported channel {job.channel}")
            except Exception as e:
                self.store.record_attempt(job.id or 0, status="failed", provider=job.channel, detail=str(e))
                self.store.mark_job(job.id or 0, "failed", reason=type(e).__name__)
            else:
                self.store.record_attempt(job.id or 0, status="sent", provider=job.channel)
                self.store.mark_job(job.id or 0, "sent")
                sent += 1
        return sent

    async def _send_email(
        self, job: ReminderJob, event: AppointmentEvent, recipient: ReminderRecipient
    ) -> None:
        if job.offset_days == 0:
            subject, body_text, body_html = build_confirmation_email(
                self.config, event, recipient
            )
        else:
            subject, body_text, body_html = build_reminder_email(
                self.config, event, recipient, job.offset_days
            )
        if self.config.reminders.email_provider == "fake":
            await FakeLog(self.config.reminders.fake_email_log_path).write(
                {
                    "channel": "email",
                    "to": recipient.email,
                    "subject": subject,
                    "body_text": body_text,
                    "job": job.idempotency_key,
                }
            )
            return
        sender = build_email_sender(self.config)
        assert self.config.email is not None
        if self.config.email.from_ is None:
            raise RuntimeError("email.from is required for configured email reminders")
        await sender.send(
            from_=self.config.email.from_,
            to=[recipient.email or ""],
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )

    async def _send_sms(
        self, job: ReminderJob, event: AppointmentEvent, recipient: ReminderRecipient
    ) -> None:
        if job.offset_days == 0:
            body = build_confirmation_sms(self.config, event, recipient)
        else:
            body = build_reminder_sms(self.config, event, job.offset_days, recipient)
        provider = self.config.sms.provider
        if isinstance(provider, FakeSMSProviderConfig):
            await FakeLog(provider.log_path).write(
                {
                    "channel": "sms",
                    "to": recipient.phone,
                    "body": body,
                    "job": job.idempotency_key,
                }
            )
            return
        if isinstance(provider, TwilioSMSProviderConfig):
            await TwilioSMSSender(provider).send(to=recipient.phone or "", body=body)
            return
        raise RuntimeError(f"unsupported sms provider: {provider.type}")


def _event_from_job(job: ReminderJob) -> AppointmentEvent:
    return AppointmentEvent(
        business_slug=job.business_slug,
        source=job.source,
        calendar_id=job.calendar_id,
        event_id=job.event_id,
        event_uid=job.event_uid,
        summary=job.event_summary or "Appointment",
        start=_parse_dt(job.event_start),
        end=_parse_dt(job.event_end),
        timezone=job.event_timezone,
    )


def _parse_dt(value: str):
    return datetime.fromisoformat(value)
