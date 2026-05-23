from __future__ import annotations

from datetime import datetime

import pytest

from receptionist.reminders.calendar_apple import import_ics
from receptionist.reminders.calendar_google import (
    event_from_google,
    list_google_events,
    normalize_google_items,
)


def test_google_event_normalizes_attendees_and_recurring_instance():
    event = event_from_google(
        {
            "id": "abc",
            "iCalUID": "uid@example.com",
            "summary": "Cleaning",
            "description": "Bring insurance card",
            "start": {"dateTime": "2026-06-05T09:00:00-04:00", "timeZone": "America/New_York"},
            "end": {"dateTime": "2026-06-05T09:30:00-04:00", "timeZone": "America/New_York"},
            "attendees": [{"email": "PAT@EXAMPLE.COM"}],
            "recurringEventId": "series-1",
        },
        business_slug="acme",
        calendar_id="primary",
        timezone_name="America/New_York",
    )

    assert event.event_uid == "uid@example.com"
    assert event.attendee_emails == ("pat@example.com",)
    assert event.notes == "Bring insurance card"
    assert event.recurring is True


def test_apple_ics_import_normalizes_event(tmp_path):
    ics = tmp_path / "appt.ics"
    ics.write_text(
        """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:apple-1
SUMMARY:Consultation
DTSTART:20260605T090000
DTEND:20260605T093000
ATTENDEE:mailto:pat@example.com
END:VEVENT
END:VCALENDAR
""",
        encoding="utf-8",
    )

    events = import_ics(
        ics,
        business_slug="acme",
        timezone_name="America/New_York",
    )

    assert len(events) == 1
    assert events[0].source == "apple_ics"
    assert events[0].event_uid == "apple-1"
    assert events[0].attendee_emails == ("pat@example.com",)


class _FakeEventsListRequest:
    def __init__(self, response: dict):
        self._response = response

    def execute(self):
        return self._response


class _FakeEventsAPI:
    def __init__(self, pages: dict[str | None, dict]):
        self._pages = pages
        self.calls: list[dict] = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        page_token = kwargs.get("pageToken")
        return _FakeEventsListRequest(self._pages[page_token])


class _FakeService:
    def __init__(self, pages: dict[str | None, dict]):
        self._events = _FakeEventsAPI(pages)

    def events(self):
        return self._events


@pytest.mark.asyncio
async def test_list_google_events_paginates_and_requests_hidden_invitations():
    pages = {
        None: {
            "items": [
                {
                    "id": "evt-1",
                    "iCalUID": "uid-1@example.com",
                    "summary": "A",
                    "start": {"dateTime": "2026-06-05T09:00:00-04:00"},
                    "end": {"dateTime": "2026-06-05T09:30:00-04:00"},
                }
            ],
            "nextPageToken": "p2",
        },
        "p2": {
            "items": [
                {
                    "id": "evt-2",
                    "iCalUID": "uid-2@example.com",
                    "summary": "B",
                    "start": {"dateTime": "2026-06-06T09:00:00-04:00"},
                    "end": {"dateTime": "2026-06-06T09:30:00-04:00"},
                }
            ]
        },
    }
    service = _FakeService(pages)
    client = type("Client", (), {"_service": service})()
    events = await list_google_events(
        client,
        business_slug="acme",
        calendar_id="primary",
        time_min=datetime.fromisoformat("2026-06-01T00:00:00-04:00"),
        time_max=datetime.fromisoformat("2026-07-01T00:00:00-04:00"),
        timezone_name="America/New_York",
    )

    assert [event.event_id for event in events] == ["evt-1", "evt-2"]
    assert len(service._events.calls) == 2
    assert all(call["showHiddenInvitations"] is True for call in service._events.calls)
    assert all(call["maxResults"] == 2500 for call in service._events.calls)
    assert service._events.calls[1]["pageToken"] == "p2"


def test_normalize_google_items_drops_invalid_event():
    items = [
        {
            "id": "evt-1",
            "iCalUID": "uid-1@example.com",
            "summary": "A",
            "start": {"dateTime": "2026-06-05T09:00:00-04:00"},
            "end": {"dateTime": "2026-06-05T09:30:00-04:00"},
        },
        {
            "id": "evt-bad",
            "iCalUID": "uid-bad@example.com",
            "summary": "Broken",
            "start": {},
            "end": {},
        },
    ]
    events = normalize_google_items(
        items,
        business_slug="acme",
        calendar_id="primary",
        timezone_name="America/New_York",
    )
    assert [event.event_id for event in events] == ["evt-1"]
