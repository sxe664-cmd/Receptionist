from receptionist.reminders.service import _email_to_display_name
from datetime import datetime

from receptionist.config import BusinessConfig
from receptionist.reminders.models import AppointmentEvent, ReminderRecipient
from receptionist.reminders.templates import build_reminder_email


def test_email_to_display_name_splits_dotted_local_part():
    assert _email_to_display_name("john.doe@example.com") == "John Doe"


def test_email_to_display_name_splits_camel_case_local_part():
    assert _email_to_display_name("johnDoe@example.com") == "John Doe"


def test_email_to_display_name_splits_plain_firstlast_local_part():
    assert _email_to_display_name("johndoe@example.com") == "John Doe"


def test_email_to_display_name_ignores_plus_tag():
    assert _email_to_display_name("jane.doe+new@example.com") == "Jane Doe"


def test_reminder_email_uses_encounter_title_over_email_derived_name():
    config = BusinessConfig.from_yaml_string(
        """
business:
  name: HIRA
  type: office
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
      file_path: ./messages
reminders:
  enabled: true
  channels: ["email"]
"""
    )
    config.message_templates.reminder_email_text = "Reminder for {recipient_name}"
    event = AppointmentEvent(
        business_slug="hira",
        source="google",
        calendar_id="primary",
        event_id="evt",
        event_uid="evt",
        summary="MRI Follow-up Encounter",
        start=datetime.fromisoformat("2026-06-05T09:00:00-04:00"),
        end=datetime.fromisoformat("2026-06-05T09:30:00-04:00"),
        timezone="America/New_York",
    )
    recipient = ReminderRecipient(
        recipient_id="manual",
        display_name=_email_to_display_name("john.doe@example.com"),
        email="john.doe@example.com",
        preferred_channels=("email",),
    )

    _subject, body_text, _body_html = build_reminder_email(config, event, recipient, 1)

    assert body_text == "Reminder for MRI Follow-up Encounter"
