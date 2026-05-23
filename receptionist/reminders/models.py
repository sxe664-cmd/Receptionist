from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ReminderChannel = Literal["email", "sms"]
ReminderStatus = Literal["scheduled", "sent", "failed", "skipped", "cancelled", "suppressed"]


@dataclass(frozen=True)
class AppointmentEvent:
    business_slug: str
    source: str
    calendar_id: str
    event_id: str
    event_uid: str
    summary: str
    start: datetime
    end: datetime
    timezone: str
    notes: str = ""
    attendee_emails: tuple[str, ...] = ()
    cancelled: bool = False
    recurring: bool = False

    @property
    def event_key(self) -> str:
        return self.event_uid or self.event_id


@dataclass(frozen=True)
class ReminderRecipient:
    recipient_id: str
    display_name: str
    email: str | None = None
    phone: str | None = None
    preferred_channels: tuple[ReminderChannel, ...] = ("email", "sms")
    sms_consent_status: Literal["unknown", "opted_in", "opted_out"] = "unknown"
    consent_source: str | None = None
    consent_timestamp: str | None = None
    suppressed: bool = False
    match_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReminderJob:
    id: int | None
    idempotency_key: str
    business_slug: str
    source: str
    calendar_id: str
    event_id: str
    event_uid: str
    event_start: str
    event_end: str
    event_timezone: str
    recipient_id: str | None
    channel: ReminderChannel
    offset_days: int
    due_at: str
    status: ReminderStatus
    reason: str | None = None
    claimed_at: str | None = None
