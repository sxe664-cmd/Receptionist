from __future__ import annotations

import pytest

from receptionist.config import BusinessConfig


def _yaml(tmp_path, extra: str = "") -> str:
    contacts = tmp_path / "contacts.yaml"
    ics = tmp_path / "calendar.ics"
    messages = (tmp_path / "messages").as_posix()
    return f"""
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
      file_path: "{messages}"
reminders:
  enabled: true
  offset_days: [1, 4, 1]
  channels: ["email", "sms"]
  store_path: "{(tmp_path / 'reminders.sqlite3').as_posix()}"
  contacts_path: "{contacts.as_posix()}"
  calendar_sources:
    - type: apple_ics
      path: "{ics.as_posix()}"
{extra}
"""


def test_reminders_config_allows_fake_email_without_top_level_email(tmp_path):
    config = BusinessConfig.from_yaml_string(_yaml(tmp_path))

    assert config.reminders.enabled is True
    assert config.reminders.offset_days == [4, 1]
    assert config.reminders.email_provider == "fake"
    assert config.sms.provider.type == "fake"


def test_configured_reminder_email_requires_email_section(tmp_path):
    with pytest.raises(Exception, match="email_provider is configured"):
        BusinessConfig.from_yaml_string(
            _yaml(tmp_path, extra="  email_provider: configured\n")
        )


def test_twilio_sms_requires_one_sender(tmp_path):
    with pytest.raises(Exception, match="exactly one"):
        BusinessConfig.from_yaml_string(
            _yaml(
                tmp_path,
                extra="""
sms:
  provider:
    type: twilio
    from_number: "+15551234567"
    messaging_service_sid: "MG123"
""",
            )
        )


def test_production_mode_rejects_fake_reminder_delivery(tmp_path):
    with pytest.raises(Exception, match="production mode cannot use reminders.email_provider=fake"):
        BusinessConfig.from_yaml_string("mode: production\n" + _yaml(tmp_path))


def test_production_mode_allows_configured_email_and_twilio_defaults(tmp_path):
    cfg = BusinessConfig.from_yaml_string(
        "mode: production\n"
        + _yaml(
            tmp_path,
            extra="""
  email_provider: configured
communications:
  email_from: "Receptionist <prod@example.com>"
  sms_from_number: "+15551234567"
email:
  sender:
    type: smtp
    smtp:
      host: smtp.example.com
      port: 587
      username: u
      password: p
      use_tls: true
sms:
  provider:
    type: twilio
""",
        )
    )

    assert cfg.mode == "production"
    assert cfg.email is not None
    assert cfg.email.from_ == "Receptionist <prod@example.com>"
    assert cfg.sms.provider.type == "twilio"
    assert cfg.sms.provider.from_number == "+15551234567"
