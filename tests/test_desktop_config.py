import importlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from receptionist import desktop_config


def _minimal_business_yaml() -> str:
    return """
mode: production
business:
  name: HIRA
  type: clinic
  timezone: America/New_York
greeting: Hello
personality: Helpful
hours:
  monday: {open: "09:00", close: "17:00"}
after_hours_message: Closed
routing:
  - name: Front Desk
    description: General inquiries
faqs: []
messages:
  channels:
    - type: file
      file_path: "./messages"
"""


def test_desktop_config_uses_desktop_root_env_for_business_listing(monkeypatch, tmp_path):
    workspace = tmp_path / "runtime-workspace"
    business_dir = workspace / "config" / "businesses"
    business_dir.mkdir(parents=True)
    (business_dir / "santiago.yaml").write_text(
        "mode: production\nbusiness:\n  name: HIRA\n", encoding="utf-8"
    )

    monkeypatch.setenv("RECEPTIONIST_DESKTOP_ROOT", str(workspace))
    reloaded = importlib.reload(desktop_config)
    try:
        captured = []
        monkeypatch.setattr(reloaded, "_print_json", captured.append)

        reloaded.list_businesses(None)

        assert reloaded.PROJECT_ROOT == workspace.resolve()
        assert captured == [
            {
                "businesses": [
                    {
                        "slug": "santiago",
                        "path": "config/businesses/santiago.yaml",
                        "name": "HIRA",
                        "mode": "production",
                        "calendar_enabled": False,
                        "reminders_enabled": False,
                    }
                ]
            }
        ]
    finally:
        monkeypatch.delenv("RECEPTIONIST_DESKTOP_ROOT", raising=False)
        importlib.reload(desktop_config)


def test_desktop_config_update_relative_path_writes_workspace_copy(monkeypatch, tmp_path):
    workspace = tmp_path / "runtime-workspace"
    business_dir = workspace / "config" / "businesses"
    business_dir.mkdir(parents=True)
    workspace_config = business_dir / "santiago.yaml"
    workspace_config.write_text(_minimal_business_yaml(), encoding="utf-8")

    monkeypatch.setenv("RECEPTIONIST_DESKTOP_ROOT", str(workspace))
    reloaded = importlib.reload(desktop_config)
    try:
        args = reloaded.build_parser().parse_args(
            [
                "update",
                "--config",
                "config/businesses/santiago.yaml",
                "--mode",
                "production",
                "--default-transfer-number",
                "+15550001111",
                "--email-from",
                "HIRA <hello@example.com>",
                "--sms-from-number",
                "",
                "--confirmation-sms",
                "Confirmed for {appointment_time}",
                "--reminder-sms",
                "Reminder from {business_name}",
            ]
        )

        reloaded.update_business(args)

        updated = workspace_config.read_text(encoding="utf-8")
        assert "default_transfer_number: '+15550001111'" in updated
        assert "confirmation_sms: Confirmed for {appointment_time}" in updated
        assert list(business_dir.glob("santiago.yaml.*.bak"))
    finally:
        monkeypatch.delenv("RECEPTIONIST_DESKTOP_ROOT", raising=False)
        importlib.reload(desktop_config)


def test_desktop_config_lists_business_files_only(monkeypatch, tmp_path):
    business_dir = tmp_path / "config" / "businesses"
    business_dir.mkdir(parents=True)
    (business_dir / "clinic.yaml").write_text(
        "mode: production\nbusiness:\n  name: Santiago Receptionist\n", encoding="utf-8"
    )
    (business_dir / "contacts.yaml").write_text(
        "contacts: []\n", encoding="utf-8"
    )
    monkeypatch.setattr(desktop_config, "BUSINESS_DIR", business_dir)
    monkeypatch.setattr(desktop_config, "PROJECT_ROOT", tmp_path)

    captured = []
    monkeypatch.setattr(desktop_config, "_print_json", captured.append)

    desktop_config.list_businesses(None)

    assert captured == [
        {
            "businesses": [
                {
                    "slug": "clinic",
                    "path": "config/businesses/clinic.yaml",
                    "name": "Santiago Receptionist",
                    "mode": "production",
                    "calendar_enabled": False,
                    "reminders_enabled": False,
                }
            ]
        }
    ]


def test_desktop_config_update_preserves_valid_business_config(tmp_path):
    source = Path("config/businesses/santiago.yaml")
    target = tmp_path / "santiago.yaml"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    args = desktop_config.build_parser().parse_args(
        [
            "update",
            "--config",
            str(target),
            "--mode",
            "demo",
            "--default-transfer-number",
            "+15550001111",
            "--email-from",
            "HIRA Demo <demo@example.com>",
            "--sms-from-number",
            "+15550002222",
            "--confirmation-sms",
            "Confirmed for {appointment_time}",
            "--reminder-sms",
            "Reminder from {business_name}",
        ]
    )
    desktop_config.update_business(args)

    snapshot = desktop_config._snapshot(target)
    assert snapshot["valid"] is True
    assert snapshot["config"]["communications"] == {
        "default_transfer_number": "+15550001111",
        "email_from": "HIRA Demo <demo@example.com>",
        "sms_from_number": "+15550002222",
    }
    assert snapshot["config"]["message_templates"]["confirmation_sms"] == (
        "Confirmed for {appointment_time}"
    )
    assert snapshot["config"]["message_templates"]["reminder_sms"] == (
        "Reminder from {business_name}"
    )
    assert list(tmp_path.glob("santiago.yaml.*.bak"))


def test_desktop_config_update_persists_all_message_templates(tmp_path):
    source = Path("config/businesses/santiago.yaml")
    target = tmp_path / "santiago.yaml"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    expected_templates = {
        "confirmation_email_subject": "Confirmed: {appointment_time}",
        "confirmation_email_text": "Hi {recipient_name},\n\nConfirmed at {appointment_time}.",
        "confirmation_sms": "SMS confirmed for {recipient_name}",
        "reminder_email_subject": "Reminder from {business_name}",
        "reminder_email_text": "Hi {recipient_name},\n\nReminder for {appointment_time}.",
        "reminder_sms": "SMS reminder for {appointment_time}",
        "quick_sms": "Quick SMS for {recipient_name}",
        "quick_email": "Quick email for {business_name}\nCall {default_transfer_number}.",
        "quick_call_script": "Call script for {recipient_name}",
    }

    args = desktop_config.build_parser().parse_args(
        [
            "update",
            "--config",
            str(target),
            "--mode",
            "production",
            "--default-transfer-number",
            "+15550001111",
            "--email-from",
            "HIRA <hello@example.com>",
            "--sms-from-number",
            "+15550002222",
            "--confirmation-email-subject",
            expected_templates["confirmation_email_subject"],
            "--confirmation-email-text",
            expected_templates["confirmation_email_text"],
            "--confirmation-sms",
            expected_templates["confirmation_sms"],
            "--reminder-email-subject",
            expected_templates["reminder_email_subject"],
            "--reminder-email-text",
            expected_templates["reminder_email_text"],
            "--reminder-sms",
            expected_templates["reminder_sms"],
            "--quick-sms",
            expected_templates["quick_sms"],
            "--quick-email",
            expected_templates["quick_email"],
            "--quick-call-script",
            expected_templates["quick_call_script"],
        ]
    )

    desktop_config.update_business(args)

    snapshot = desktop_config._snapshot(target)
    assert snapshot["valid"] is True
    assert snapshot["config"]["message_templates"] == expected_templates
    assert list(tmp_path.glob("santiago.yaml.*.bak"))


def test_desktop_config_send_appointment_email_uses_attendee_email(tmp_path, monkeypatch):
    source = Path("config/businesses/santiago.yaml")
    target = tmp_path / "santiago.yaml"
    target.write_text(
        source.read_text(encoding="utf-8")
        + """

email:
  from: "HIRA <noreply@example.com>"
  sender:
    type: "smtp"
    smtp:
      host: "smtp.gmail.com"
      port: 587
      username: "user@example.com"
      password: "app-password"
      use_tls: true
""",
        encoding="utf-8",
    )

    fake_send = AsyncMock(
        return_value={
            "recipient_email": "pat@example.com",
            "recipient_name": "Pat One",
            "subject": "Appointment reminder: Tuesday",
        }
    )
    monkeypatch.setattr(desktop_config, "send_manual_appointment_email", fake_send)

    captured = []
    monkeypatch.setattr(desktop_config, "_print_json", captured.append)

    args = desktop_config.build_parser().parse_args(
        [
            "send-email",
            "--config",
            str(target),
            "--event-id",
            "evt-1",
            "--event-uid",
            "uid-1",
            "--calendar-id",
            "primary",
            "--summary",
            "Cleaning",
            "--start-iso",
            "2026-05-23T10:00:00-04:00",
            "--end-iso",
            "2026-05-23T10:30:00-04:00",
            "--timezone",
            "America/New_York",
            "--attendee-email",
            "pat@example.com",
        ]
    )

    desktop_config.send_appointment_email(args)

    assert fake_send.await_count == 1
    kwargs = fake_send.await_args.kwargs
    assert kwargs["attendee_email"] == "pat@example.com"
    assert kwargs["event"].summary == "Cleaning"
    assert kwargs["event"].event_id == "evt-1"
    assert captured == [
        {
            "ok": True,
            "recipient_email": "pat@example.com",
            "recipient_name": "Pat One",
            "subject": "Appointment reminder: Tuesday",
        }
    ]


def test_desktop_config_send_appointment_email_rejects_missing_attendee_email(tmp_path):
    source = Path("config/businesses/santiago.yaml")
    target = tmp_path / "santiago.yaml"
    target.write_text(
        source.read_text(encoding="utf-8")
        + """

email:
  from: "HIRA <noreply@example.com>"
  sender:
    type: "smtp"
    smtp:
      host: "smtp.gmail.com"
      port: 587
      username: "user@example.com"
      password: "app-password"
      use_tls: true
""",
        encoding="utf-8",
    )

    args = desktop_config.build_parser().parse_args(
        [
            "send-email",
            "--config",
            str(target),
            "--event-id",
            "evt-1",
            "--start-iso",
            "2026-05-23T10:00:00-04:00",
            "--end-iso",
            "2026-05-23T10:30:00-04:00",
            "--timezone",
            "America/New_York",
        ]
    )

    with pytest.raises(ValueError, match="attendee email"):
        desktop_config.send_appointment_email(args)


def test_desktop_config_rename_appointment_updates_google_and_store(tmp_path, monkeypatch):
    source = Path("config/businesses/santiago.yaml")
    target = tmp_path / "santiago.yaml"
    token_file = tmp_path / "google-oauth.json"
    token_file.write_text('{"token":"abc"}', encoding="utf-8")
    target.write_text(
        source.read_text(encoding="utf-8")
        + f"""

calendar:
  enabled: true
  auth:
    type: "oauth"
    oauth_token_file: "{token_file.as_posix()}"
""",
        encoding="utf-8",
    )

    fake_client = AsyncMock(return_value={"id": "evt-1", "summary": "Renamed Visit"})
    fake_store = type("FakeStore", (), {"rename_event": lambda self, **kwargs: 1})()
    ctor_calls = []

    class FakeCalendarClient:
        def __init__(self, creds, calendar_id):
            ctor_calls.append((creds, calendar_id))

        async def rename_event(self, **kwargs):
            return await fake_client(**kwargs)

    monkeypatch.setattr(desktop_config, "build_credentials", lambda auth: "creds")
    monkeypatch.setattr(desktop_config, "GoogleCalendarClient", FakeCalendarClient)
    monkeypatch.setattr(desktop_config, "ReminderStore", lambda path: fake_store)

    captured = []
    monkeypatch.setattr(desktop_config, "_print_json", captured.append)

    args = desktop_config.build_parser().parse_args(
        [
            "appointment-rename",
            "--config",
            str(target),
            "--calendar-id",
            "primary",
            "--event-id",
            "evt-1",
            "--summary",
            "Renamed Visit",
        ]
    )

    desktop_config.rename_appointment(args)

    assert fake_client.await_count == 1
    assert fake_client.await_args.kwargs == {
        "event_id": "evt-1",
        "summary": "Renamed Visit",
    }
    assert ctor_calls == [("creds", "primary")]
    assert captured == [
        {
            "ok": True,
            "event_id": "evt-1",
            "calendar_id": "primary",
            "summary": "Renamed Visit",
            "store_rows_updated": 1,
        }
    ]


def test_desktop_config_delete_appointment_updates_google_and_store(tmp_path, monkeypatch):
    source = Path("config/businesses/santiago.yaml")
    target = tmp_path / "santiago.yaml"
    token_file = tmp_path / "google-oauth.json"
    token_file.write_text('{"token":"abc"}', encoding="utf-8")
    target.write_text(
        source.read_text(encoding="utf-8")
        + f"""

calendar:
  enabled: true
  auth:
    type: "oauth"
    oauth_token_file: "{token_file.as_posix()}"
""",
        encoding="utf-8",
    )

    fake_delete = AsyncMock(return_value=None)
    fake_store = type("FakeStore", (), {"cancel_event": lambda self, **kwargs: (1, 2)})()

    class FakeCalendarClient:
        def __init__(self, creds, calendar_id):
            self.calendar_id = calendar_id

        async def delete_event(self, **kwargs):
            return await fake_delete(**kwargs)

    monkeypatch.setattr(desktop_config, "build_credentials", lambda auth: "creds")
    monkeypatch.setattr(desktop_config, "GoogleCalendarClient", FakeCalendarClient)
    monkeypatch.setattr(desktop_config, "ReminderStore", lambda path: fake_store)

    captured = []
    monkeypatch.setattr(desktop_config, "_print_json", captured.append)

    args = desktop_config.build_parser().parse_args(
        [
            "appointment-delete",
            "--config",
            str(target),
            "--calendar-id",
            "primary",
            "--event-id",
            "evt-1",
        ]
    )

    desktop_config.delete_appointment(args)

    assert fake_delete.await_count == 1
    assert fake_delete.await_args.kwargs == {"event_id": "evt-1"}
    assert captured == [
        {
            "ok": True,
            "event_id": "evt-1",
            "calendar_id": "primary",
            "store_event_rows_updated": 1,
            "store_job_rows_updated": 2,
        }
    ]


def test_desktop_config_rename_appointment_propagates_calendar_failure(tmp_path, monkeypatch):
    source = Path("config/businesses/santiago.yaml")
    target = tmp_path / "santiago.yaml"
    token_file = tmp_path / "google-oauth.json"
    token_file.write_text('{"token":"abc"}', encoding="utf-8")
    target.write_text(
        source.read_text(encoding="utf-8")
        + f"""

calendar:
  enabled: true
  auth:
    type: "oauth"
    oauth_token_file: "{token_file.as_posix()}"
""",
        encoding="utf-8",
    )

    class FakeCalendarClient:
        def __init__(self, creds, calendar_id):
            self.calendar_id = calendar_id

        async def rename_event(self, **kwargs):
            raise RuntimeError("permission denied")

    monkeypatch.setattr(desktop_config, "build_credentials", lambda auth: "creds")
    monkeypatch.setattr(desktop_config, "GoogleCalendarClient", FakeCalendarClient)

    args = desktop_config.build_parser().parse_args(
        [
            "appointment-rename",
            "--config",
            str(target),
            "--calendar-id",
            "primary",
            "--event-id",
            "evt-1",
            "--summary",
            "Renamed Visit",
        ]
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        desktop_config.rename_appointment(args)


def test_desktop_config_get_email_setup_reports_gmail_oauth(tmp_path, monkeypatch):
    source = Path("config/businesses/santiago.yaml")
    target = tmp_path / "santiago.yaml"
    token_file = tmp_path / "gmail-oauth.json"
    token_file.write_text('{"token": "abc"}', encoding="utf-8")
    target.write_text(
        source.read_text(encoding="utf-8")
        + f"""

email:
  from: "HIRA <noreply@example.com>"
  sender:
    type: "gmail_oauth"
    gmail_oauth:
      oauth_token_file: "{token_file.as_posix()}"
""",
        encoding="utf-8",
    )

    captured = []
    monkeypatch.setattr(desktop_config, "_print_json", captured.append)

    args = desktop_config.build_parser().parse_args(
        [
            "email-setup",
            "--config",
            str(target),
        ]
    )
    desktop_config.get_email_setup(args)

    assert captured == [
        {
            "from": "HIRA <noreply@example.com>",
            "sender_type": "gmail_oauth",
            "gmail_oauth_token_file": token_file.as_posix(),
            "gmail_oauth_token_set": True,
            "smtp_username": "",
            "smtp_password_set": False,
            "config_valid": True,
            "config_error": None,
        }
    ]

