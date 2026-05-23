from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

import yaml

from receptionist.reminders.models import ReminderRecipient


def load_contacts(path: str | Path) -> list[ReminderRecipient]:
    """Load structured reminder recipients from YAML.

    The first supported production/local surface is intentionally explicit:
    calendar events may help match a contact, but they are not the source of
    SMS consent.
    """
    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rows = raw.get("contacts", raw if isinstance(raw, list) else [])
    contacts: list[ReminderRecipient] = []
    for row in rows:
        keys = set(str(v).strip().lower() for v in row.get("match_keys", []) if v)
        if row.get("email"):
            keys.add(str(row["email"]).strip().lower())
        contacts.append(
            ReminderRecipient(
                recipient_id=str(row["recipient_id"]),
                display_name=str(row.get("display_name") or row.get("name") or row["recipient_id"]),
                email=row.get("email"),
                phone=row.get("phone"),
                preferred_channels=tuple(row.get("preferred_channels", ["email", "sms"])),
                sms_consent_status=row.get("sms_consent_status", "unknown"),
                consent_source=row.get("consent_source"),
                consent_timestamp=row.get("consent_timestamp"),
                suppressed=bool(row.get("suppressed", False)),
                match_keys=tuple(sorted(keys)),
            )
        )
    return contacts


def upsert_booking_contact(
    path: str | Path,
    *,
    event_id: str,
    caller_name: str,
    callback_number: str,
    caller_email: str | None,
    sms_consent_status: str = "unknown",
    consent_source: str | None = None,
    consent_timestamp: str | None = None,
) -> ReminderRecipient:
    """Create/update a structured contact row for an AI-booked appointment.

    This bridges the live booking flow to demo reminder delivery: when the AI
    has a caller name + phone number, confirmations/reminders can match the
    just-created Google event without requiring the operator to pre-create a
    contacts YAML row by hand.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
    if isinstance(raw, list):
        rows = raw
        raw = {"contacts": rows}
    elif isinstance(raw, dict):
        rows = raw.setdefault("contacts", [])
    else:
        raw = {"contacts": []}
        rows = raw["contacts"]

    recipient_id = f"booking-{_slug(event_id)}"
    email = caller_email.strip().lower() if caller_email else None
    phone = callback_number.strip() if callback_number else None
    match_keys = {event_id.strip().lower(), recipient_id.lower()}
    if email:
        match_keys.add(email)
    if phone:
        match_keys.add(phone.lower())

    row = None
    for candidate in rows:
        candidate_keys = {
            str(v).strip().lower()
            for v in candidate.get("match_keys", [])
            if v
        }
        if (
            str(candidate.get("recipient_id", "")).lower() == recipient_id.lower()
            or event_id.strip().lower() in candidate_keys
            or (email and str(candidate.get("email", "")).strip().lower() == email)
            or (phone and str(candidate.get("phone", "")).strip().lower() == phone.lower())
        ):
            row = candidate
            break
    if row is None:
        row = {"recipient_id": recipient_id}
        rows.append(row)

    row.update(
        {
            "display_name": caller_name.strip() or "Caller",
            "email": email,
            "phone": phone,
            "preferred_channels": ["email", "sms"],
            "sms_consent_status": sms_consent_status,
            "match_keys": sorted(match_keys),
        }
    )
    if consent_source:
        row["consent_source"] = consent_source
    if consent_timestamp:
        row["consent_timestamp"] = consent_timestamp

    p.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return ContactResolver(load_contacts(p)).by_key[recipient_id.lower()]


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or "event"


class ContactResolver:
    def __init__(self, contacts: Iterable[ReminderRecipient]) -> None:
        self.contacts = list(contacts)
        self.by_key: dict[str, ReminderRecipient] = {}
        for contact in self.contacts:
            self.by_key[contact.recipient_id.lower()] = contact
            for key in contact.match_keys:
                self.by_key[key.lower()] = contact

    def match_event(self, attendee_emails: Iterable[str]) -> ReminderRecipient | None:
        for email in attendee_emails:
            contact = self.by_key.get(email.strip().lower())
            if contact:
                return contact
        return None
