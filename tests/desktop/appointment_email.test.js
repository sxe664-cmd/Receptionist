const test = require('node:test');
const assert = require('node:assert/strict');

const {
  firstAttendeeEmail,
  buildAppointmentEmailRequest,
} = require('../../desktop/appointment_email');

test('firstAttendeeEmail picks the first attendee email', () => {
  assert.equal(
    firstAttendeeEmail({ attendee_emails: ['pat@example.com', 'alt@example.com'] }),
    'pat@example.com',
  );
});

test('buildAppointmentEmailRequest carries the appointment metadata', () => {
  const payload = buildAppointmentEmailRequest(
    {
      event_id: 'evt-1',
      event_uid: 'uid-1',
      calendar_id: 'primary',
      summary: 'Cleaning',
      start_iso: '2026-05-23T10:00:00-04:00',
      end_iso: '2026-05-23T10:30:00-04:00',
      timezone: 'America/New_York',
      attendee_emails: ['pat@example.com'],
    },
    'config/businesses/santiago.yaml',
  );

  assert.deepEqual(payload, {
    configPath: 'config/businesses/santiago.yaml',
    eventId: 'evt-1',
    eventUid: 'uid-1',
    calendarId: 'primary',
    summary: 'Cleaning',
    startIso: '2026-05-23T10:00:00-04:00',
    endIso: '2026-05-23T10:30:00-04:00',
    timezone: 'America/New_York',
    attendeeEmail: 'pat@example.com',
  });
});

test('buildAppointmentEmailRequest leaves attendeeEmail blank when absent', () => {
  const payload = buildAppointmentEmailRequest(
    {
      event_id: 'evt-1',
      summary: 'Cleaning',
      start_iso: '2026-05-23T10:00:00-04:00',
      end_iso: '2026-05-23T10:30:00-04:00',
      timezone: 'America/New_York',
      attendee_emails: [],
    },
    'config/businesses/santiago.yaml',
  );

  assert.equal(payload.attendeeEmail, '');
});
