from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from receptionist.booking.client import GoogleCalendarClient
from receptionist.reminders.models import AppointmentEvent

logger = logging.getLogger("receptionist")
_MAX_RESULTS_PER_PAGE = 2500


async def list_google_events(
    client: GoogleCalendarClient,
    *,
    business_slug: str,
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
    timezone_name: str,
) -> list[AppointmentEvent]:
    """List Google Calendar event instances and normalize them for reminders."""
    service = client._service  # existing client owns the Google service wrapper

    def _execute(page_token: str | None):
        query = {
            "calendarId": calendar_id,
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": _MAX_RESULTS_PER_PAGE,
            "showHiddenInvitations": True,
        }
        if page_token:
            query["pageToken"] = page_token
        return service.events().list(**query).execute()

    page_count = 0
    raw_items: list[dict] = []
    page_token: str | None = None
    while True:
        response = await asyncio.to_thread(_execute, page_token)
        page_count += 1
        raw_items.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    events = normalize_google_items(
        raw_items,
        business_slug=business_slug,
        calendar_id=calendar_id,
        timezone_name=timezone_name,
    )
    sample = [
        {"id": item.get("id", ""), "start": _start_value(item)}
        for item in raw_items[:5]
    ]
    logger.info(
        "reminders.google_sync calendar_id=%s time_min=%s time_max=%s pages=%d raw_items=%d normalized_events=%d sample=%s",
        calendar_id,
        time_min.isoformat(),
        time_max.isoformat(),
        page_count,
        len(raw_items),
        len(events),
        sample,
    )
    return events


def normalize_google_items(
    items: list[dict],
    *,
    business_slug: str,
    calendar_id: str,
    timezone_name: str,
) -> list[AppointmentEvent]:
    events: list[AppointmentEvent] = []
    for item in items:
        try:
            events.append(
                event_from_google(
                    item,
                    business_slug=business_slug,
                    calendar_id=calendar_id,
                    timezone_name=timezone_name,
                )
            )
        except Exception as exc:
            logger.warning(
                "reminders.google_sync dropped event id=%s start=%s reason=%s",
                item.get("id", ""),
                _start_value(item),
                exc,
            )
    return events


def event_from_google(
    item: dict,
    *,
    business_slug: str,
    calendar_id: str,
    timezone_name: str,
) -> AppointmentEvent:
    tz = ZoneInfo(timezone_name)
    start_raw = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
    end_raw = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
    if start_raw is None or end_raw is None:
        raise ValueError("Google event missing start/end")
    start = _parse_google_dt(start_raw, tz)
    end = _parse_google_dt(end_raw, tz)
    attendees = tuple(
        sorted(
            a.get("email", "").strip().lower()
            for a in item.get("attendees", [])
            if a.get("email")
        )
    )
    return AppointmentEvent(
        business_slug=business_slug,
        source="google",
        calendar_id=calendar_id,
        event_id=item.get("id", ""),
        event_uid=item.get("iCalUID") or item.get("id", ""),
        summary=item.get("summary") or "Appointment",
        notes=(item.get("description") or "").strip(),
        start=start,
        end=end,
        timezone=item.get("start", {}).get("timeZone") or timezone_name,
        attendee_emails=attendees,
        cancelled=item.get("status") == "cancelled",
        recurring=bool(item.get("recurringEventId")),
    )


def _start_value(item: dict) -> str:
    start = item.get("start", {})
    return str(start.get("dateTime") or start.get("date") or "")


def _parse_google_dt(value: str, tz: ZoneInfo) -> datetime:
    if "T" not in value:
        return datetime.fromisoformat(value).replace(tzinfo=tz)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
