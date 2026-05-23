from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from receptionist.reminders.models import AppointmentEvent, ReminderJob, ReminderRecipient

SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_event_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def make_idempotency_key(
    *,
    business_slug: str,
    source: str,
    calendar_id: str,
    event_uid_or_id: str,
    event_start: str,
    offset_days: int,
    channel: str,
) -> str:
    return "|".join(
        [
            business_slug,
            source,
            calendar_id,
            event_uid_or_id,
            event_start,
            str(offset_days),
            channel,
        ]
    )


class ReminderStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        if self.path.parent:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recipients (
                    recipient_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    email TEXT,
                    phone TEXT,
                    preferred_channels TEXT NOT NULL,
                    sms_consent_status TEXT NOT NULL,
                    consent_source TEXT,
                    consent_timestamp TEXT,
                    suppressed INTEGER NOT NULL DEFAULT 0,
                    match_keys TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    business_slug TEXT NOT NULL,
                    source TEXT NOT NULL,
                    calendar_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_uid TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    start_iso TEXT NOT NULL,
                    end_iso TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    attendee_emails TEXT NOT NULL,
                    cancelled INTEGER NOT NULL DEFAULT 0,
                    recurring INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (business_slug, source, calendar_id, event_id, start_iso)
                );
                CREATE TABLE IF NOT EXISTS reminder_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    business_slug TEXT NOT NULL,
                    source TEXT NOT NULL,
                    calendar_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_uid TEXT NOT NULL,
                    event_start TEXT NOT NULL,
                    event_end TEXT NOT NULL,
                    event_timezone TEXT NOT NULL,
                    recipient_id TEXT,
                    channel TEXT NOT NULL,
                    offset_days INTEGER NOT NULL,
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    claimed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reminder_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    attempted_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    detail TEXT,
                    FOREIGN KEY(job_id) REFERENCES reminder_jobs(id)
                );
                """
            )
            self._ensure_events_notes_column(conn)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now_iso()),
            )

    def _ensure_events_notes_column(self, conn: sqlite3.Connection) -> None:
        cols = conn.execute("PRAGMA table_info(events)").fetchall()
        if any(col["name"] == "notes" for col in cols):
            return
        conn.execute("ALTER TABLE events ADD COLUMN notes TEXT NOT NULL DEFAULT ''")

    def import_recipients(self, recipients: Iterable[ReminderRecipient]) -> int:
        self.init_db()
        count = 0
        with self.connect() as conn:
            for r in recipients:
                conn.execute(
                    """
                    INSERT INTO recipients(
                        recipient_id, display_name, email, phone, preferred_channels,
                        sms_consent_status, consent_source, consent_timestamp, suppressed, match_keys
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(recipient_id) DO UPDATE SET
                        display_name=excluded.display_name,
                        email=excluded.email,
                        phone=excluded.phone,
                        preferred_channels=excluded.preferred_channels,
                        sms_consent_status=excluded.sms_consent_status,
                        consent_source=excluded.consent_source,
                        consent_timestamp=excluded.consent_timestamp,
                        suppressed=excluded.suppressed,
                        match_keys=excluded.match_keys
                    """,
                    (
                        r.recipient_id,
                        r.display_name,
                        r.email,
                        r.phone,
                        ",".join(r.preferred_channels),
                        r.sms_consent_status,
                        r.consent_source,
                        r.consent_timestamp,
                        int(r.suppressed),
                        ",".join(r.match_keys),
                    ),
                )
                count += 1
        return count

    def upsert_event(self, event: AppointmentEvent) -> None:
        self.init_db()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO events(
                    business_slug, source, calendar_id, event_id, event_uid, summary, notes,
                    start_iso, end_iso, timezone, attendee_emails, cancelled, recurring, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(business_slug, source, calendar_id, event_id, start_iso) DO UPDATE SET
                    event_uid=excluded.event_uid,
                    summary=excluded.summary,
                    notes=excluded.notes,
                    end_iso=excluded.end_iso,
                    timezone=excluded.timezone,
                    attendee_emails=excluded.attendee_emails,
                    cancelled=excluded.cancelled,
                    recurring=excluded.recurring,
                    updated_at=excluded.updated_at
                """,
                (
                    event.business_slug,
                    event.source,
                    event.calendar_id,
                    event.event_id,
                    event.event_uid,
                    event.summary,
                    event.notes,
                    event.start.isoformat(),
                    event.end.isoformat(),
                    event.timezone,
                    ",".join(event.attendee_emails),
                    int(event.cancelled),
                    int(event.recurring),
                    utc_now_iso(),
                ),
            )

    def upsert_job(
        self,
        *,
        event: AppointmentEvent,
        recipient: ReminderRecipient | None,
        channel: str,
        offset_days: int,
        due_at: str,
        status: str,
        reason: str | None = None,
    ) -> str:
        self.init_db()
        key = make_idempotency_key(
            business_slug=event.business_slug,
            source=event.source,
            calendar_id=event.calendar_id,
            event_uid_or_id=event.event_key,
            event_start=event.start.isoformat(),
            offset_days=offset_days,
            channel=channel,
        )
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO reminder_jobs(
                    idempotency_key, business_slug, source, calendar_id, event_id, event_uid,
                    event_start, event_end, event_timezone, recipient_id, channel, offset_days,
                    due_at, status, reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    event_end=excluded.event_end,
                    event_timezone=excluded.event_timezone,
                    recipient_id=excluded.recipient_id,
                    due_at=excluded.due_at,
                    status=CASE
                        WHEN reminder_jobs.status IN ('sent') THEN reminder_jobs.status
                        ELSE excluded.status
                    END,
                    reason=excluded.reason,
                    claimed_at=NULL,
                    updated_at=excluded.updated_at
                """,
                (
                    key,
                    event.business_slug,
                    event.source,
                    event.calendar_id,
                    event.event_id,
                    event.event_uid,
                    event.start.isoformat(),
                    event.end.isoformat(),
                    event.timezone,
                    recipient.recipient_id if recipient else None,
                    channel,
                    offset_days,
                    due_at,
                    status,
                    reason,
                    now,
                    now,
                ),
            )
        return key

    def cancel_jobs_for_event(self, event: AppointmentEvent, reason: str) -> int:
        self.init_db()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE reminder_jobs
                SET status='cancelled', reason=?, updated_at=?, claimed_at=NULL
                WHERE business_slug=? AND source=? AND calendar_id=? AND event_id=?
                  AND status='scheduled'
                """,
                (reason, utc_now_iso(), event.business_slug, event.source, event.calendar_id, event.event_id),
            )
            return cur.rowcount

    def rename_event(
        self,
        *,
        business_slug: str,
        source: str,
        calendar_id: str,
        event_id: str,
        summary: str,
    ) -> int:
        self.init_db()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE events
                SET summary=?, updated_at=?
                WHERE business_slug=? AND source=? AND calendar_id=? AND event_id=?
                """,
                (summary, utc_now_iso(), business_slug, source, calendar_id, event_id),
            )
            return cur.rowcount

    def cancel_event(
        self,
        *,
        business_slug: str,
        source: str,
        calendar_id: str,
        event_id: str,
        reason: str = "deleted",
    ) -> tuple[int, int]:
        self.init_db()
        with self.connect() as conn:
            event_cur = conn.execute(
                """
                UPDATE events
                SET cancelled=1, updated_at=?
                WHERE business_slug=? AND source=? AND calendar_id=? AND event_id=?
                """,
                (utc_now_iso(), business_slug, source, calendar_id, event_id),
            )
            jobs_cur = conn.execute(
                """
                UPDATE reminder_jobs
                SET status='cancelled', reason=?, claimed_at=NULL, updated_at=?
                WHERE business_slug=? AND source=? AND calendar_id=? AND event_id=?
                  AND status IN ('scheduled', 'claimed')
                """,
                (reason, utc_now_iso(), business_slug, source, calendar_id, event_id),
            )
            return event_cur.rowcount, jobs_cur.rowcount

    def claim_due(self, now_iso: str, *, limit: int = 100) -> list[ReminderJob]:
        self.init_db()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM reminder_jobs
                WHERE status='scheduled' AND claimed_at IS NULL AND due_at <= ?
                ORDER BY due_at ASC, id ASC
                LIMIT ?
                """,
                (now_iso, limit),
            ).fetchall()
            claimed = utc_now_iso()
            ids = [row["id"] for row in rows]
            if ids:
                conn.executemany(
                    "UPDATE reminder_jobs SET claimed_at=?, updated_at=? WHERE id=?",
                    [(claimed, claimed, job_id) for job_id in ids],
                )
            conn.commit()
        return [self._row_to_job(row) for row in rows]

    def mark_job(self, job_id: int, status: str, *, reason: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE reminder_jobs SET status=?, reason=?, claimed_at=NULL, updated_at=? WHERE id=?",
                (status, reason, utc_now_iso(), job_id),
            )

    def record_attempt(self, job_id: int, *, status: str, provider: str, detail: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO reminder_attempts(job_id, attempted_at, status, provider, detail) VALUES (?, ?, ?, ?, ?)",
                (job_id, utc_now_iso(), status, provider, detail),
            )

    def list_jobs(self, *, status: str | None = None) -> list[ReminderJob]:
        self.init_db()
        with self.connect() as conn:
            if status:
                rows = conn.execute("SELECT * FROM reminder_jobs WHERE status=? ORDER BY due_at, id", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM reminder_jobs ORDER BY due_at, id").fetchall()
        return [self._row_to_job(row) for row in rows]

    def list_events(
        self,
        *,
        limit: int = 25,
        start_iso: str | None = None,
        end_iso: str | None = None,
    ) -> list[dict]:
        self.init_db()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT business_slug, source, calendar_id, event_id, event_uid, summary,
                       notes, start_iso, end_iso, timezone, attendee_emails, cancelled, recurring
                FROM events
                WHERE cancelled = 0
                ORDER BY start_iso ASC
                """,
            ).fetchall()
        start_at = _parse_event_datetime(start_iso) if start_iso else None
        end_at = _parse_event_datetime(end_iso) if end_iso else None
        events = []
        for row in rows:
            event_start = _parse_event_datetime(row["start_iso"])
            if start_at and event_start < start_at:
                continue
            if end_at and event_start >= end_at:
                continue
            events.append({
                "business_slug": row["business_slug"],
                "source": row["source"],
                "calendar_id": row["calendar_id"],
                "event_id": row["event_id"],
                "event_uid": row["event_uid"],
                "summary": row["summary"],
                "notes": row["notes"],
                "start_iso": row["start_iso"],
                "end_iso": row["end_iso"],
                "timezone": row["timezone"],
                "attendee_emails": [v for v in row["attendee_emails"].split(",") if v],
                "recurring": bool(row["recurring"]),
            })
            if len(events) >= limit:
                break
        return events

    def get_recipient(self, recipient_id: str | None) -> ReminderRecipient | None:
        if not recipient_id:
            return None
        self.init_db()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM recipients WHERE recipient_id=?", (recipient_id,)).fetchone()
        if row is None:
            return None
        return ReminderRecipient(
            recipient_id=row["recipient_id"],
            display_name=row["display_name"],
            email=row["email"],
            phone=row["phone"],
            preferred_channels=tuple(row["preferred_channels"].split(",")) if row["preferred_channels"] else (),
            sms_consent_status=row["sms_consent_status"],
            consent_source=row["consent_source"],
            consent_timestamp=row["consent_timestamp"],
            suppressed=bool(row["suppressed"]),
            match_keys=tuple(row["match_keys"].split(",")) if row["match_keys"] else (),
        )

    def _row_to_job(self, row: sqlite3.Row) -> ReminderJob:
        return ReminderJob(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            business_slug=row["business_slug"],
            source=row["source"],
            calendar_id=row["calendar_id"],
            event_id=row["event_id"],
            event_uid=row["event_uid"],
            event_start=row["event_start"],
            event_end=row["event_end"],
            event_timezone=row["event_timezone"],
            recipient_id=row["recipient_id"],
            channel=row["channel"],
            offset_days=row["offset_days"],
            due_at=row["due_at"],
            status=row["status"],
            reason=row["reason"],
            claimed_at=row["claimed_at"],
        )
