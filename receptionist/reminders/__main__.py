from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

from receptionist.booking.auth import build_credentials
from receptionist.booking.client import GoogleCalendarClient
from receptionist.config import load_config
from receptionist.reminders.calendar_apple import import_ics
from receptionist.reminders.calendar_google import (
    list_google_events,
    normalize_google_items,
)
from receptionist.reminders.contacts import load_contacts
from receptionist.reminders.delivery import ReminderDispatcher
from receptionist.reminders.scheduler import business_slug, parse_now, sync_events
from receptionist.reminders.store import ReminderStore

DEFAULT_CONFIG_DIR = Path("config/businesses")

load_dotenv(".env.local")
load_dotenv(".env")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m receptionist.reminders")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("init-db", "sync", "run-due", "list"):
        p = sub.add_parser(name)
        p.add_argument("--business", required=True)

    contacts = sub.add_parser("contacts")
    contacts_sub = contacts.add_subparsers(dest="contacts_command", required=True)
    contacts_import = contacts_sub.add_parser("import")
    contacts_import.add_argument("--business", required=True)

    sync_p = sub.choices["sync"]
    sync_p.add_argument("--fixture", help="Google JSON fixture with items[] for local tests")
    sync_p.add_argument("--ics", help="Apple .ics file to import")
    sync_p.add_argument("--now", help="Injected current time")

    run_due = sub.choices["run-due"]
    run_due.add_argument("--now", help="Injected current time")
    run_due.add_argument("--limit", type=int, default=100)

    list_p = sub.choices["list"]
    list_p.add_argument("--status")

    args = parser.parse_args(argv)
    config = _load_business(args.business)
    store = ReminderStore(config.reminders.store_path)

    if args.command == "init-db":
        store.init_db()
        print(f"Initialized reminder store: {store.path}")
        return 0
    if args.command == "contacts":
        loaded = load_contacts(config.reminders.contacts_path)
        count = store.import_recipients(loaded)
        print(f"Imported contacts: {count}")
        return 0
    if args.command == "sync":
        count = asyncio.run(_sync(config, store, fixture=args.fixture, ics=args.ics, now=args.now))
        print(f"Synced events: {count}")
        return 0
    if args.command == "run-due":
        now = parse_now(args.now, config.business.timezone).astimezone(__import__("datetime").timezone.utc)
        sent = asyncio.run(ReminderDispatcher(config, store).dispatch_due(now_iso=now.isoformat(), limit=args.limit))
        print(f"Dispatched reminders: {sent}")
        return 0
    if args.command == "list":
        for job in store.list_jobs(status=args.status):
            print(f"{job.id}\t{job.status}\t{job.channel}\t{job.due_at}\t{job.reason or ''}\t{job.idempotency_key}")
        return 0
    return 2


async def _sync(config, store: ReminderStore, *, fixture: str | None, ics: str | None, now: str | None) -> int:
    contacts = load_contacts(config.reminders.contacts_path)
    slug = business_slug(config)
    current = parse_now(now, config.business.timezone)
    events = []
    if fixture:
        raw = json.loads(Path(fixture).read_text(encoding="utf-8"))
        items = raw.get("items", raw if isinstance(raw, list) else [])
        events = normalize_google_items(
            items,
            business_slug=slug,
            calendar_id=(config.calendar.calendar_id if config.calendar else "primary"),
            timezone_name=config.business.timezone,
        )
        if len(events) < len(items):
            print(
                f"Warning: Google fixture normalization dropped {len(items) - len(events)} events "
                f"(raw={len(items)} normalized={len(events)})"
            )
    elif ics:
        events = import_ics(ics, business_slug=slug, timezone_name=config.business.timezone)
    else:
        events = await _load_configured_events(config, slug=slug, current=current)
    return sync_events(config=config, store=store, events=events, contacts=contacts, now=current)


async def _load_configured_events(config, *, slug: str, current):
    """Load reminder source events from the business config.

    If reminder sources are configured, those are the source of truth. We keep
    the legacy single Google Calendar path as a fallback for older configs that
    have not been migrated yet.
    """
    sources = list(config.reminders.calendar_sources)
    lookback = current - timedelta(days=config.reminders.lookback_days)
    lookahead = current + timedelta(days=config.reminders.lookahead_days)
    events = []

    if sources:
        for source in sources:
            if source.type == "google":
                if config.calendar is None or not config.calendar.enabled:
                    raise RuntimeError("reminders calendar source google requires calendar.enabled")
                creds = build_credentials(config.calendar.auth)
                client = GoogleCalendarClient(creds, source.calendar_id)
                events.extend(
                    await list_google_events(
                        client,
                        business_slug=slug,
                        calendar_id=source.calendar_id,
                        time_min=lookback,
                        time_max=lookahead,
                        timezone_name=config.business.timezone,
                    )
                )
            elif source.type == "apple_ics":
                if not source.path:
                    raise RuntimeError("apple_ics reminder calendar source requires path")
                events.extend(
                    import_ics(
                        source.path,
                        business_slug=slug,
                        calendar_id=source.calendar_id,
                        timezone_name=config.business.timezone,
                    )
                )
            else:
                raise RuntimeError(f"unsupported reminder calendar source: {source.type}")
        return events

    if config.calendar is None or not config.calendar.enabled:
        raise RuntimeError("Google sync requires calendar.enabled or use --fixture/--ics")

    creds = build_credentials(config.calendar.auth)
    client = GoogleCalendarClient(creds, config.calendar.calendar_id)
    return await list_google_events(
        client,
        business_slug=slug,
        calendar_id=config.calendar.calendar_id,
        time_min=lookback,
        time_max=lookahead,
        timezone_name=config.business.timezone,
    )


def _load_business(slug: str):
    if not slug.replace("-", "").replace("_", "").isalnum():
        raise SystemExit(f"Invalid business slug: {slug!r}")
    path = DEFAULT_CONFIG_DIR / f"{slug}.yaml"
    if not path.exists():
        raise SystemExit(f"Business config not found: {path}")
    return load_config(path)


if __name__ == "__main__":
    sys.exit(main())
