from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from receptionist.config import BusinessConfig
from receptionist.reminders.contacts import ContactResolver, load_contacts
from receptionist.reminders.delivery import ReminderDispatcher
from receptionist.reminders.models import AppointmentEvent
from receptionist.reminders.scheduler import schedule_event_confirmations, schedule_event_reminders
from receptionist.reminders.service import ensure_booking_reminders, send_booking_confirmation
from receptionist.reminders.store import ReminderStore


def _config(tmp_path) -> BusinessConfig:
    contacts = tmp_path / "contacts.yaml"
    contacts.write_text(
        """
contacts:
  - recipient_id: pat-1
    display_name: Pat One
    email: pat@example.com
    phone: "+15551234567"
    preferred_channels: ["email", "sms"]
    sms_consent_status: opted_in
    consent_source: intake
    consent_timestamp: "2026-01-01T00:00:00Z"
    match_keys: ["pat@example.com"]
  - recipient_id: pat-2
    display_name: Pat Two
    email: two@example.com
    phone: "+15557654321"
    preferred_channels: ["email", "sms"]
    sms_consent_status: opted_out
    match_keys: ["two@example.com"]
""",
        encoding="utf-8",
    )
    return BusinessConfig.from_yaml_string(
        f"""
business:
  name: Acme Dental
  type: dental
  timezone: America/New_York
greeting: Hello
personality: Helpful
hours:
  monday: {{open: "09:00", close: "17:00"}}
after_hours_message: Closed
routing: []
faqs: []
messages:
  channels:
    - type: file
      file_path: "{(tmp_path / 'messages').as_posix()}"
reminders:
  enabled: true
  offset_days: [4, 1]
  channels: ["email", "sms"]
  store_path: "{(tmp_path / 'reminders.sqlite3').as_posix()}"
  contacts_path: "{contacts.as_posix()}"
  fake_email_log_path: "{(tmp_path / 'email.log').as_posix()}"
sms:
  provider:
    type: fake
    log_path: "{(tmp_path / 'sms.log').as_posix()}"
"""
    )


def _event(email="pat@example.com", start="2026-06-05T09:00:00-04:00"):
    return AppointmentEvent(
        business_slug="acme-dental",
        source="google",
        calendar_id="primary",
        event_id="evt-1",
        event_uid="uid-1",
        summary="Cleaning",
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat("2026-06-05T09:30:00-04:00"),
        timezone="America/New_York",
        attendee_emails=(email,),
    )


def test_schedule_event_creates_idempotent_email_and_sms_jobs(tmp_path):
    config = _config(tmp_path)
    store = ReminderStore(config.reminders.store_path)
    contacts = load_contacts(config.reminders.contacts_path)
    now = datetime(2026, 5, 20, tzinfo=ZoneInfo("America/New_York"))

    schedule_event_reminders(
        config=config,
        store=store,
        event=_event(),
        resolver=ContactResolver(contacts),
        now=now,
    )
    schedule_event_reminders(
        config=config,
        store=store,
        event=_event(),
        resolver=ContactResolver(contacts),
        now=now,
    )

    jobs = store.list_jobs()
    assert len(jobs) == 4
    assert {j.channel for j in jobs} == {"email", "sms"}
    assert {j.offset_days for j in jobs} == {4, 1}
    assert all(j.status == "scheduled" for j in jobs)


def test_list_events_filters_by_parsed_datetime_range(tmp_path):
    config = _config(tmp_path)
    store = ReminderStore(config.reminders.store_path)
    store.upsert_event(_event(start="2026-06-04T23:30:00-04:00"))
    store.upsert_event(_event(start="2026-06-05T09:00:00-04:00"))
    store.upsert_event(_event(start="2026-06-06T09:00:00-04:00"))

    events = store.list_events(
        start_iso="2026-06-05T00:00:00-04:00",
        end_iso="2026-06-06T00:00:00-04:00",
        limit=10,
    )

    assert [event["start_iso"] for event in events] == ["2026-06-05T09:00:00-04:00"]


def test_past_synced_event_is_listed_but_reminder_jobs_are_skipped(tmp_path):
    config = _config(tmp_path)
    store = ReminderStore(config.reminders.store_path)
    contacts = load_contacts(config.reminders.contacts_path)
    now = datetime(2026, 6, 5, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    event = AppointmentEvent(
        business_slug="acme-dental",
        source="google",
        calendar_id="primary",
        event_id="past-evt",
        event_uid="past-uid",
        summary="Yesterday Visit",
        start=datetime.fromisoformat("2026-06-04T09:00:00-04:00"),
        end=datetime.fromisoformat("2026-06-04T09:30:00-04:00"),
        timezone="America/New_York",
        attendee_emails=("pat@example.com",),
    )

    schedule_event_reminders(
        config=config,
        store=store,
        event=event,
        resolver=ContactResolver(contacts),
        now=now,
    )

    events = store.list_events(
        start_iso="2026-06-04T00:00:00-04:00",
        end_iso="2026-06-05T00:00:00-04:00",
        limit=10,
    )
    jobs = store.list_jobs()
    assert [item["event_id"] for item in events] == ["past-evt"]
    assert jobs
    assert all(job.status == "skipped" for job in jobs)
    assert all(job.reason == "missed_due_time" for job in jobs)


def test_sms_without_opt_in_is_suppressed(tmp_path):
    config = _config(tmp_path)
    store = ReminderStore(config.reminders.store_path)
    contacts = load_contacts(config.reminders.contacts_path)

    schedule_event_reminders(
        config=config,
        store=store,
        event=_event(email="two@example.com"),
        resolver=ContactResolver(contacts),
        now=datetime(2026, 5, 20, tzinfo=ZoneInfo("America/New_York")),
    )

    jobs = store.list_jobs()
    sms_jobs = [j for j in jobs if j.channel == "sms"]
    assert sms_jobs
    assert all(j.status == "suppressed" for j in sms_jobs)
    assert all(j.reason == "sms_not_opted_in" for j in sms_jobs)


@pytest.mark.asyncio
async def test_run_due_fake_delivery_marks_sent_and_logs(tmp_path):
    config = _config(tmp_path)
    store = ReminderStore(config.reminders.store_path)
    contacts = load_contacts(config.reminders.contacts_path)
    schedule_event_reminders(
        config=config,
        store=store,
        event=_event(),
        resolver=ContactResolver(contacts),
        now=datetime(2026, 5, 20, tzinfo=ZoneInfo("America/New_York")),
    )

    sent = await ReminderDispatcher(config, store).dispatch_due(
        now_iso="2026-06-04T13:00:00+00:00"
    )

    assert sent == 4
    assert {j.status for j in store.list_jobs()} == {"sent"}
    assert "pat@example.com" in (tmp_path / "email.log").read_text(encoding="utf-8")
    assert "+15551234567" in (tmp_path / "sms.log").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_booking_confirmation_fake_delivery_sends_once(tmp_path):
    config = _config(tmp_path)
    store = ReminderStore(config.reminders.store_path)
    contacts = load_contacts(config.reminders.contacts_path)
    now = datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("America/New_York"))

    schedule_event_confirmations(
        config=config,
        store=store,
        event=_event(),
        resolver=ContactResolver(contacts),
        now=now,
    )
    sent_first = await ReminderDispatcher(config, store).dispatch_due(
        now_iso="2026-06-01T13:00:00+00:00"
    )
    schedule_event_confirmations(
        config=config,
        store=store,
        event=_event(),
        resolver=ContactResolver(contacts),
        now=now,
    )
    sent_second = await ReminderDispatcher(config, store).dispatch_due(
        now_iso="2026-06-01T13:00:00+00:00"
    )

    jobs = store.list_jobs()
    assert sent_first == 2
    assert sent_second == 0
    assert len(jobs) == 2
    assert {j.offset_days for j in jobs} == {0}
    assert {j.status for j in jobs} == {"sent"}
    assert "Appointment confirmed" in (tmp_path / "email.log").read_text(encoding="utf-8")
    assert "your appointment is confirmed" in (tmp_path / "sms.log").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_custom_message_templates_are_used_for_confirmation_and_reminders(tmp_path):
    config = _config(tmp_path)
    config.message_templates.confirmation_email_subject = "Confirmed for {recipient_name}"
    config.message_templates.confirmation_email_text = (
        "Custom confirmation: {business_name} at {appointment_time}"
    )
    config.message_templates.confirmation_sms = (
        "Custom SMS confirmation for {recipient_name}"
    )
    config.message_templates.reminder_email_subject = "Reminder T-{offset_days}"
    config.message_templates.reminder_email_text = (
        "Custom reminder for {recipient_name}: {appointment_time}"
    )
    config.message_templates.reminder_sms = "Custom SMS reminder for {recipient_name} T-{offset_days}"
    store = ReminderStore(config.reminders.store_path)
    contacts = load_contacts(config.reminders.contacts_path)

    schedule_event_confirmations(
        config=config,
        store=store,
        event=_event(),
        resolver=ContactResolver(contacts),
        now=datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    schedule_event_reminders(
        config=config,
        store=store,
        event=_event(),
        resolver=ContactResolver(contacts),
        now=datetime(2026, 5, 20, tzinfo=ZoneInfo("America/New_York")),
    )
    await ReminderDispatcher(config, store).dispatch_due(
        now_iso="2026-06-04T13:00:00+00:00"
    )

    email_log = (tmp_path / "email.log").read_text(encoding="utf-8")
    sms_log = (tmp_path / "sms.log").read_text(encoding="utf-8")
    assert "Confirmed for Cleaning" in email_log
    assert "Custom reminder for Cleaning" in email_log
    assert "Custom confirmation: Acme Dental" in email_log
    assert "Custom SMS confirmation for Cleaning" in sms_log
    assert "Custom SMS reminder for Cleaning" in sms_log
    assert "Reminder T-1" in email_log
    assert "Custom SMS confirmation" in sms_log


@pytest.mark.asyncio
async def test_booking_confirmation_sms_requires_opt_in(tmp_path):
    config = _config(tmp_path)
    store = ReminderStore(config.reminders.store_path)
    contacts = load_contacts(config.reminders.contacts_path)

    schedule_event_confirmations(
        config=config,
        store=store,
        event=_event(email="two@example.com"),
        resolver=ContactResolver(contacts),
        now=datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    sent = await ReminderDispatcher(config, store).dispatch_due(
        now_iso="2026-06-01T13:00:00+00:00"
    )

    jobs = store.list_jobs()
    sms_jobs = [j for j in jobs if j.channel == "sms"]
    assert sent == 1
    assert sms_jobs
    assert all(j.status == "suppressed" for j in sms_jobs)
    assert all(j.reason == "sms_not_opted_in" for j in sms_jobs)
    assert "Appointment confirmed" in (tmp_path / "email.log").read_text(encoding="utf-8")
    assert not (tmp_path / "sms.log").exists()


@pytest.mark.asyncio
async def test_demo_booking_upserts_contact_and_sends_mock_sms_with_phone_only(tmp_path):
    config = _config(tmp_path)
    config.reminders.channels = ["sms"]
    (tmp_path / "fake-sa.json").write_text("{}", encoding="utf-8")
    config.calendar = BusinessConfig.from_yaml_string(
        f"""
business:
  name: Acme Dental
  type: dental
  timezone: America/New_York
greeting: Hello
personality: Helpful
hours:
  monday: {{open: "09:00", close: "17:00"}}
after_hours_message: Closed
routing: []
faqs: []
messages:
  channels:
    - type: file
      file_path: "{(tmp_path / 'messages2').as_posix()}"
calendar:
  enabled: true
  calendar_id: primary
  auth:
    type: service_account
    service_account_file: "{(tmp_path / 'fake-sa.json').as_posix()}"
"""
    ).calendar

    sent = await send_booking_confirmation(
        config=config,
        event_id="evt-demo-1",
        start_iso="2026-06-05T09:00:00-04:00",
        end_iso="2026-06-05T09:30:00-04:00",
        caller_name="Jamie Demo",
        callback_number="+15550003333",
    )
    reminder_keys = ensure_booking_reminders(
        config=config,
        event_id="evt-demo-1",
        start_iso="2026-06-05T09:00:00-04:00",
        end_iso="2026-06-05T09:30:00-04:00",
        caller_name="Jamie Demo",
        callback_number="+15550003333",
    )

    assert sent == 1
    assert reminder_keys
    contacts_yaml = (tmp_path / "contacts.yaml").read_text(encoding="utf-8")
    assert "Jamie Demo" in contacts_yaml
    assert "opted_in" in contacts_yaml
    sms_log = (tmp_path / "sms.log").read_text(encoding="utf-8")
    assert "+15550003333" in sms_log
    assert "your appointment is confirmed" in sms_log


@pytest.mark.asyncio
async def test_booking_sms_consent_flag_allows_production_booking_sms(tmp_path):
    config = _config(tmp_path)
    config.mode = "production"
    config.reminders.channels = ["sms"]
    (tmp_path / "fake-sa.json").write_text("{}", encoding="utf-8")
    config.calendar = BusinessConfig.from_yaml_string(
        f"""
business:
  name: Acme Dental
  type: dental
  timezone: America/New_York
greeting: Hello
personality: Helpful
hours:
  monday: {{open: "09:00", close: "17:00"}}
after_hours_message: Closed
routing: []
faqs: []
messages:
  channels:
    - type: file
      file_path: "{(tmp_path / 'messages3').as_posix()}"
calendar:
  enabled: true
  calendar_id: primary
  auth:
    type: service_account
    service_account_file: "{(tmp_path / 'fake-sa.json').as_posix()}"
"""
    ).calendar

    sent = await send_booking_confirmation(
        config=config,
        event_id="evt-prod-consent-1",
        start_iso="2026-06-05T09:00:00-04:00",
        end_iso="2026-06-05T09:30:00-04:00",
        caller_name="Jamie Consent",
        callback_number="+15550004444",
        sms_consent_opted_in=True,
    )

    assert sent == 1
    contacts_yaml = (tmp_path / "contacts.yaml").read_text(encoding="utf-8")
    assert "Jamie Consent" in contacts_yaml
    assert "ai_booking_sms_opt_in" in contacts_yaml
    sms_log = (tmp_path / "sms.log").read_text(encoding="utf-8")
    assert "+15550004444" in sms_log
