const state = {
  businesses: [],
  selectedConfig: null,
  selectedBusiness: null,
  emailSetup: null,
  selectionMode: false,
  selectedAppointmentKeys: new Set(),
  appointmentsCache: [],
  appointmentsExpanded: false,
  selectedDate: new Date(),
  calendarView: 'day',
  visibleRange: null,
};

const $ = (id) => document.getElementById(id);
const logEl = $('log');
const toast = $('toast');
const appointmentsList = $('appointmentsList');
const calendarDays = $('calendarDays');
const calendarMonthLabel = $('calendarMonthLabel');
const calendarRangeLabel = $('calendarRangeLabel');
const updateBanner = $('updateBanner');
const updateBannerTitle = $('updateBannerTitle');
const updateBannerDetail = $('updateBannerDetail');
const updateRestartBtn = $('updateRestartBtn');
const appointmentEmailApi = globalThis.AppointmentEmail || {};
const views = {
  operate: {
    button: $('operateTabBtn'),
    panel: $('operateView'),
  },
  settings: {
    button: $('settingsTabBtn'),
    panel: $('settingsView'),
  },
};

const windowMaximizeButton = $('windowMaximizeBtn');
const APPOINTMENT_LOAD_LIMIT = 500;

function cloneDate(value) {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

function addDays(value, days) {
  const next = cloneDate(value);
  next.setDate(next.getDate() + days);
  return next;
}

function addMonths(value, months) {
  const next = cloneDate(value);
  next.setMonth(next.getMonth() + months);
  return next;
}

function startOfWeek(value) {
  return addDays(value, -value.getDay());
}

function startOfMonth(value) {
  return new Date(value.getFullYear(), value.getMonth(), 1);
}

function endOfVisibleMonth(value) {
  return new Date(value.getFullYear(), value.getMonth() + 1, 1);
}

function isSameDay(a, b) {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

function dateKey(value) {
  return [
    value.getFullYear(),
    String(value.getMonth() + 1).padStart(2, '0'),
    String(value.getDate()).padStart(2, '0'),
  ].join('-');
}

function appointmentDate(appointment) {
  const parsed = new Date(appointment?.start_iso || '');
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function getVisibleRange() {
  const selected = cloneDate(state.selectedDate);
  if (state.calendarView === 'week') {
    const start = startOfWeek(selected);
    return { start, end: addDays(start, 7) };
  }
  if (state.calendarView === 'month') {
    const start = startOfMonth(selected);
    return { start, end: endOfVisibleMonth(selected) };
  }
  return { start: selected, end: addDays(selected, 1) };
}

function getAppointmentLoadRange() {
  const visible = getVisibleRange();
  const month = {
    start: startOfMonth(state.selectedDate),
    end: endOfVisibleMonth(state.selectedDate),
  };
  return {
    start: visible.start < month.start ? visible.start : month.start,
    end: visible.end > month.end ? visible.end : month.end,
  };
}

function appointmentMatchesRange(appointment, range) {
  const start = appointmentDate(appointment);
  return Boolean(start && start >= range.start && start < range.end);
}

function filteredAppointments() {
  const range = getVisibleRange();
  state.visibleRange = range;
  return state.appointmentsCache.filter((appointment) => appointmentMatchesRange(appointment, range));
}

function appointmentKeyFromParts(calendarId, eventId) {
  return `${String(calendarId || 'primary')}::${String(eventId || '')}`;
}

function appointmentKeyFromRecord(appointment) {
  return appointmentKeyFromParts(appointment?.calendar_id || 'primary', appointment?.event_id || '');
}

function selectedAppointmentCount() {
  return state.selectedAppointmentKeys.size;
}

function isSelectionMode() {
  return Boolean(state.selectionMode);
}

function resetSelectionState() {
  state.selectionMode = false;
  state.selectedAppointmentKeys.clear();
}

function buttonLabelForSelectionMode() {
  if (!isSelectionMode()) return 'Delete';
  const count = selectedAppointmentCount();
  return count > 0 ? `Delete Selected (${count})` : 'Cancel Delete';
}

function syncDeleteControl() {
  const button = $('refreshAppointmentsBtn');
  if (!button) return;
  button.textContent = buttonLabelForSelectionMode();
  const disabled = false;
  button.disabled = disabled;
  button.setAttribute('aria-disabled', disabled ? 'true' : 'false');
}

function setSelectedDate(value) {
  state.selectedDate = cloneDate(value);
  state.appointmentsExpanded = false;
  resetSelectionState();
  syncDeleteControl();
  loadAppointments().catch((error) => showToast(error.message, true));
}

function shiftCalendarPeriod(direction) {
  const amount = direction < 0 ? -1 : 1;
  if (state.calendarView === 'month') {
    setSelectedDate(addMonths(state.selectedDate, amount));
    return;
  }
  if (state.calendarView === 'week') {
    setSelectedDate(addDays(state.selectedDate, amount * 7));
    return;
  }
  setSelectedDate(addDays(state.selectedDate, amount));
}

function showToast(message, isError = false) {
  toast.textContent = message;
  toast.className = `toast${isError ? ' error' : ''}`;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 6000);
}

function renderUpdaterStatus(payload = {}) {
  if (!updateBanner || !updateBannerTitle || !updateBannerDetail || !updateRestartBtn) return;
  const stateName = String(payload.state || '');
  if (!stateName || stateName === 'idle' || stateName === 'checking' || stateName === 'available') {
    updateBanner.hidden = true;
    updateRestartBtn.hidden = true;
    return;
  }

  updateBanner.hidden = false;
  updateRestartBtn.hidden = true;
  if (stateName === 'downloading') {
    updateBannerTitle.textContent = 'Downloading update';
    const percent = Number(payload.percent);
    updateBannerDetail.textContent = Number.isFinite(percent)
      ? `Download progress: ${Math.max(0, Math.min(100, Math.round(percent)))}%`
      : 'Downloading in the background.';
    return;
  }
  if (stateName === 'downloaded') {
    updateBannerTitle.textContent = 'Update ready';
    updateBannerDetail.textContent = payload.version
      ? `Version ${payload.version} is ready to install.`
      : 'The latest version is ready to install.';
    updateRestartBtn.hidden = false;
    return;
  }
  if (stateName === 'error') {
    updateBannerTitle.textContent = 'Update check failed';
    updateBannerDetail.textContent = payload.message || 'The app will retry on next launch.';
  }
}

function appendLog({ source = 'console', line = '', at = new Date().toISOString() }) {
  if (!logEl) {
    console.info(`[${new Date(at).toLocaleTimeString()}] ${source}: ${line}`);
    return;
  }
  const time = new Date(at).toLocaleTimeString();
  logEl.textContent += `[${time}] ${source}: ${line}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

function setView(viewName) {
  const next = views[viewName] ? viewName : 'operate';
  document.body.classList.toggle('settings-mode', next === 'settings');
  Object.entries(views).forEach(([name, view]) => {
    const active = name === next;
    view.button.classList.toggle('active', active);
    view.button.setAttribute('aria-current', active ? 'page' : 'false');
    view.panel.classList.toggle('active', active);
  });
}

function businessSlugFromPath(configPath) {
  return (configPath || '').split(/[\\/]/).pop().replace(/\.ya?ml$/i, '');
}

function renderBusiness(data) {
  state.selectedBusiness = data;
  const cfg = data.config || {};
  const comms = cfg.communications || {};
  const templates = cfg.message_templates || {};

  $('defaultTransferNumber').value = comms.default_transfer_number || '';
  $('emailFrom').value = comms.email_from || '';
  $('smsFromNumber').value = comms.sms_from_number || '';
  $('confirmationEmailSubject').value = templates.confirmation_email_subject || '';
  $('confirmationEmailText').value = templates.confirmation_email_text || '';
  $('confirmationSms').value = templates.confirmation_sms || '';
  $('reminderEmailSubject').value = templates.reminder_email_subject || '';
  $('reminderEmailText').value = templates.reminder_email_text || '';
  $('reminderSms').value = templates.reminder_sms || '';
  $('quickSmsTemplate').value = templates.quick_sms || '';
  $('quickEmailTemplate').value = templates.quick_email || '';
  $('quickCallScript').value = templates.quick_call_script || '';
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatRangeLabel(range) {
  const sameYear = range.start.getFullYear() === addDays(range.end, -1).getFullYear();
  const endInclusive = addDays(range.end, -1);
  if (state.calendarView === 'day') {
    return range.start.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  }
  if (state.calendarView === 'month') {
    return range.start.toLocaleDateString([], { month: 'long', year: 'numeric' });
  }
  return `${range.start.toLocaleDateString([], { month: 'short', day: 'numeric' })} - ${endInclusive.toLocaleDateString([], {
    month: sameYear ? 'short' : 'short',
    day: 'numeric',
    year: sameYear ? undefined : 'numeric',
  })}`;
}

function renderCalendarFilter() {
  if (!calendarDays || !calendarMonthLabel || !calendarRangeLabel) return;
  const selected = cloneDate(state.selectedDate);
  const monthStart = startOfMonth(selected);
  const monthEnd = endOfVisibleMonth(selected);
  const range = getVisibleRange();
  const appointmentDays = new Set(
    state.appointmentsCache
      .map(appointmentDate)
      .filter(Boolean)
      .map(dateKey),
  );

  document.querySelectorAll('[data-calendar-view]').forEach((button) => {
    const active = button.dataset.calendarView === state.calendarView;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });

  calendarMonthLabel.textContent = selected.toLocaleDateString([], { month: 'long', year: 'numeric' });
  calendarRangeLabel.textContent = formatRangeLabel(range);

  const cells = [];
  for (let index = 0; index < monthStart.getDay(); index += 1) {
    cells.push('<span class="calendar-day-placeholder"></span>');
  }
  for (let day = new Date(monthStart); day < monthEnd; day = addDays(day, 1)) {
    const inRange = day >= range.start && day < range.end;
    const selectedDay = isSameDay(day, selected);
    const key = dateKey(day);
    cells.push(`
      <button
        class="calendar-day${selectedDay ? ' is-selected' : ''}${inRange && !selectedDay ? ' is-in-range' : ''}${appointmentDays.has(key) ? ' has-appointments' : ''}"
        type="button"
        data-calendar-date="${key}"
        aria-label="${escapeHtml(day.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }))}"
        aria-current="${selectedDay ? 'date' : 'false'}"
      >
        <span>${day.getDate()}</span>
      </button>
    `);
  }
  calendarDays.innerHTML = cells.join('');
}

async function loadBusinesses(selectPath = null) {
  const result = await window.receptionist.listBusinesses();
  state.businesses = result.businesses || [];
  state.selectedConfig = selectPath || state.businesses[0]?.path || null;
  await loadSelectedBusiness();
}

async function loadSelectedBusiness() {
  if (!state.selectedConfig) return;
  try {
    state.appointmentsExpanded = false;
    const data = await window.receptionist.getBusiness(state.selectedConfig);
    renderBusiness(data);
    await loadEmailSetup();
    resetSelectionState();
    syncDeleteControl();
    await loadAppointments();
  } catch (error) {
    showToast(error.message, true);
    if (appointmentsList) appointmentsList.textContent = 'Appointments unavailable until the config validates.';
  }
}

async function loadEmailSetup() {
  if (!state.selectedConfig) return;
  const status = $('emailSetupStatus');
  try {
    const setup = await window.receptionist.getEmailSetup(state.selectedConfig);
    state.emailSetup = setup;
    $('emailSetupFrom').value = setup.from || $('emailFrom').value || '';
    const senderType = setup.sender_type || 'smtp';
    const isSmtp = senderType === 'smtp';
    const isGmailOauth = senderType === 'gmail_oauth';
    $('smtpUsername').value = isSmtp ? (setup.smtp_username || '') : '';
    $('smtpPassword').value = '';
    $('emailSetupFrom').disabled = isGmailOauth;
    $('smtpUsername').disabled = !isSmtp;
    $('smtpPassword').disabled = !isSmtp;
    $('saveEmailSetupBtn').disabled = !isSmtp;
    if (status) {
      if (isGmailOauth) {
        status.textContent = setup.gmail_oauth_token_set ? 'Google OAuth connected' : 'Google OAuth needs setup';
        status.className = `setup-status${setup.gmail_oauth_token_set ? ' ready' : ''}`;
      } else if (isSmtp) {
        status.textContent = setup.smtp_password_set ? 'Gmail SMTP saved' : 'Needs app password';
        status.className = `setup-status${setup.smtp_password_set ? ' ready' : ''}`;
      } else {
        status.textContent = 'Email sender configured';
        status.className = 'setup-status ready';
      }
    }
  } catch (error) {
    state.emailSetup = null;
    if (status) {
      status.textContent = 'Email setup unavailable';
      status.className = 'setup-status error';
    }
  }
}

async function saveEmailSetup() {
  if (!state.selectedConfig) return;
  if (state.emailSetup?.sender_type && state.emailSetup.sender_type !== 'smtp') {
    showToast('This business uses Google OAuth for email. Reconnect Google instead of saving SMTP settings.', true);
    return;
  }
  const status = $('emailSetupStatus');
  try {
    const fromAddress = $('emailSetupFrom').value.trim();
    const smtpUsername = $('smtpUsername').value.trim();
    const smtpPassword = $('smtpPassword').value.trim();
    const data = await window.receptionist.updateEmailSetup({
      configPath: state.selectedConfig,
      fromAddress,
      smtpUsername,
      smtpPassword,
    });
    renderBusiness(data);
    $('emailFrom').value = fromAddress;
    $('smtpPassword').value = '';
    if (status) {
      status.textContent = 'Gmail SMTP saved';
      status.className = 'setup-status ready';
    }
    showToast('Email sender saved.');
  } catch (error) {
    if (status) {
      status.textContent = 'Email setup needs attention';
      status.className = 'setup-status error';
    }
    showToast(error.message, true);
  }
}

async function saveSettings(event) {
  event.preventDefault();
  if (!state.selectedConfig) return;
  try {
    const data = await window.receptionist.updateBusiness({
      configPath: state.selectedConfig,
      mode: document.querySelector('input[name="mode"]:checked')?.value || 'demo',
      defaultTransferNumber: $('defaultTransferNumber').value.trim(),
      emailFrom: $('emailFrom').value.trim(),
      smsFromNumber: $('smsFromNumber').value.trim(),
      confirmationEmailSubject: $('confirmationEmailSubject').value.trim(),
      confirmationEmailText: $('confirmationEmailText').value.trim(),
      confirmationSms: $('confirmationSms').value.trim(),
      reminderEmailSubject: $('reminderEmailSubject').value.trim(),
      reminderEmailText: $('reminderEmailText').value.trim(),
      reminderSms: $('reminderSms').value.trim(),
      quickSms: $('quickSmsTemplate').value.trim(),
      quickEmail: $('quickEmailTemplate').value.trim(),
      quickCallScript: $('quickCallScript').value.trim(),
    });
    renderBusiness(data);
    showToast('Saved and validated business config.');
  } catch (error) {
    showToast(error.message, true);
    await loadSelectedBusiness();
  }
}

async function loadAppointments() {
  if (!appointmentsList || !state.selectedConfig) return;
  appointmentsList.textContent = 'Loading appointments...';
  try {
    const range = getAppointmentLoadRange();
    const result = await window.receptionist.listAppointments(state.selectedConfig, {
      startIso: range.start.toISOString(),
      endIso: range.end.toISOString(),
      limit: APPOINTMENT_LOAD_LIMIT,
    });
    state.appointmentsExpanded = false;
    state.appointmentsCache = result.appointments || [];
    renderFilteredAppointments();
  } catch (error) {
    state.appointmentsCache = [];
    resetSelectionState();
    syncDeleteControl();
    renderCalendarFilter();
    appointmentsList.textContent = 'Sync calendar to import appointments.';
  }
}

function renderFilteredAppointments() {
  renderCalendarFilter();
  renderAppointments(filteredAppointments(), { updateCache: false });
}

function renderAppointments(appointments, options = {}) {
  if (!appointmentsList) return;
  if (options.updateCache) {
    state.appointmentsCache = appointments;
  }
  const allAppointmentKeys = new Set(state.appointmentsCache.map((appointment) => appointmentKeyFromRecord(appointment)));
  state.selectedAppointmentKeys.forEach((key) => {
    if (!allAppointmentKeys.has(key)) state.selectedAppointmentKeys.delete(key);
  });
  if (isSelectionMode() && !appointments.length) {
    resetSelectionState();
  }
  syncDeleteControl();
  if (!appointments.length) {
    appointmentsList.textContent = state.appointmentsCache.length
      ? 'No appointments in this date range.'
      : 'Sync calendar to import appointments.';
    return;
  }
  const visibleAppointments = state.appointmentsExpanded ? appointments : appointments.slice(0, 5);
  const hiddenCount = appointments.length - visibleAppointments.length;
  appointmentsList.innerHTML = visibleAppointments.map((appointment) => {
    const start = formatAppointmentTime(appointment.start_iso);
    const summaryText = appointment.summary || 'Appointment';
    const attendeeEmail = (appointment.attendee_emails || [])[0] || '';
    const attendee = attendeeEmail || 'No email on event';
    const notes = formatAppointmentNotes(appointment.notes);
    const hasAttendeeEmail = Boolean(String(attendeeEmail).trim());
    const appointmentKey = appointmentKeyFromRecord(appointment);
    const selected = state.selectedAppointmentKeys.has(appointmentKey);
    const selectorLabel = selected ? 'Deselect appointment' : 'Select appointment';

    return `
      <article
        class="appointment-card${selected ? ' appointment-card--selected' : ''}"
        data-source="${escapeHtml(appointment.source || '')}"
        data-event-id="${escapeHtml(appointment.event_id || '')}"
        data-calendar-id="${escapeHtml(appointment.calendar_id || 'primary')}"
        data-summary="${escapeHtml(summaryText)}"
      >
        <div class="appointment-card__copy">
          <strong>${escapeHtml(summaryText)}</strong>
          <small>${escapeHtml(start)} · ${escapeHtml(attendee)}</small>
          <small class="appointment-note">${escapeHtml(notes)}</small>
        </div>
        <div class="appointment-card__controls">
          <div class="appointment-actions" aria-label="Appointment contact actions">
            <button type="button" data-appointment-action="sms" data-event-id="${escapeHtml(appointment.event_id)}" aria-label="Send SMS" title="Send SMS">
              <svg class="appointment-action__icon" aria-hidden="true" viewBox="0 0 24 24">
                <path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.6 8.6 0 0 1-7.7 4.7 8.5 8.5 0 0 1-4-.9L3 21l1.8-5.1a8.5 8.5 0 0 1-.8-3.6 8.6 8.6 0 1 1 17 .2Z" />
              </svg>
            </button>            ${hasAttendeeEmail ? `
              <button
                type="button"
                data-appointment-action="email"
                data-event-id="${escapeHtml(appointment.event_id)}"
                data-event-uid="${escapeHtml(appointment.event_uid || appointment.event_id)}"
                data-calendar-id="${escapeHtml(appointment.calendar_id)}"
                data-summary="${escapeHtml(summaryText)}"
                data-start-iso="${escapeHtml(appointment.start_iso)}"
                data-end-iso="${escapeHtml(appointment.end_iso)}"
                data-timezone="${escapeHtml(appointment.timezone)}"
                data-attendee-email="${escapeHtml(attendeeEmail)}"
                aria-label="Send email"
                title="Send email"
              >
                <svg class="appointment-action__icon" aria-hidden="true" viewBox="0 0 24 24">
                  <rect x="3" y="5" width="18" height="14" rx="2" />
                  <path d="m4 7 8 6 8-6" />
                </svg>
              </button>
            ` : ''}
            <button type="button" data-appointment-action="call" data-event-id="${escapeHtml(appointment.event_id)}" aria-label="Call" title="Call">
              <svg class="appointment-action__icon" aria-hidden="true" viewBox="0 0 24 24">
                <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.7 19.7 0 0 1-8.6-3.1 19.4 19.4 0 0 1-6-6A19.7 19.7 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.2a2 2 0 0 1 2.1-.5c.9.3 1.8.6 2.8.7A2 2 0 0 1 22 16.9Z" />
              </svg>
            </button>
          </div>
          ${isSelectionMode() ? `
            <button
              type="button"
              class="appointment-select-toggle${selected ? ' is-selected' : ''}"
              data-appointment-action="toggle-select"
              data-appointment-key="${escapeHtml(appointmentKey)}"
              role="checkbox"
              aria-checked="${selected ? 'true' : 'false'}"
              aria-label="${selectorLabel}"
              title="${selectorLabel}"
            >
              <span class="appointment-select-toggle__check" aria-hidden="true">?</span>
            </button>
          ` : ''}
        </div>
      </article>
    `;
  }).join('');
  if (appointments.length > 5) {
    appointmentsList.insertAdjacentHTML('beforeend', `
      <button type="button" class="appointments-show-more" data-appointments-show-more>
        ${state.appointmentsExpanded ? 'Show Less' : 'Show More'}
      </button>
    `);
  }
}

function formatAppointmentNotes(value) {
  const compact = String(value || '').replace(/\s+/g, ' ').trim();
  if (!compact) return 'No notes';
  const maxLen = 140;
  if (compact.length <= maxLen) return compact;
  return `${compact.slice(0, maxLen - 1)}…`;
}

async function deleteSelectedAppointments(button) {
  if (!state.selectedConfig || selectedAppointmentCount() === 0) return;
  const count = selectedAppointmentCount();
  const confirmed = window.confirm(`Delete ${count} selected appointment${count === 1 ? '' : 's'}?`);
  if (!confirmed) return;
  button.disabled = true;
  try {
    const selectedKeys = new Set(state.selectedAppointmentKeys);
    const selectedAppointments = state.appointmentsCache.filter((appointment) => {
      const key = appointmentKeyFromRecord(appointment);
      return selectedKeys.has(key);
    });
    const results = await Promise.allSettled(selectedAppointments.map((appointment) => {
      return window.receptionist.deleteAppointment({
        configPath: state.selectedConfig,
        eventId: String(appointment.event_id || ''),
        calendarId: String(appointment.calendar_id || 'primary'),
      });
    }));
    const failedCount = results.filter((result) => result.status === 'rejected').length;
    const succeededCount = results.length - failedCount;
    if (failedCount === 0) {
      showToast(`Deleted ${succeededCount} appointment${succeededCount === 1 ? '' : 's'}.`);
    } else {
      showToast(`Deleted ${succeededCount}. Failed to delete ${failedCount}.`, true);
    }
    resetSelectionState();
    await loadAppointments();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    syncDeleteControl();
  }
}

function formatAppointmentTime(value) {
  try {
    return new Date(value).toLocaleString([], {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  } catch (_error) {
    return value || 'Unknown time';
  }
}

async function runReminderCommand(commandText) {
  if (!state.selectedConfig) return;
  const [command, subcommand] = commandText.split(' ');
  const businessSlug = businessSlugFromPath(state.selectedConfig);
  appendLog({ source: 'console', line: `Running reminders ${commandText} for ${businessSlug}` });
  const result = await window.receptionist.runReminderCommand({
    command: subcommand ? `${command} ${subcommand}` : command,
    businessSlug,
  });
  if (result.stdout) appendLog({ source: 'reminders:out', line: result.stdout.trim() });
  if (result.stderr) appendLog({ source: 'reminders:err', line: result.stderr.trim() });
  showToast(result.ok ? `Reminder command finished: ${commandText}` : `Reminder command failed: ${commandText}`, !result.ok);
  if (result.ok && command === 'sync') await loadAppointments();
}

window.receptionist.onProcessLog(appendLog);
window.receptionist.onUpdateStatus(renderUpdaterStatus);
function addClick(id, handler) {
  const el = $(id);
  if (el) el.addEventListener('click', handler);
}

addClick('refreshBtn', () => loadBusinesses(state.selectedConfig).catch((e) => showToast(e.message, true)));
$('windowMinimizeBtn')?.addEventListener('click', () => window.windowControls?.minimize?.());
$('windowMaximizeBtn')?.addEventListener('click', async () => {
  const result = await window.windowControls?.toggleMaximize?.();
  if (windowMaximizeButton) {
    windowMaximizeButton.classList.toggle('is-maximized', Boolean(result?.maximized));
    windowMaximizeButton.setAttribute('aria-label', result?.maximized ? 'Restore window' : 'Maximize window');
    const maximizeIcon = windowMaximizeButton.querySelector('.window-control__icon--maximize');
    const restoreIcon = windowMaximizeButton.querySelector('.window-control__icon--restore');
    if (maximizeIcon) maximizeIcon.hidden = Boolean(result?.maximized);
    if (restoreIcon) restoreIcon.hidden = !result?.maximized;
  }
});
$('windowCloseBtn')?.addEventListener('click', () => window.windowControls?.close?.());
$('settingsForm').addEventListener('submit', saveSettings);
addClick('saveBtnTop', () => $('settingsForm').requestSubmit());
addClick('calendarPrevBtn', () => shiftCalendarPeriod(-1));
addClick('calendarNextBtn', () => shiftCalendarPeriod(1));
addClick('calendarTodayBtn', () => setSelectedDate(new Date()));
document.querySelectorAll('[data-calendar-view]').forEach((button) => {
  button.addEventListener('click', () => {
    state.calendarView = button.dataset.calendarView || 'day';
    state.appointmentsExpanded = false;
    resetSelectionState();
    syncDeleteControl();
    loadAppointments().catch((error) => showToast(error.message, true));
  });
});
calendarDays?.addEventListener('click', (event) => {
  const button = event.target.closest('[data-calendar-date]');
  if (!button) return;
  const [year, month, day] = String(button.dataset.calendarDate || '').split('-').map(Number);
  if (!year || !month || !day) return;
  setSelectedDate(new Date(year, month - 1, day));
});
addClick('refreshAppointmentsBtn', async () => {
  const button = $('refreshAppointmentsBtn');
  if (!button) return;
  if (!isSelectionMode()) {
    state.selectionMode = true;
    state.selectedAppointmentKeys.clear();
    renderFilteredAppointments();
    showToast('Select appointments to delete.');
    return;
  }
  if (selectedAppointmentCount() === 0) {
    resetSelectionState();
    renderFilteredAppointments();
    showToast('Exited delete mode.');
    return;
  }
  await deleteSelectedAppointments(button);
});
addClick('saveEmailSetupBtn', saveEmailSetup);
addClick('updateRestartBtn', async () => {
  const result = await window.receptionist.installUpdate();
  if (!result?.ok) showToast(result?.message || 'No update is ready to install yet.', true);
});
Object.entries(views).forEach(([name, view]) => {
  view.button.addEventListener('click', () => setView(name));
});
addClick('backToDashboardBtn', () => setView('operate'));
addClick('setupGoogleBtn', async () => {
  if (!state.selectedConfig) return;
  const businessSlug = businessSlugFromPath(state.selectedConfig);
  appendLog({ source: 'console', line: `Running Google Calendar and Gmail setup for ${businessSlug}` });
  const result = await window.receptionist.setupBooking(businessSlug);
  if (result.stdout) appendLog({ source: 'booking:out', line: result.stdout.trim() });
  if (result.stderr) appendLog({ source: 'booking:err', line: result.stderr.trim() });
  showToast(result.ok ? 'Google setup finished.' : 'Google setup needs attention. See console log.', !result.ok);
});
appointmentsList?.addEventListener('click', (event) => {
  const showMoreButton = event.target.closest('[data-appointments-show-more]');
  if (showMoreButton) {
    event.preventDefault();
    state.appointmentsExpanded = !state.appointmentsExpanded;
    renderFilteredAppointments();
    return;
  }
  const button = event.target.closest('[data-appointment-action]');
  if (!button) return;
  const action = button.dataset.appointmentAction;
  if (action === 'toggle-select') {
    event.preventDefault();
    event.stopPropagation();
    const appointmentKey = button.dataset.appointmentKey;
    if (!appointmentKey) return;
    if (state.selectedAppointmentKeys.has(appointmentKey)) {
      state.selectedAppointmentKeys.delete(appointmentKey);
    } else {
      state.selectedAppointmentKeys.add(appointmentKey);
    }
    renderFilteredAppointments();
    return;
  }
  if (action === 'email') {
    const payload = appointmentEmailApi.buildAppointmentEmailRequest ? appointmentEmailApi.buildAppointmentEmailRequest({
      event_id: button.dataset.eventId,
      event_uid: button.dataset.eventUid,
      calendar_id: button.dataset.calendarId,
      summary: button.dataset.summary,
      start_iso: button.dataset.startIso,
      end_iso: button.dataset.endIso,
      timezone: button.dataset.timezone,
      attendee_emails: button.dataset.attendeeEmail ? [button.dataset.attendeeEmail] : [],
    }, state.selectedConfig) : null;
    if (!payload || !payload.attendeeEmail) {
      showToast('No attendee email is available for this appointment.', true);
      return;
    }
    button.disabled = true;
    appendLog({ source: 'appointment', line: `Sending email to ${payload.attendeeEmail} for event ${payload.eventId}.` });
    window.receptionist.sendAppointmentEmail(payload)
      .then((result) => {
        const recipient = result.recipient_email || payload.attendeeEmail;
        appendLog({ source: 'appointment', line: `Sent email to ${recipient} for event ${payload.eventId}.` });
        showToast(`Email sent to ${recipient}.`);
      })
      .catch((error) => {
        showToast(error.message, true);
      })
      .finally(() => {
        button.disabled = false;
      });
    return;
  }
  const label = action === 'sms'
    ? 'SMS template ready in Settings.'
    : 'Call script ready in Settings.';
  appendLog({ source: 'appointment', line: `${action.toUpperCase()} selected for event ${button.dataset.eventId}. ${label}` });
  showToast(`${action.toUpperCase()} selected. Use the customizable template in Settings.`);
});

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!isSelectionMode()) return;
  resetSelectionState();
  renderFilteredAppointments();
});

document.querySelectorAll('[data-reminder]').forEach((button) => {
  button.addEventListener('click', () => runReminderCommand(button.dataset.reminder));
});

setView('operate');
renderCalendarFilter();
syncDeleteControl();
loadBusinesses().catch((error) => showToast(error.message, true));





