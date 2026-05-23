from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from receptionist.config import BusinessConfig
from receptionist.reminders.contacts import ContactResolver, load_contacts, upsert_booking_contact
from receptionist.reminders.delivery import ReminderDispatcher, build_email_sender
from receptionist.reminders.models import AppointmentEvent, ReminderRecipient
from receptionist.reminders.scheduler import (
    business_slug,
    schedule_event_confirmations,
    schedule_event_reminders,
)
from receptionist.reminders.store import ReminderStore
from receptionist.reminders.templates import build_reminder_email

logger = logging.getLogger("receptionist")


def ensure_booking_reminders(
    *,
    config: BusinessConfig,
    event_id: str,
    start_iso: str,
    end_iso: str,
    caller_name: str | None = None,
    callback_number: str | None = None,
    caller_email: str | None = None,
    sms_consent_opted_in: bool = False,
) -> list[str]:
    """Idempotently ensure reminder jobs after a successful AI booking.

    This function is deliberately sync so the agent can call it after calendar
    creation without changing the lower-level calendar booking function.
    """
    if not config.reminders.enabled:
        return []
    if config.calendar is None:
        return []
    slug = business_slug(config)
    match_keys = _booking_match_keys(event_id, caller_email, callback_number)
    if caller_name and callback_number:
        upsert_booking_contact(
            config.reminders.contacts_path,
            event_id=event_id,
            caller_name=caller_name,
            callback_number=callback_number,
            caller_email=caller_email,
            sms_consent_status=(
                "opted_in"
                if config.mode == "demo" or sms_consent_opted_in
                else "unknown"
            ),
            consent_source=(
                "demo_ai_booking"
                if config.mode == "demo"
                else "ai_booking_sms_opt_in"
                if sms_consent_opted_in
                else "ai_booking"
            ),
            consent_timestamp=datetime.now().astimezone().isoformat(),
        )
    event = AppointmentEvent(
        business_slug=slug,
        source="google",
        calendar_id=config.calendar.calendar_id,
        event_id=event_id,
        event_uid=event_id,
        summary="Appointment",
        start=datetime.fromisoformat(start_iso),
        end=datetime.fromisoformat(end_iso),
        timezone=config.business.timezone,
        attendee_emails=match_keys,
    )
    contacts = load_contacts(config.reminders.contacts_path)
    store = ReminderStore(config.reminders.store_path)
    keys = schedule_event_reminders(
        config=config,
        store=store,
        event=event,
        resolver=ContactResolver(contacts),
    )
    logger.info(
        "booking reminders ensured: %d jobs for event %s",
        len(keys),
        event_id,
        extra={"component": "reminders.booking"},
    )
    return keys


async def send_booking_confirmation(
    *,
    config: BusinessConfig,
    event_id: str,
    start_iso: str,
    end_iso: str,
    caller_name: str | None = None,
    callback_number: str | None = None,
    caller_email: str | None = None,
    sms_consent_opted_in: bool = False,
) -> int:
    """Idempotently send immediate confirmation email/SMS after booking.

    Confirmations use the same structured contact and SMS consent model as
    reminders. The caller email is only used as a contact match key; SMS is
    never sent unless the matched structured contact is opted in.
    """
    if not config.reminders.enabled:
        return 0
    if config.calendar is None:
        return 0
    slug = business_slug(config)
    match_keys = _booking_match_keys(event_id, caller_email, callback_number)
    if caller_name and callback_number:
        upsert_booking_contact(
            config.reminders.contacts_path,
            event_id=event_id,
            caller_name=caller_name,
            callback_number=callback_number,
            caller_email=caller_email,
            sms_consent_status=(
                "opted_in"
                if config.mode == "demo" or sms_consent_opted_in
                else "unknown"
            ),
            consent_source=(
                "demo_ai_booking"
                if config.mode == "demo"
                else "ai_booking_sms_opt_in"
                if sms_consent_opted_in
                else "ai_booking"
            ),
            consent_timestamp=datetime.now().astimezone().isoformat(),
        )
    event = AppointmentEvent(
        business_slug=slug,
        source="google",
        calendar_id=config.calendar.calendar_id,
        event_id=event_id,
        event_uid=event_id,
        summary="Appointment",
        start=datetime.fromisoformat(start_iso),
        end=datetime.fromisoformat(end_iso),
        timezone=config.business.timezone,
        attendee_emails=match_keys,
    )
    contacts = load_contacts(config.reminders.contacts_path)
    store = ReminderStore(config.reminders.store_path)
    keys = schedule_event_confirmations(
        config=config,
        store=store,
        event=event,
        resolver=ContactResolver(contacts),
    )
    sent = await ReminderDispatcher(config, store).dispatch_due(
        now_iso=datetime.now(timezone.utc).isoformat(),
        limit=max(len(keys), 1),
    )
    logger.info(
        "booking confirmations dispatched: %d sends for event %s",
        sent,
        event_id,
        extra={"component": "reminders.confirmation"},
    )
    return sent


def _booking_match_keys(
    event_id: str, caller_email: str | None, callback_number: str | None
) -> tuple[str, ...]:
    keys = [event_id.strip().lower()]
    if caller_email:
        keys.append(caller_email.strip().lower())
    if callback_number:
        keys.append(callback_number.strip().lower())
    return tuple(dict.fromkeys(k for k in keys if k))


async def send_appointment_email(
    *,
    config: BusinessConfig,
    event: AppointmentEvent,
    attendee_email: str,
) -> dict[str, str]:
    """Send one manual email using the reminder template fields.

    This is the desktop one-off action: it does not schedule a reminder job.
    It reuses the configured email sender and the reminder subject/body
    template fields so operators get the same copy they would expect from the
    automated reminder path.
    """
    email = attendee_email.strip()
    if not email:
        raise ValueError("appointment email requires an attendee email")
    if config.email is None:
        raise RuntimeError("email configuration is required to send appointment email")
    if config.email.from_ is None:
        raise RuntimeError("email.from is required to send appointment email")

    recipient = _manual_email_recipient(config, email)
    subject, body_text, body_html = build_reminder_email(config, event, recipient, 0)
    sender = build_email_sender(config)
    await sender.send(
        from_=config.email.from_,
        to=[recipient.email or email],
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )
    logger.info(
        "manual appointment email sent",
        extra={"component": "desktop.email", "recipient": recipient.email or email, "event_id": event.event_id},
    )
    return {
        "recipient_email": recipient.email or email,
        "recipient_name": recipient.display_name,
        "subject": subject,
    }


def _manual_email_recipient(config: BusinessConfig, attendee_email: str):
    contacts = load_contacts(config.reminders.contacts_path)
    recipient = ContactResolver(contacts).match_event([attendee_email])
    if recipient is not None:
        return recipient
    display_name = _email_to_display_name(attendee_email)
    return ReminderRecipient(
        recipient_id=f"manual-{_slug(attendee_email)}",
        display_name=display_name,
        email=attendee_email,
        preferred_channels=("email",),
        match_keys=(attendee_email.strip().lower(),),
    )


def _email_to_display_name(attendee_email: str) -> str:
    local = attendee_email.split("@", 1)[0].strip()
    if not local:
        return attendee_email
    local = local.split("+", 1)[0]
    normalized = re.sub(r"[^A-Za-z0-9]+", " ", local)
    normalized = re.sub(r"([a-z])([A-Z])", r"\1 \2", normalized)
    normalized = re.sub(r"([A-Za-z])([0-9])", r"\1 \2", normalized)
    normalized = re.sub(r"([0-9])([A-Za-z])", r"\1 \2", normalized)
    parts = [part for part in normalized.split() if part]
    if len(parts) == 1 and parts[0].isalpha() and parts[0].islower() and len(parts[0]) >= 6:
        token = parts[0]
        split_at = _best_plain_name_split(token)
        if split_at:
            parts = [token[:split_at], token[split_at:]]
    display_name = " ".join(part.capitalize() for part in parts if part).strip()
    return display_name or attendee_email


def _best_plain_name_split(token: str) -> int | None:
    if len(token) < 6:
        return None
    vowels = set("aeiou")
    midpoint = len(token) / 2.0
    best_index: int | None = None
    best_score = float("-inf")
    for i in range(3, len(token) - 2):
        score = -abs(i - midpoint)
        if token[i - 1] in vowels:
            score += 1.0
        if token[i] not in vowels:
            score += 1.0
        if i in (4, 5, 6, 7):
            score += 0.25
        if score >= best_score:
            best_score = score
            best_index = i
    return best_index


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or "email"
