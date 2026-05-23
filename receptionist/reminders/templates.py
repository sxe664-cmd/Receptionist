from __future__ import annotations

import html
from string import Formatter

from receptionist.config import BusinessConfig
from receptionist.reminders.models import AppointmentEvent, ReminderRecipient


def format_when(event: AppointmentEvent) -> str:
    # Windows' strftime does not support %-d / %-I.
    return event.start.strftime("%A, %B %d at %I:%M %p").replace(" 0", " ").replace(" at 0", " at ")


def _context(
    config: BusinessConfig,
    event: AppointmentEvent,
    recipient: ReminderRecipient | None = None,
    offset_days: int | None = None,
) -> dict[str, str | int]:
    return {
        "business_name": config.business.name,
        "recipient_name": recipient.display_name if recipient else "",
        "appointment_time": format_when(event),
        "offset_days": offset_days or 0,
        "default_transfer_number": config.communications.default_transfer_number or "",
    }


def _render(template: str | None, context: dict[str, str | int]) -> str | None:
    if not template:
        return None
    allowed = set(context)
    fields = {
        field_name.split(".", 1)[0].split("[", 1)[0]
        for _, field_name, _, _ in Formatter().parse(template)
        if field_name
    }
    unknown = sorted(fields - allowed)
    if unknown:
        raise ValueError(
            "Unknown message template placeholder(s): "
            + ", ".join(f"{{{name}}}" for name in unknown)
        )
    return template.format(**context)


def _html_from_text(text: str) -> str:
    return "".join(f"<p>{html.escape(part, quote=True)}</p>" for part in text.split("\n\n"))


def build_reminder_email(
    config: BusinessConfig, event: AppointmentEvent, recipient: ReminderRecipient, offset_days: int
) -> tuple[str, str, str]:
    ctx = _context(config, event, recipient, offset_days)
    templates = config.message_templates
    subject = _render(templates.reminder_email_subject, ctx) or f"Appointment reminder: {format_when(event)}"
    name = recipient.display_name
    body_text = _render(templates.reminder_email_text, ctx) or (
        f"Hi {name},\n\n"
        f"This is a reminder from {config.business.name} about your appointment "
        f"on {format_when(event)}.\n\n"
        f"If you need to make changes, please call us.\n"
    )
    configured_html = _render(templates.reminder_email_html, ctx)
    if configured_html:
        return subject, body_text, configured_html
    e = lambda s: html.escape(str(s), quote=True)
    body_html = (
        f"<p>Hi {e(name)},</p>"
        f"<p>This is a reminder from <strong>{e(config.business.name)}</strong> "
        f"about your appointment on <strong>{e(format_when(event))}</strong>.</p>"
        f"<p>If you need to make changes, please call us.</p>"
    )
    return subject, body_text, body_html


def build_reminder_sms(config: BusinessConfig, event: AppointmentEvent, offset_days: int) -> str:
    ctx = _context(config, event, offset_days=offset_days)
    return _render(config.message_templates.reminder_sms, ctx) or (
        f"{config.business.name}: reminder for your appointment on {format_when(event)}. "
        f"Reply STOP to opt out. Reply HELP for help."
    )


def build_confirmation_email(
    config: BusinessConfig, event: AppointmentEvent, recipient: ReminderRecipient
) -> tuple[str, str, str]:
    ctx = _context(config, event, recipient)
    templates = config.message_templates
    subject = _render(templates.confirmation_email_subject, ctx) or f"Appointment confirmed: {format_when(event)}"
    name = recipient.display_name
    body_text = _render(templates.confirmation_email_text, ctx) or (
        f"Hi {name},\n\n"
        f"Your appointment with {config.business.name} is confirmed for "
        f"{format_when(event)}.\n\n"
        f"If you need to make changes, please call us.\n"
    )
    configured_html = _render(templates.confirmation_email_html, ctx)
    if configured_html:
        return subject, body_text, configured_html
    e = lambda s: html.escape(str(s), quote=True)
    body_html = (
        f"<p>Hi {e(name)},</p>"
        f"<p>Your appointment with <strong>{e(config.business.name)}</strong> "
        f"is confirmed for <strong>{e(format_when(event))}</strong>.</p>"
        f"<p>If you need to make changes, please call us.</p>"
    )
    return subject, body_text, body_html


def build_confirmation_sms(config: BusinessConfig, event: AppointmentEvent) -> str:
    ctx = _context(config, event)
    return _render(config.message_templates.confirmation_sms, ctx) or (
        f"{config.business.name}: your appointment is confirmed for {format_when(event)}. "
        f"Reply STOP to opt out. Reply HELP for help."
    )
