from __future__ import annotations

import json
from datetime import datetime

import receptionist.reminders.__main__ as reminders_main
from receptionist.reminders.models import AppointmentEvent

main = reminders_main.main


def test_cli_local_e2e_with_fixture(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config" / "businesses"
    cfg_dir.mkdir(parents=True)
    contacts = cfg_dir / "example-contacts.yaml"
    contacts.write_text(
        """
contacts:
  - recipient_id: sample
    display_name: Sample Patient
    email: patient@example.com
    phone: "+15551234567"
    preferred_channels: ["email", "sms"]
    sms_consent_status: opted_in
    match_keys: ["patient@example.com"]
""",
        encoding="utf-8",
    )
    (cfg_dir / "example.yaml").write_text(
        """
business:
  name: Example Clinic
  type: clinic
  timezone: America/New_York
greeting: Hello
personality: Helpful
hours:
  monday: {open: "09:00", close: "17:00"}
after_hours_message: Closed
routing: []
faqs: []
messages:
  channels:
    - type: file
      file_path: "./messages"
reminders:
  enabled: true
  offset_days: [4, 1]
  channels: ["email", "sms"]
  store_path: "./messages/reminders.sqlite3"
  contacts_path: "./config/businesses/example-contacts.yaml"
  fake_email_log_path: "./messages/email.log"
sms:
  provider:
    type: fake
    log_path: "./messages/sms.log"
""",
        encoding="utf-8",
    )
    fixture = tmp_path / "google.json"
    fixture.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "evt",
                        "iCalUID": "evt@example.com",
                        "summary": "Visit",
                        "start": {"dateTime": "2026-06-05T09:00:00-04:00"},
                        "end": {"dateTime": "2026-06-05T09:30:00-04:00"},
                        "attendees": [{"email": "patient@example.com"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main(["init-db", "--business", "example"]) == 0
    assert main(["contacts", "import", "--business", "example"]) == 0
    assert main(["sync", "--business", "example", "--fixture", str(fixture), "--now", "2026-05-20T09:00:00-04:00"]) == 0
    assert main(["run-due", "--business", "example", "--now", "2026-06-04T11:00:00-04:00"]) == 0
    assert main(["list", "--business", "example"]) == 0

    out = capsys.readouterr().out
    assert "Initialized reminder store" in out
    assert "Imported contacts: 1" in out
    assert "Synced events: 1" in out
    assert "Dispatched reminders: 4" in out
    assert "sent" in out
    assert "patient@example.com" in (tmp_path / "messages" / "email.log").read_text(encoding="utf-8")
    assert "+15551234567" in (tmp_path / "messages" / "sms.log").read_text(encoding="utf-8")


def test_cli_sync_uses_configured_calendar_sources(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config" / "businesses"
    cfg_dir.mkdir(parents=True)
    auth_file = tmp_path / "calendar-auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    contacts = cfg_dir / "example-contacts.yaml"
    contacts.write_text(
        """
contacts:
  - recipient_id: sample
    display_name: Sample Patient
    email: patient@example.com
    phone: "+15551234567"
    preferred_channels: ["email", "sms"]
    sms_consent_status: opted_in
    match_keys: ["patient@example.com"]
""",
        encoding="utf-8",
    )
    (cfg_dir / "example.yaml").write_text(
        f"""
business:
  name: Example Clinic
  type: clinic
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
      file_path: "./messages"
calendar:
  enabled: true
  calendar_id: google-primary
  auth:
    type: service_account
    service_account_file: "{auth_file.as_posix()}"
reminders:
  enabled: true
  offset_days: [4, 1]
  channels: ["email", "sms"]
  store_path: "./messages/reminders.sqlite3"
  contacts_path: "./config/businesses/example-contacts.yaml"
  fake_email_log_path: "./messages/email.log"
  calendar_sources:
    - type: google
      calendar_id: google-source
    - type: apple_ics
      calendar_id: apple-source
      path: "./appointments.ics"
sms:
  provider:
    type: fake
    log_path: "./messages/sms.log"
""",
        encoding="utf-8",
    )
    ics = tmp_path / "appointments.ics"
    ics.write_text(
        """
BEGIN:VCALENDAR
BEGIN:VEVENT
UID:apple-evt@example.com
SUMMARY:Visit
DTSTART:20260605T090000
DTEND:20260605T093000
ATTENDEE:mailto:patient@example.com
END:VEVENT
END:VCALENDAR
""",
        encoding="utf-8",
    )
    google_event = AppointmentEvent(
        business_slug="example-clinic",
        source="google",
        calendar_id="google-source",
        event_id="google-evt",
        event_uid="google-evt@example.com",
        summary="Google Visit",
        start=datetime.fromisoformat("2026-06-05T10:00:00-04:00"),
        end=datetime.fromisoformat("2026-06-05T10:30:00-04:00"),
        timezone="America/New_York",
        attendee_emails=("patient@example.com",),
    )

    monkeypatch.setattr(reminders_main, "build_credentials", lambda auth: object())
    monkeypatch.setattr(reminders_main, "GoogleCalendarClient", lambda creds, calendar_id: {"calendar_id": calendar_id})
    google_calls = []

    async def fake_list_google_events(client, **kwargs):
        assert client["calendar_id"] == "google-source"
        google_calls.append(kwargs)
        return [google_event]

    monkeypatch.setattr(reminders_main, "list_google_events", fake_list_google_events)

    assert main(["init-db", "--business", "example"]) == 0
    assert main(["contacts", "import", "--business", "example"]) == 0
    assert main(["sync", "--business", "example", "--now", "2026-05-20T09:00:00-04:00"]) == 0
    assert main(["run-due", "--business", "example", "--now", "2026-06-04T11:00:00-04:00"]) == 0

    out = capsys.readouterr().out
    assert "Synced events: 2" in out
    assert "Dispatched reminders: 8" in out
    assert google_calls[0]["time_min"].isoformat() == "2026-02-19T09:00:00-04:00"
    assert google_calls[0]["time_max"].isoformat() == "2026-07-19T09:00:00-04:00"
    assert "patient@example.com" in (tmp_path / "messages" / "email.log").read_text(encoding="utf-8")
    assert "+15551234567" in (tmp_path / "messages" / "sms.log").read_text(encoding="utf-8")


def test_cli_sync_fixture_with_two_future_events(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config" / "businesses"
    cfg_dir.mkdir(parents=True)
    contacts = cfg_dir / "example-contacts.yaml"
    contacts.write_text(
        """
contacts:
  - recipient_id: sample
    display_name: Sample Patient
    email: patient@example.com
    phone: "+15551234567"
    preferred_channels: ["email", "sms"]
    sms_consent_status: opted_in
    match_keys: ["patient@example.com"]
""",
        encoding="utf-8",
    )
    (cfg_dir / "example.yaml").write_text(
        """
business:
  name: Example Clinic
  type: clinic
  timezone: America/New_York
greeting: Hello
personality: Helpful
hours:
  monday: {open: "09:00", close: "17:00"}
after_hours_message: Closed
routing: []
faqs: []
messages:
  channels:
    - type: file
      file_path: "./messages"
reminders:
  enabled: true
  offset_days: [4, 1]
  channels: ["email"]
  store_path: "./messages/reminders.sqlite3"
  contacts_path: "./config/businesses/example-contacts.yaml"
""",
        encoding="utf-8",
    )
    fixture = tmp_path / "google.json"
    fixture.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "evt-1",
                        "iCalUID": "evt-1@example.com",
                        "summary": "Visit 1",
                        "start": {"dateTime": "2026-06-05T09:00:00-04:00"},
                        "end": {"dateTime": "2026-06-05T09:30:00-04:00"},
                        "attendees": [{"email": "patient@example.com"}],
                    },
                    {
                        "id": "evt-2",
                        "iCalUID": "evt-2@example.com",
                        "summary": "Visit 2",
                        "start": {"dateTime": "2026-06-06T09:00:00-04:00"},
                        "end": {"dateTime": "2026-06-06T09:30:00-04:00"},
                        "attendees": [{"email": "patient@example.com"}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main(["sync", "--business", "example", "--fixture", str(fixture), "--now", "2026-05-20T09:00:00-04:00"]) == 0
    out = capsys.readouterr().out
    assert "Synced events: 2" in out


def test_cli_sync_fixture_warns_when_normalization_drops_events(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config" / "businesses"
    cfg_dir.mkdir(parents=True)
    contacts = cfg_dir / "example-contacts.yaml"
    contacts.write_text("contacts: []\n", encoding="utf-8")
    (cfg_dir / "example.yaml").write_text(
        """
business:
  name: Example Clinic
  type: clinic
  timezone: America/New_York
greeting: Hello
personality: Helpful
hours:
  monday: {open: "09:00", close: "17:00"}
after_hours_message: Closed
routing: []
faqs: []
messages:
  channels:
    - type: file
      file_path: "./messages"
reminders:
  enabled: true
  offset_days: [4]
  channels: ["email"]
  store_path: "./messages/reminders.sqlite3"
  contacts_path: "./config/businesses/example-contacts.yaml"
""",
        encoding="utf-8",
    )
    fixture = tmp_path / "google.json"
    fixture.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "evt-1",
                        "iCalUID": "evt-1@example.com",
                        "summary": "Visit 1",
                        "start": {"dateTime": "2026-06-05T09:00:00-04:00"},
                        "end": {"dateTime": "2026-06-05T09:30:00-04:00"},
                    },
                    {
                        "id": "evt-bad",
                        "summary": "Broken",
                        "start": {},
                        "end": {},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main(["sync", "--business", "example", "--fixture", str(fixture), "--now", "2026-05-20T09:00:00-04:00"]) == 0
    out = capsys.readouterr().out
    assert "Warning: Google fixture normalization dropped 1 events (raw=2 normalized=1)" in out
    assert "Synced events: 1" in out
