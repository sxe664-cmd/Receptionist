"""Small JSON helper API used by the Electron desktop console.

This module intentionally keeps the desktop app thin: Electron handles the
window/process controls, while Python owns YAML parsing and BusinessConfig
validation so the UI reports the same errors the agent would hit at runtime.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from receptionist.booking.auth import build_credentials
from receptionist.booking.client import GoogleCalendarClient
from receptionist.config import ConfigError, load_config
from receptionist.reminders.models import AppointmentEvent
from receptionist.reminders.store import ReminderStore
from receptionist.reminders.service import send_appointment_email as send_manual_appointment_email

PROJECT_ROOT = Path(
    os.environ.get("RECEPTIONIST_DESKTOP_ROOT") or Path(__file__).resolve().parents[1]
).expanduser().resolve()
BUSINESS_DIR = PROJECT_ROOT / "config" / "businesses"
ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"

load_dotenv(PROJECT_ROOT / ".env.local")
load_dotenv(PROJECT_ROOT / ".env")


def _to_project_path(path: str | Path) -> Path:
    raw = Path(path)
    resolved = raw if raw.is_absolute() else PROJECT_ROOT / raw
    return resolved.resolve()


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError("Business YAML must be a mapping at the top level")
    return data


def _normalize_business_mode(path: Path) -> None:
    try:
        data = _read_yaml(path)
    except Exception:
        return
    if data.get("mode") == "production":
        return
    text = path.read_text(encoding="utf-8")
    text = _set_top_level_scalar(text, "mode", "production")
    path.write_text(text, encoding="utf-8")


def _safe_get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _snapshot(path: Path) -> dict[str, Any]:
    _normalize_business_mode(path)
    data = _read_yaml(path)
    valid = True
    error = None
    try:
        validated = load_config(path)
    except Exception as exc:  # UI should show validation failures, not crash.
        valid = False
        error = str(exc)
        validated = None

    sms_provider = _safe_get(data, "sms", "provider", default={}) or {}
    if not isinstance(sms_provider, dict):
        sms_provider = {}
    reminders = _safe_get(data, "reminders", default={}) or {}
    if not isinstance(reminders, dict):
        reminders = {}
    calendar = _safe_get(data, "calendar", default={}) or {}
    if not isinstance(calendar, dict):
        calendar = {}
    email = _safe_get(data, "email", default={}) or {}
    if not isinstance(email, dict):
        email = {}

    return {
        "path": _rel(path),
        "slug": path.stem,
        "valid": valid,
        "error": error,
        "config": {
            "mode": "production",
            "business_name": _safe_get(data, "business", "name", default=path.stem),
            "communications": data.get("communications", {}) or {},
            "message_templates": data.get("message_templates", {}) or {},
            "reminders": {
                "enabled": bool(reminders.get("enabled", False)),
                "channels": reminders.get("channels", []),
                "email_provider": reminders.get("email_provider"),
            },
            "calendar": {
                "enabled": bool(calendar.get("enabled", False)),
                "calendar_id": calendar.get("calendar_id"),
            },
            "sms_provider": {
                "type": sms_provider.get("type", "fake"),
                "from_number": sms_provider.get("from_number"),
                "messaging_service_sid": sms_provider.get("messaging_service_sid"),
            },
            "email": {
                "from": email.get("from") or email.get("from_"),
                "configured": bool(email),
                "sender_type": validated.email.sender.type if validated and validated.email else None,
                "smtp_username_set": bool(_env_value("SMTP_USERNAME")),
                "smtp_password_set": bool(_env_value("SMTP_PASSWORD")),
                "gmail_oauth_token_set": bool(
                    validated
                    and validated.email
                    and validated.email.sender.type == "gmail_oauth"
                    and Path(validated.email.sender.gmail_oauth.oauth_token_file).expanduser().exists()
                ),
            },
            "validated_business_name": validated.business.name if validated else None,
        },
    }


def list_businesses(_args: argparse.Namespace) -> None:
    BUSINESS_DIR.mkdir(parents=True, exist_ok=True)
    businesses = []
    for path in sorted([*BUSINESS_DIR.glob("*.yaml"), *BUSINESS_DIR.glob("*.yml")]):
        try:
            _normalize_business_mode(path)
            data = _read_yaml(path)
            if not isinstance(data.get("business"), dict):
                continue
            name = _safe_get(data, "business", "name", default=path.stem)
            slug = path.stem
        except Exception:
            continue
        mode = data.get("mode", "production")
        if slug.startswith("example-"):
            continue
        if mode != "production":
            continue
        businesses.append({"slug": slug, "path": _rel(path), "name": name, "mode": mode})
        calendar_enabled = bool(_safe_get(data, "calendar", "enabled", default=False))
        reminders_enabled = bool(_safe_get(data, "reminders", "enabled", default=False))
        businesses[-1]["calendar_enabled"] = calendar_enabled
        businesses[-1]["reminders_enabled"] = reminders_enabled
    _print_json({"businesses": businesses})


def get_business(args: argparse.Namespace) -> None:
    _print_json(_snapshot(_to_project_path(args.config)))


def list_appointments(args: argparse.Namespace) -> None:
    config = load_config(_to_project_path(args.config))
    if not config.reminders.enabled:
        _print_json({"appointments": []})
        return
    appointments = ReminderStore(config.reminders.store_path).list_events(
        limit=args.limit,
        start_iso=args.start_iso,
        end_iso=args.end_iso,
    )
    _print_json({"appointments": appointments})


def rename_appointment(args: argparse.Namespace) -> None:
    path = _to_project_path(args.config)
    config = load_config(path)
    if config.calendar is None or not config.calendar.enabled:
        raise ValueError("appointment rename requires calendar.enabled")
    summary = (args.summary or "").strip()
    if not summary:
        raise ValueError("appointment rename requires a non-empty summary")

    creds = build_credentials(config.calendar.auth)
    client = GoogleCalendarClient(creds, args.calendar_id or config.calendar.calendar_id)
    result = asyncio.run(
        client.rename_event(
            event_id=args.event_id,
            summary=summary,
        )
    )
    updated = ReminderStore(config.reminders.store_path).rename_event(
        business_slug=path.stem,
        source="google",
        calendar_id=args.calendar_id or config.calendar.calendar_id,
        event_id=args.event_id,
        summary=summary,
    )
    _print_json(
        {
            "ok": True,
            "event_id": args.event_id,
            "calendar_id": args.calendar_id or config.calendar.calendar_id,
            "summary": result.get("summary", summary),
            "store_rows_updated": updated,
        }
    )


def delete_appointment(args: argparse.Namespace) -> None:
    path = _to_project_path(args.config)
    config = load_config(path)
    if config.calendar is None or not config.calendar.enabled:
        raise ValueError("appointment delete requires calendar.enabled")

    creds = build_credentials(config.calendar.auth)
    calendar_id = args.calendar_id or config.calendar.calendar_id
    client = GoogleCalendarClient(creds, calendar_id)
    asyncio.run(client.delete_event(event_id=args.event_id))
    event_rows_updated, job_rows_updated = ReminderStore(config.reminders.store_path).cancel_event(
        business_slug=path.stem,
        source="google",
        calendar_id=calendar_id,
        event_id=args.event_id,
    )
    _print_json(
        {
            "ok": True,
            "event_id": args.event_id,
            "calendar_id": calendar_id,
            "store_event_rows_updated": event_rows_updated,
            "store_job_rows_updated": job_rows_updated,
        }
    )


def send_appointment_email(args: argparse.Namespace) -> None:
    path = _to_project_path(args.config)
    config = load_config(path)
    attendee_email = (args.attendee_email or "").strip()
    if not attendee_email:
        raise ValueError("appointment email requires an attendee email")
    event = AppointmentEvent(
        business_slug=path.stem,
        source="google",
        calendar_id=args.calendar_id or "primary",
        event_id=args.event_id,
        event_uid=args.event_uid or args.event_id,
        summary=args.summary or "Appointment",
        start=datetime.fromisoformat(args.start_iso),
        end=datetime.fromisoformat(args.end_iso),
        timezone=args.timezone,
        attendee_emails=(attendee_email,),
    )
    result = asyncio.run(
        send_manual_appointment_email(
            config=config,
            event=event,
            attendee_email=attendee_email,
        )
    )
    _print_json(
        {
            "ok": True,
            "recipient_email": result["recipient_email"],
            "recipient_name": result["recipient_name"],
            "subject": result["subject"],
        }
    )


def get_email_setup(args: argparse.Namespace) -> None:
    path = _to_project_path(args.config)
    snapshot = _snapshot(path)
    validated = None
    try:
        validated = load_config(path)
    except Exception:
        pass
    sender_type = validated.email.sender.type if validated and validated.email else None
    gmail_token_file = (
        validated.email.sender.gmail_oauth.oauth_token_file
        if validated and validated.email and validated.email.sender.type == "gmail_oauth"
        else ""
    )
    smtp_username = _env_value("SMTP_USERNAME") or ""
    smtp_password_set = bool(_env_value("SMTP_PASSWORD"))
    if sender_type != "smtp":
        smtp_username = ""
        smtp_password_set = False
    _print_json({
        "from": _safe_get(_read_yaml(path), "email", "from")
        or _safe_get(_read_yaml(path), "communications", "email_from")
        or "",
        "sender_type": sender_type or "",
        "gmail_oauth_token_file": gmail_token_file,
        "gmail_oauth_token_set": bool(gmail_token_file and Path(gmail_token_file).expanduser().exists()),
        "smtp_username": smtp_username,
        "smtp_password_set": smtp_password_set,
        "config_valid": snapshot["valid"],
        "config_error": snapshot["error"],
    })


def update_email_setup(args: argparse.Namespace) -> None:
    path = _to_project_path(args.config)
    original = path.read_text(encoding="utf-8")
    text = original
    if args.from_address:
        if not re.search(r"^email\s*:\s*(?:#.*)?$", text, re.MULTILINE):
            text = text.rstrip() + (
                "\n\nemail:\n"
                f"  from: {_yaml_scalar(args.from_address)}\n"
                "  sender:\n"
                "    type: \"smtp\"\n"
                "    smtp:\n"
                "      host: \"smtp.gmail.com\"\n"
                "      port: 587\n"
                "      username: ${SMTP_USERNAME}\n"
                "      password: ${SMTP_PASSWORD}\n"
                "      use_tls: true\n"
                "  triggers:\n"
                "    on_message: true\n"
                "    on_call_end: false\n"
                "    on_booking: false\n"
            )
        text = _set_mapping_value(text, "communications", "email_from", args.from_address)
        text = _set_mapping_value(text, "email", "from", args.from_address)

    env_updates = {}
    if args.smtp_username:
        env_updates["SMTP_USERNAME"] = args.smtp_username
    if args.smtp_password:
        env_updates["SMTP_PASSWORD"] = args.smtp_password
    if env_updates:
        _write_env_local(env_updates)
        for key, value in env_updates.items():
            os.environ[key] = value

    backup_path = _backup(path)
    path.write_text(text, encoding="utf-8")
    snapshot = _snapshot(path)
    snapshot["backup_path"] = _rel(backup_path)
    _print_json(snapshot)


def _env_value(key: str) -> str | None:
    value = os.environ.get(key)
    return value if value else None


def _quote_env(value: str) -> str:
    if re.search(r"\s|#|=|\"", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _write_env_local(values: dict[str, str]) -> None:
    ENV_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ENV_LOCAL_PATH.read_text(encoding="utf-8").splitlines() if ENV_LOCAL_PATH.exists() else []
    seen = set()
    out = []
    for line in lines:
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=", line)
        if match and match.group(1) in values:
            key = match.group(1)
            out.append(f"{key}={_quote_env(values[key])}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={_quote_env(value)}")
    ENV_LOCAL_PATH.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _set_mapping_value(text: str, section: str, key: str, value: str) -> str:
    lines = text.splitlines()
    section_start = None
    for index, line in enumerate(lines):
        if re.match(rf"^{re.escape(section)}\s*:\s*(?:#.*)?$", line):
            section_start = index
            break
    if section_start is None:
        return text.rstrip() + f"\n\n{section}:\n  {key}: {_yaml_scalar(value)}\n"

    end = section_start + 1
    key_index = None
    while end < len(lines):
        line = lines[end]
        if line and not line.startswith((" ", "\t")) and not line.lstrip().startswith("#"):
            break
        if re.match(rf"^\s{{2}}{re.escape(key)}\s*:", line):
            key_index = end
        end += 1

    new_line = f"  {key}: {_yaml_scalar(value)}"
    if key_index is not None:
        lines[key_index] = new_line
    else:
        lines.insert(section_start + 1, new_line)
    return "\n".join(lines) + "\n"


def _backup(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_suffix(path.suffix + f".{stamp}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def _yaml_scalar(value: str) -> str:
    dumped = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True).strip()
    lines = [line for line in dumped.splitlines() if line.strip() != "..."]
    return " ".join(lines).strip() or "''"


def _yaml_mapping_lines(key: str, values: dict[str, str]) -> list[str]:
    lines = [f"{key}:"]
    for name, value in values.items():
        if "\n" in value:
            chomping = "|" if value.endswith("\n") else "|-"
            lines.append(f"  {name}: {chomping}")
            for line in value.splitlines():
                lines.append(f"    {line}")
            if value.endswith("\n"):
                lines.append("    ")
        else:
            lines.append(f"  {name}: {_yaml_scalar(value)}")
    return lines


def _set_top_level_scalar(text: str, key: str, value: str) -> str:
    line = f"{key}: {_yaml_scalar(value)}"
    pattern = re.compile(rf"^(?P<prefix>{re.escape(key)}\s*:\s*).*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    return line + "\n\n" + text


def _set_mapping_block(text: str, key: str, values: dict[str, str]) -> str:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}\s*:\s*(?:#.*)?$", line):
            start = index
            break
    block_lines = _yaml_mapping_lines(key, values)
    if start is None:
        insert_at = 0
        for index, line in enumerate(lines):
            if re.match(r"^business\s*:\s*(?:#.*)?$", line):
                insert_at = index
                break
        return "\n".join(lines[:insert_at] + block_lines + [""] + lines[insert_at:]) + "\n"

    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line and not line.startswith((" ", "\t")) and not line.lstrip().startswith("#"):
            break
        end += 1
    return "\n".join(lines[:start] + block_lines + lines[end:]) + "\n"


def update_business(args: argparse.Namespace) -> None:
    path = _to_project_path(args.config)
    _normalize_business_mode(path)
    original = path.read_text(encoding="utf-8")
    text = original
    if args.mode not in (None, "production"):
        raise ValueError("desktop only supports production mode")
    text = _set_top_level_scalar(text, "mode", "production")
    comms = {
        "default_transfer_number": args.default_transfer_number or "",
        "email_from": args.email_from or "",
        "sms_from_number": args.sms_from_number or "",
    }
    text = _set_mapping_block(text, "communications", comms)
    template_values = {
        "confirmation_email_subject": args.confirmation_email_subject or "",
        "confirmation_email_text": args.confirmation_email_text or "",
        "confirmation_sms": args.confirmation_sms or "",
        "reminder_email_subject": args.reminder_email_subject or "",
        "reminder_email_text": args.reminder_email_text or "",
        "reminder_sms": args.reminder_sms or "",
        "quick_sms": args.quick_sms or "",
        "quick_email": args.quick_email or "",
        "quick_call_script": args.quick_call_script or "",
    }
    text = _set_mapping_block(text, "message_templates", template_values)

    backup_path = _backup(path)
    path.write_text(text, encoding="utf-8")
    snapshot = _snapshot(path)
    snapshot["backup_path"] = _rel(backup_path)
    _print_json(snapshot)


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AIReceptionist desktop console helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-businesses")
    list_parser.set_defaults(func=list_businesses)

    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("--config", required=True)
    get_parser.set_defaults(func=get_business)

    appointments_parser = subparsers.add_parser("appointments")
    appointments_parser.add_argument("--config", required=True)
    appointments_parser.add_argument("--limit", type=int, default=25)
    appointments_parser.add_argument("--start-iso", default=None)
    appointments_parser.add_argument("--end-iso", default=None)
    appointments_parser.set_defaults(func=list_appointments)

    rename_parser = subparsers.add_parser("appointment-rename")
    rename_parser.add_argument("--config", required=True)
    rename_parser.add_argument("--calendar-id", default="primary")
    rename_parser.add_argument("--event-id", required=True)
    rename_parser.add_argument("--summary", required=True)
    rename_parser.set_defaults(func=rename_appointment)

    delete_parser = subparsers.add_parser("appointment-delete")
    delete_parser.add_argument("--config", required=True)
    delete_parser.add_argument("--calendar-id", default="primary")
    delete_parser.add_argument("--event-id", required=True)
    delete_parser.set_defaults(func=delete_appointment)

    send_email_parser = subparsers.add_parser("send-email")
    send_email_parser.add_argument("--config", required=True)
    send_email_parser.add_argument("--event-id", required=True)
    send_email_parser.add_argument("--event-uid", default="")
    send_email_parser.add_argument("--calendar-id", default="primary")
    send_email_parser.add_argument("--summary", default="Appointment")
    send_email_parser.add_argument("--start-iso", required=True)
    send_email_parser.add_argument("--end-iso", required=True)
    send_email_parser.add_argument("--timezone", required=True)
    send_email_parser.add_argument("--attendee-email", default="")
    send_email_parser.set_defaults(func=send_appointment_email)

    email_get_parser = subparsers.add_parser("email-setup")
    email_get_parser.add_argument("--config", required=True)
    email_get_parser.set_defaults(func=get_email_setup)

    email_update_parser = subparsers.add_parser("email-update")
    email_update_parser.add_argument("--config", required=True)
    email_update_parser.add_argument("--from-address", default="")
    email_update_parser.add_argument("--smtp-username", default="")
    email_update_parser.add_argument("--smtp-password", default="")
    email_update_parser.set_defaults(func=update_email_setup)

    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("--config", required=True)
    update_parser.add_argument("--mode", choices=["demo", "production"])
    update_parser.add_argument("--default-transfer-number", default="")
    update_parser.add_argument("--email-from", default="")
    update_parser.add_argument("--sms-from-number", default="")
    update_parser.add_argument("--confirmation-email-subject", default="")
    update_parser.add_argument("--confirmation-email-text", default="")
    update_parser.add_argument("--confirmation-sms", default="")
    update_parser.add_argument("--reminder-email-subject", default="")
    update_parser.add_argument("--reminder-email-text", default="")
    update_parser.add_argument("--reminder-sms", default="")
    update_parser.add_argument("--quick-sms", default="")
    update_parser.add_argument("--quick-email", default="")
    update_parser.add_argument("--quick-call-script", default="")
    update_parser.set_defaults(func=update_business)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
