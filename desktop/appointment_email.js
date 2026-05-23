function firstAttendeeEmail(appointment) {
  const attendees = Array.isArray(appointment?.attendee_emails) ? appointment.attendee_emails : [];
  return String(attendees[0] || '').trim();
}

function buildAppointmentEmailRequest(appointment, configPath) {
  return {
    configPath,
    eventId: String(appointment?.event_id || ''),
    eventUid: String(appointment?.event_uid || appointment?.event_id || ''),
    calendarId: String(appointment?.calendar_id || 'primary'),
    summary: String(appointment?.summary || 'Appointment'),
    startIso: String(appointment?.start_iso || ''),
    endIso: String(appointment?.end_iso || ''),
    timezone: String(appointment?.timezone || ''),
    attendeeEmail: firstAttendeeEmail(appointment),
  };
}

const api = {
  firstAttendeeEmail,
  buildAppointmentEmailRequest,
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = api;
}

if (typeof window !== 'undefined') {
  window.AppointmentEmail = api;
}
