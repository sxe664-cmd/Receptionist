# Appointment Reminders

AIReceptionist can send immediate booking confirmations and schedule
appointment reminders on top of the existing calendar and messaging
architecture. The reminder subsystem is intentionally local-first: you can test
scheduling and delivery with fake log providers before configuring Twilio SMS or
a real email sender.

## Model

- **Calendars define appointments.** Google Calendar events and Apple Calendar
  `.ics` imports provide the appointment time.
- **Structured contacts define recipients.** `contacts.yaml` supplies email,
  phone, channel preferences, and SMS consent. The system does not guess phone
  numbers from calendar titles.
- **Bookings send confirmations.** Successful AI-booked appointments create
  immediate confirmation email/SMS jobs using the same recipient and consent
  rules as reminders.
- **SQLite tracks reminder state.** Jobs are idempotent by business, source,
  calendar, event, start time, offset, and channel.

## Configuration

```yaml
mode: "demo"  # "production" rejects fake email/SMS reminder providers

communications:
  default_transfer_number: "+15551234567"
  email_from: "Acme Dental <receptionist@acmedental.com>"
  sms_from_number: "+15557654321"

reminders:
  enabled: true
  offset_days: [4, 1]
  channels: ["email", "sms"]
  store_path: "./messages/reminders.sqlite3"
  contacts_path: "./config/businesses/example-contacts.yaml"
  lookahead_days: 60
  allow_retroactive_send: false
  email_provider: "fake"      # "configured" uses the top-level email sender
  fake_email_log_path: "./messages/reminders-email.log"
  calendar_sources:
    - type: "google"
      calendar_id: "primary"
    - type: "apple_ics"
      path: "./imports/apple-calendar.ics"

sms:
  provider:
    type: "fake"
    log_path: "./messages/reminders-sms.log"
```

For production Twilio SMS:

```yaml
mode: "production"
communications:
  email_from: "Acme Dental <receptionist@acmedental.com>"
  sms_from_number: "+15557654321"
reminders:
  email_provider: "configured"
email:
  sender:
    type: "smtp"
    smtp:
      host: "smtp.gmail.com"
      port: 587
      username: ${SMTP_USERNAME}
      password: ${SMTP_PASSWORD}
      use_tls: true
sms:
  provider:
    type: "twilio"
    account_sid_env: "TWILIO_ACCOUNT_SID"
    auth_token_env: "TWILIO_AUTH_TOKEN"
    # Either use communications.sms_from_number as the Twilio From number,
    # set from_number here, or set messaging_service_sid instead.
```

Use either `messaging_service_sid` or `from_number`, not both.

## Local workflow

```bash
python -m receptionist.reminders init-db --business example-dental
python -m receptionist.reminders contacts import --business example-dental
python -m receptionist.reminders sync --business example-dental --fixture tests/fixtures/calendar/google.json
python -m receptionist.reminders run-due --business example-dental --now 2026-06-01T09:00:00-04:00
python -m receptionist.reminders list --business example-dental
```

Fake email and SMS sends append JSON lines to the configured log files.
If `reminders.calendar_sources` is configured, `sync` reads from those sources
directly; `--fixture` and `--ics` are just local override paths for tests.
When the AI books an appointment, confirmation delivery happens from the agent
process immediately after Google Calendar returns a successful event.


## Message templates

Confirmation and reminder copy can be changed in the Electron console or in YAML:

```yaml
message_templates:
  confirmation_email_subject: "Appointment confirmed: {appointment_time}"
  confirmation_email_text: |
    Hi {recipient_name},

    Your appointment with {business_name} is confirmed for {appointment_time}.

    If you need to make changes, please call us at {default_transfer_number}.
  confirmation_sms: "{business_name}: your appointment is confirmed for {appointment_time}. Reply STOP to opt out. Reply HELP for help."
  reminder_email_subject: "Appointment reminder: {appointment_time}"
  reminder_email_text: |
    Hi {recipient_name},

    This is a reminder from {business_name} about your appointment on {appointment_time}.

    If you need to make changes, please call us at {default_transfer_number}.
  reminder_sms: "{business_name}: reminder for your appointment on {appointment_time}. Reply STOP to opt out. Reply HELP for help."
```

Supported placeholders: `{business_name}`, `{recipient_name}`, `{appointment_time}`, `{offset_days}`, and `{default_transfer_number}`. Unknown placeholders are rejected when the config loads so production does not fail later during delivery.


## Google Calendar demo bookings

To test the real booking path against Google Calendar while keeping delivery
mocked:

1. Enable Google Calendar API in Google Cloud.
2. Create an OAuth 2.0 Client ID with application type **Desktop app**.
3. Save it as `secrets/<business-slug>/google-calendar-client.json`.
4. Run `python -m receptionist.booking setup <business-slug>` or click
   **Run Google setup** in the Electron console.
5. Enable `calendar` and `reminders` in the business YAML, with fake delivery
   providers in demo mode.

During demo mode, a successful AI booking that includes caller name and phone
number auto-upserts a local contact in `reminders.contacts_path`, marks the
SMS consent as `opted_in` with `consent_source: demo_ai_booking`, schedules the
future reminder jobs, and immediately writes the confirmation SMS/email to fake
logs. Production mode still requires real consent data; auto-created contacts
use `sms_consent_status: unknown` there.

## Production notes

- Run `sync` and `run-due` periodically with cron, systemd timers, Windows Task
  Scheduler, or another process supervisor.
- SMS reminders require opt-in. Contacts with `unknown`, `opted_out`, or
  `suppressed: true` are skipped/suppressed.
- US Twilio long-code application traffic generally requires A2P 10DLC
  registration and compliant opt-in/STOP/HELP handling before real sends.
- Apple Calendar first support is `.ics` import. Direct iCloud
  CalDAV/CardDAV is a future hardening path.
