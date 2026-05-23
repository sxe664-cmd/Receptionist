function supportsAppointmentMutation(appointment) {
  return String(appointment?.source || '').trim().toLowerCase() === 'google'
    && String(appointment?.event_id || '').trim() !== '';
}

function buildAppointmentMutationRequest(appointment, configPath) {
  return {
    configPath,
    eventId: String(appointment?.event_id || ''),
    calendarId: String(appointment?.calendar_id || 'primary'),
    summary: String(appointment?.summary || 'Appointment'),
    source: String(appointment?.source || ''),
  };
}

const api = {
  supportsAppointmentMutation,
  buildAppointmentMutationRequest,
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = api;
}

if (typeof window !== 'undefined') {
  window.AppointmentMenu = api;
}
