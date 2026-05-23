from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from receptionist.config import BusinessConfig
from receptionist.reminders.contacts import ContactResolver
from receptionist.reminders.models import AppointmentEvent, ReminderRecipient
from receptionist.reminders.store import ReminderStore


def business_slug(config: BusinessConfig) -> str:
    return re.sub(r"[^a-z0-9]+", "-", config.business.name.lower()).strip("-") or "business"


def parse_now(now: str | None, tz_name: str) -> datetime:
    if now:
        dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        return dt
    return datetime.now(ZoneInfo(tz_name))


def due_at_for(event: AppointmentEvent, offset_days: int) -> datetime:
    due = event.start - timedelta(days=offset_days)
    if due.tzinfo is None:
        due = due.replace(tzinfo=ZoneInfo(event.timezone))
    return due.astimezone(timezone.utc)


def schedule_event_reminders(
    *,
    config: BusinessConfig,
    store: ReminderStore,
    event: AppointmentEvent,
    resolver: ContactResolver,
    now: datetime | None = None,
) -> list[str]:
    """Create/update reminder jobs for one event.

    Events are appointments only; recipients and SMS consent come from the
    structured resolver. Missing data becomes skipped/suppressed jobs rather
    than guessed sends.
    """
    if not config.reminders.enabled:
        return []
    now = now or datetime.now(ZoneInfo(config.business.timezone))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(config.business.timezone))

    store.upsert_event(event)
    if event.cancelled:
        store.cancel_jobs_for_event(event, "event_cancelled")
        return []

    recipient = resolver.match_event(event.attendee_emails)
    if recipient is not None:
        store.import_recipients([recipient])
    keys: list[str] = []
    for offset in config.reminders.offset_days:
        due = due_at_for(event, offset)
        for channel in config.reminders.channels:
            status, reason = _delivery_status(config, recipient, channel)
            if due < now.astimezone(timezone.utc) and not config.reminders.allow_retroactive_send:
                status = "skipped"
                reason = "missed_due_time"
            keys.append(
                store.upsert_job(
                    event=event,
                    recipient=recipient,
                    channel=channel,
                    offset_days=offset,
                    due_at=due.isoformat(),
                    status=status,
                    reason=reason,
                )
            )
    return keys


def schedule_event_confirmations(
    *,
    config: BusinessConfig,
    store: ReminderStore,
    event: AppointmentEvent,
    resolver: ContactResolver,
    now: datetime | None = None,
) -> list[str]:
    """Create/update immediate confirmation jobs for one booked appointment.

    Confirmation jobs use offset_days=0 so they share the existing reminder
    ledger/idempotency/dispatch path without colliding with T-4/T-1 reminders.
    Recipient and SMS consent rules are identical to reminders.
    """
    if not config.reminders.enabled:
        return []
    now = now or datetime.now(ZoneInfo(config.business.timezone))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(config.business.timezone))

    store.upsert_event(event)
    if event.cancelled:
        store.cancel_jobs_for_event(event, "event_cancelled")
        return []

    recipient = resolver.match_event(event.attendee_emails)
    if recipient is not None:
        store.import_recipients([recipient])

    keys: list[str] = []
    due = now.astimezone(timezone.utc)
    for channel in config.reminders.channels:
        status, reason = _delivery_status(config, recipient, channel)
        keys.append(
            store.upsert_job(
                event=event,
                recipient=recipient,
                channel=channel,
                offset_days=0,
                due_at=due.isoformat(),
                status=status,
                reason=reason,
            )
        )
    return keys


def sync_events(
    *,
    config: BusinessConfig,
    store: ReminderStore,
    events: list[AppointmentEvent],
    contacts: list[ReminderRecipient],
    now: datetime | None = None,
) -> int:
    resolver = ContactResolver(contacts)
    count = 0
    for event in events:
        schedule_event_reminders(config=config, store=store, event=event, resolver=resolver, now=now)
        count += 1
    return count


def _delivery_status(
    config: BusinessConfig, recipient: ReminderRecipient | None, channel: str
) -> tuple[str, str | None]:
    if recipient is None:
        return "skipped", "missing_recipient"
    if recipient.suppressed:
        return "suppressed", "recipient_suppressed"
    if channel not in recipient.preferred_channels:
        return "skipped", "channel_not_preferred"
    if channel == "email":
        if not recipient.email:
            return "skipped", "missing_email"
        return "scheduled", None
    if channel == "sms":
        if not recipient.phone:
            return "skipped", "missing_phone"
        if recipient.sms_consent_status != "opted_in":
            return "suppressed", "sms_not_opted_in"
        return "scheduled", None
    return "skipped", "unsupported_channel"
