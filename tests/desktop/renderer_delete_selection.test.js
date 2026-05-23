const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const rendererPath = path.join(__dirname, '..', '..', 'desktop', 'renderer.js');
const rendererSource = fs.readFileSync(rendererPath, 'utf8');

test('renderer uses selection-mode delete workflow', () => {
  assert.match(rendererSource, /selectionMode:\s*false/);
  assert.match(rendererSource, /selectedAppointmentKeys:\s*new Set\(\)/);
  assert.match(rendererSource, /data-appointment-action=\"toggle-select\"/);
  assert.match(rendererSource, /Delete Selected/);
  assert.match(rendererSource, /Promise\.allSettled/);
});

test('renderer wires calendar appointment filtering controls', () => {
  assert.match(rendererSource, /selectedDate:\s*new Date\(\)/);
  assert.match(rendererSource, /calendarView:\s*'day'/);
  assert.match(rendererSource, /visibleRange:\s*null/);
  assert.match(rendererSource, /data-calendar-view/);
  assert.match(rendererSource, /function getVisibleRange\(\)/);
  assert.match(rendererSource, /function filteredAppointments\(\)/);
  assert.match(rendererSource, /renderAppointments\(filteredAppointments\(\), \{ updateCache: false \}\)/);
  assert.match(rendererSource, /APPOINTMENT_LOAD_LIMIT/);
});

test('renderer no longer contains overflow menu implementation', () => {
  assert.doesNotMatch(rendererSource, /appointment-overflow/);
  assert.doesNotMatch(rendererSource, /data-appointment-menu-toggle/);
  assert.doesNotMatch(rendererSource, /renameAppointment/);
});
