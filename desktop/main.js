const { app, BrowserWindow, dialog, ipcMain, Menu, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const { autoUpdater } = require('electron-updater');

const repoRoot = path.resolve(__dirname, '..');
const devPythonCmd = process.env.PYTHON || process.env.PYTHON_EXECUTABLE || 'python';
const STARTER_BUSINESS_FILE = 'starter-business.yaml';

let mainWindow;
let agentProcess = null;
let desktopRuntime = null;
let updateReady = false;
let updateState = { state: 'idle' };
let updateAvailableInfo = null;

function compareSemver(left, right) {
  const l = String(left || '').split('.').map((part) => Number.parseInt(part, 10) || 0);
  const r = String(right || '').split('.').map((part) => Number.parseInt(part, 10) || 0);
  const len = Math.max(l.length, r.length);
  for (let i = 0; i < len; i += 1) {
    const lv = l[i] || 0;
    const rv = r[i] || 0;
    if (lv > rv) return 1;
    if (lv < rv) return -1;
  }
  return 0;
}

function createWindow() {
  Menu.setApplicationMenu(null);
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 820,
    minWidth: 980,
    minHeight: 680,
    title: 'HIRA Receptionist Console',
    backgroundColor: '#0f172a',
    frame: false,
    titleBarStyle: 'hidden',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    autoHideMenuBar: true,
  });

  mainWindow.removeMenu();
  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile(path.join(__dirname, 'index.html'));
}

function emitLog(source, line) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('process-log', {
    source,
    line: String(line || '').replace(/\r?\n$/, ''),
    at: new Date().toISOString(),
  });
}

function emitUpdate(payload) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('update-status', {
    at: new Date().toISOString(),
    ...payload,
  });
}

function resolvePackagedPythonCmd() {
  const runtimeRoot = process.platform === 'win32'
    ? path.join(process.resourcesPath, 'python-runtime', 'win')
    : process.platform === 'darwin'
      ? path.join(process.resourcesPath, 'python-runtime', 'mac')
      : path.join(process.resourcesPath, 'python-runtime', 'linux');
  const pythonCmd = process.platform === 'win32'
    ? path.join(runtimeRoot, 'python.exe')
    : path.join(runtimeRoot, 'bin', 'python3');
  return { runtimeRoot, pythonCmd };
}

function resolveEmbeddedOAuthClientPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'oauth', 'google-calendar-oauth-client.json');
  }
  const localCandidate = path.join(repoRoot, 'desktop', 'oauth', 'google-calendar-oauth-client.json');
  if (fs.existsSync(localCandidate)) return localCandidate;
  return path.join(repoRoot, 'secrets', 'santiago', 'google-calendar-oauth-client.json');
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function starterYamlTemplate() {
  return `mode: demo

business:
  name: "Starter Business"
  type: "appointment-based office"
  timezone: "America/New_York"

communications:
  default_transfer_number: "+15551234567"
  email_from: "Front Desk <contact@example.com>"
  sms_from_number: "+15557654321"

message_templates:
  confirmation_email_subject: "Appointment confirmed: {appointment_time}"
  confirmation_email_text: "Hi {recipient_name}, your appointment with {business_name} is confirmed for {appointment_time}."
  confirmation_sms: "{business_name}: your appointment is confirmed for {appointment_time}."
  reminder_email_subject: "Appointment reminder: {appointment_time}"
  reminder_email_text: "Hi {recipient_name}, this is a reminder about your appointment on {appointment_time}."
  reminder_sms: "{business_name}: reminder for your appointment on {appointment_time}."
  quick_sms: ""
  quick_email: ""
  quick_call_script: ""

email:
  from: "Front Desk <contact@example.com>"
  sender:
    type: "gmail_oauth"
    gmail_oauth:
      oauth_token_file: "~/.aireceptionist/secrets/starter-business/google-calendar-oauth.json"
  triggers:
    on_message: true
    on_call_end: false
    on_booking: true

calendar:
  enabled: true
  calendar_id: "primary"
  auth:
    type: "oauth"
    oauth_token_file: "~/.aireceptionist/secrets/starter-business/google-calendar-oauth.json"
  appointment_duration_minutes: 30
  buffer_minutes: 15
  buffer_placement: "after"
  booking_window_days: 30
  earliest_booking_hours_ahead: 2

reminders:
  enabled: true
  offset_days: [4, 1]
  channels: ["email"]
  store_path: "./messages/starter-reminders.sqlite3"
  contacts_path: "./config/businesses/starter-contacts.yaml"
  lookahead_days: 60
  allow_retroactive_send: false
  email_provider: "configured"
`;
}

function ensureStarterBusinessConfig(runtime) {
  const configDir = runtime.businessConfigDir;
  const existing = fs.readdirSync(configDir).filter((name) => /\.ya?ml$/i.test(name));
  if (existing.length > 0) return;

  const templateCandidates = [
    path.join(runtime.appContentRoot, 'config', 'businesses', 'example-dental.yaml'),
    path.join(runtime.appContentRoot, 'config', 'businesses', 'example-workers-comp.yaml'),
  ];
  const destination = path.join(configDir, STARTER_BUSINESS_FILE);
  const template = templateCandidates.find((candidate) => fs.existsSync(candidate));
  if (template) {
    fs.copyFileSync(template, destination);
    return;
  }
  fs.writeFileSync(destination, starterYamlTemplate(), 'utf8');
}

function bootstrapDesktopRuntime() {
  const workspaceRoot = app.isPackaged ? path.join(app.getPath('userData'), 'workspace') : repoRoot;
  const businessConfigDir = path.join(workspaceRoot, 'config', 'businesses');
  const secretsDir = path.join(workspaceRoot, 'secrets');
  const messagesDir = path.join(workspaceRoot, 'messages');
  const recordingsDir = path.join(workspaceRoot, 'recordings');
  const transcriptsDir = path.join(workspaceRoot, 'transcripts');
  const appContentRoot = app.getAppPath();
  const oauthClientSourcePath = resolveEmbeddedOAuthClientPath();
  const packaged = app.isPackaged;
  const packagedPython = packaged ? resolvePackagedPythonCmd() : null;

  const runtime = {
    packaged,
    appContentRoot,
    workspaceRoot,
    businessConfigDir,
    secretsDir,
    messagesDir,
    recordingsDir,
    transcriptsDir,
    oauthClientSourcePath,
    pythonRuntimeRoot: packagedPython?.runtimeRoot || null,
    pythonCmd: packagedPython?.pythonCmd || devPythonCmd,
    pythonAvailable: true,
    workspaceWritable: true,
  };

  ensureDir(runtime.workspaceRoot);
  ensureDir(runtime.businessConfigDir);
  ensureDir(runtime.secretsDir);
  ensureDir(runtime.messagesDir);
  ensureDir(runtime.recordingsDir);
  ensureDir(runtime.transcriptsDir);
  ensureStarterBusinessConfig(runtime);

  if (runtime.packaged && !fs.existsSync(runtime.pythonCmd)) {
    runtime.pythonAvailable = false;
  }

  const writeProbePath = path.join(runtime.workspaceRoot, '.write-probe');
  try {
    fs.writeFileSync(writeProbePath, 'ok', 'utf8');
    fs.unlinkSync(writeProbePath);
  } catch (_error) {
    runtime.workspaceWritable = false;
  }

  desktopRuntime = runtime;
  emitLog('runtime', `Workspace root: ${runtime.workspaceRoot}`);
  if (runtime.packaged) emitLog('runtime', `Bundled Python: ${runtime.pythonCmd}`);
}

function buildPythonEnv(extraEnv = {}) {
  return {
    ...process.env,
    PYTHONUNBUFFERED: '1',
    RECEPTIONIST_DESKTOP_ROOT: desktopRuntime.workspaceRoot,
    RECEPTIONIST_DESKTOP_EMBEDDED_OAUTH_CLIENT: desktopRuntime.oauthClientSourcePath,
    ...(extraEnv || {}),
  };
}

function preflightPython({ configPath, businessSlug, requireOAuthClient = false } = {}) {
  if (!desktopRuntime) {
    return { ok: false, code: 'runtime_not_initialized', message: 'Desktop runtime is not initialized.' };
  }
  if (!desktopRuntime.workspaceWritable) {
    return { ok: false, code: 'workspace_not_writable', message: `Desktop workspace is not writable: ${desktopRuntime.workspaceRoot}` };
  }
  if (!desktopRuntime.pythonAvailable) {
    return {
      ok: false,
      code: 'python_runtime_missing',
      message: `Bundled Python runtime not found at ${desktopRuntime.pythonCmd}.`,
      details: 'Reinstall the desktop app to restore runtime resources.',
    };
  }
  if (configPath) {
    const resolved = resolveWorkspacePath(configPath);
    if (!resolved.ok) return resolved;
    if (!fs.existsSync(resolved.path)) {
      return {
        ok: false,
        code: 'config_not_found',
        message: `Business config not found: ${configPath}`,
      };
    }
  }
  if (requireOAuthClient) {
    if (!businessSlug) {
      return { ok: false, code: 'missing_business_slug', message: 'Business slug is required for Google setup.' };
    }
    const oauthCheck = ensureWorkspaceOAuthClient(businessSlug);
    if (!oauthCheck.ok) return oauthCheck;
  }
  return { ok: true };
}

function resolveWorkspacePath(relativeOrAbsolutePath) {
  const base = desktopRuntime.workspaceRoot;
  const input = String(relativeOrAbsolutePath || '');
  const resolved = path.isAbsolute(input) ? path.resolve(input) : path.resolve(base, input);
  if (desktopRuntime.packaged && !resolved.startsWith(base)) {
    return {
      ok: false,
      code: 'path_outside_workspace',
      message: `Path is outside desktop workspace: ${input}`,
    };
  }
  return { ok: true, path: resolved };
}

function ensureWorkspaceOAuthClient(businessSlug) {
  const normalizedSlug = String(businessSlug || '').trim();
  if (!normalizedSlug) {
    return { ok: false, code: 'invalid_business_slug', message: 'Business slug is empty.' };
  }
  const sourcePath = desktopRuntime.oauthClientSourcePath;
  if (!sourcePath || !fs.existsSync(sourcePath)) {
    return {
      ok: false,
      code: 'oauth_client_missing',
      message: `Embedded OAuth client JSON not found at ${sourcePath || 'unknown path'}.`,
      details: 'Configure and bundle desktop OAuth client JSON during build.',
    };
  }
  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(sourcePath, 'utf8'));
  } catch (error) {
    return {
      ok: false,
      code: 'oauth_client_invalid_json',
      message: `Embedded OAuth client JSON is not valid JSON: ${error.message}`,
    };
  }
  if (!parsed?.installed?.client_id || !parsed?.installed?.client_secret) {
    return {
      ok: false,
      code: 'oauth_client_invalid_shape',
      message: 'Embedded OAuth client JSON is missing installed.client_id/client_secret.',
    };
  }
  const destinationDir = path.join(desktopRuntime.secretsDir, normalizedSlug);
  const destinationPath = path.join(destinationDir, 'google-calendar-oauth-client.json');
  ensureDir(destinationDir);
  if (!fs.existsSync(destinationPath)) {
    fs.copyFileSync(sourcePath, destinationPath);
  }
  return { ok: true, path: destinationPath };
}

function formatPreflightError(check) {
  return JSON.stringify({
    ok: false,
    error: {
      code: check.code || 'runtime_error',
      message: check.message || 'Desktop runtime preflight failed.',
      details: check.details || null,
    },
  });
}

function setupUpdaterEvents() {
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.allowPrerelease = false;
  autoUpdater.allowDowngrade = false;

  autoUpdater.on('checking-for-update', () => {
    emitLog('updater', 'Checking for updates...');
    updateState = { state: 'checking' };
    emitUpdate(updateState);
  });

  autoUpdater.on('update-available', (info) => {
    const version = info?.version || '';
    const currentVersion = app.getVersion();
    if (!version || compareSemver(version, currentVersion) <= 0) {
      emitLog('updater', `Ignoring non-newer update candidate (${version || 'unknown'}) vs current ${currentVersion}.`);
      return;
    }
    emitLog('updater', `Update available: ${version}`);
    updateAvailableInfo = info;
    updateState = { state: 'available', version };
    emitUpdate(updateState);
  });

  autoUpdater.on('download-progress', (progress) => {
    const percent = Math.max(0, Math.min(100, Number(progress?.percent || 0)));
    updateState = { state: 'downloading', percent: Math.round(percent) };
    emitUpdate(updateState);
  });

  autoUpdater.on('update-not-available', () => {
    emitLog('updater', 'No updates available.');
    updateAvailableInfo = null;
    updateReady = false;
    updateState = { state: 'idle', message: 'Up to date' };
    emitUpdate(updateState);
  });

  autoUpdater.on('update-downloaded', (info) => {
    const version = info?.version || 'unknown';
    updateReady = true;
    emitLog('updater', `Update downloaded: ${version}. Awaiting restart.`);
    updateState = { state: 'downloaded', version };
    emitUpdate(updateState);
  });

  autoUpdater.on('error', (error) => {
    const message = error?.message || 'Auto-update failed.';
    emitLog('updater:err', message);
    updateState = { state: 'error', message };
    emitUpdate(updateState);
  });
}

function runPython(args, options = {}) {
  return new Promise((resolve) => {
    const child = spawn(desktopRuntime.pythonCmd, args, {
      cwd: desktopRuntime.workspaceRoot,
      env: buildPythonEnv(options.env),
      shell: false,
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => {
      const text = chunk.toString();
      stdout += text;
      if (options.stream) text.split(/\r?\n/).filter(Boolean).forEach((line) => emitLog(options.stream, line));
    });
    child.stderr.on('data', (chunk) => {
      const text = chunk.toString();
      stderr += text;
      if (options.stream) text.split(/\r?\n/).filter(Boolean).forEach((line) => emitLog(`${options.stream}:err`, line));
    });
    child.on('error', (error) => resolve({
      ok: false,
      code: null,
      stdout,
      stderr: String(error),
      error: { code: 'python_spawn_failed', message: error.message },
    }));
    child.on('close', (code) => resolve({
      ok: code === 0,
      code,
      stdout,
      stderr,
      error: code === 0
        ? null
        : { code: 'python_process_failed', message: stderr || stdout || `Command failed with code ${code}` },
    }));
  });
}

async function runDesktopConfig(args, options = {}) {
  const result = await runPython(['-m', 'receptionist.desktop_config', ...args], options);
  if (!result.ok) {
    throw new Error(result.stderr || result.stdout || `Command failed with code ${result.code}`);
  }
  try {
    return JSON.parse(result.stdout || '{}');
  } catch (error) {
    throw new Error(`Invalid JSON from desktop_config: ${error.message}\n${result.stdout}`);
  }
}

ipcMain.handle('business:list', async () => {
  const check = preflightPython();
  if (!check.ok) throw new Error(formatPreflightError(check));
  return runDesktopConfig(['list-businesses']);
});

ipcMain.handle('business:get', async (_event, configPath) => {
  const check = preflightPython({ configPath });
  if (!check.ok) throw new Error(formatPreflightError(check));
  return runDesktopConfig(['get', '--config', configPath]);
});

ipcMain.handle('business:appointments', async (_event, configPath, options = {}) => {
  const check = preflightPython({ configPath });
  if (!check.ok) throw new Error(formatPreflightError(check));
  const args = ['appointments', '--config', configPath];
  if (options.startIso) args.push('--start-iso', options.startIso);
  if (options.endIso) args.push('--end-iso', options.endIso);
  if (options.limit) args.push('--limit', String(options.limit));
  return runDesktopConfig(args);
});

ipcMain.handle('business:emailSetup', async (_event, configPath) => {
  const check = preflightPython({ configPath });
  if (!check.ok) throw new Error(formatPreflightError(check));
  return runDesktopConfig(['email-setup', '--config', configPath]);
});

ipcMain.handle('business:emailUpdate', async (_event, payload) => {
  const check = preflightPython({ configPath: payload?.configPath });
  if (!check.ok) throw new Error(formatPreflightError(check));
  const args = ['email-update', '--config', payload.configPath];
  if (payload.fromAddress !== undefined) args.push('--from-address', payload.fromAddress);
  if (payload.smtpUsername !== undefined) args.push('--smtp-username', payload.smtpUsername);
  if (payload.smtpPassword !== undefined) args.push('--smtp-password', payload.smtpPassword);
  return runDesktopConfig(args);
});

ipcMain.handle('business:update', async (_event, payload) => {
  const check = preflightPython({ configPath: payload?.configPath });
  if (!check.ok) throw new Error(formatPreflightError(check));
  const args = ['update', '--config', payload.configPath];
  if (payload.mode) args.push('--mode', payload.mode);
  if (payload.defaultTransferNumber !== undefined) args.push('--default-transfer-number', payload.defaultTransferNumber);
  if (payload.emailFrom !== undefined) args.push('--email-from', payload.emailFrom);
  if (payload.smsFromNumber !== undefined) args.push('--sms-from-number', payload.smsFromNumber);
  if (payload.confirmationEmailSubject !== undefined) args.push('--confirmation-email-subject', payload.confirmationEmailSubject);
  if (payload.confirmationEmailText !== undefined) args.push('--confirmation-email-text', payload.confirmationEmailText);
  if (payload.confirmationSms !== undefined) args.push('--confirmation-sms', payload.confirmationSms);
  if (payload.reminderEmailSubject !== undefined) args.push('--reminder-email-subject', payload.reminderEmailSubject);
  if (payload.reminderEmailText !== undefined) args.push('--reminder-email-text', payload.reminderEmailText);
  if (payload.reminderSms !== undefined) args.push('--reminder-sms', payload.reminderSms);
  if (payload.quickSms !== undefined) args.push('--quick-sms', payload.quickSms);
  if (payload.quickEmail !== undefined) args.push('--quick-email', payload.quickEmail);
  if (payload.quickCallScript !== undefined) args.push('--quick-call-script', payload.quickCallScript);
  return runDesktopConfig(args);
});

ipcMain.handle('window:minimize', async () => {
  mainWindow?.minimize();
  return { ok: true };
});

ipcMain.handle('window:toggle-maximize', async () => {
  if (!mainWindow) return { ok: false };
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
    return { ok: true, maximized: false };
  }
  mainWindow.maximize();
  return { ok: true, maximized: true };
});

ipcMain.handle('window:close', async () => {
  mainWindow?.close();
  return { ok: true };
});

ipcMain.handle('update:install', async () => {
  if (!updateReady) return { ok: false, message: 'No downloaded update is ready to install.' };
  setImmediate(() => autoUpdater.quitAndInstall(false, true));
  return { ok: true };
});

ipcMain.handle('update:check', async () => {
  if (!app.isPackaged) {
    const message = 'Update checks are only available in installed builds.';
    updateState = { state: 'error', message };
    emitUpdate(updateState);
    return { ok: false, message };
  }
  updateReady = false;
  updateAvailableInfo = null;
  updateState = { state: 'checking' };
  emitUpdate(updateState);
  try {
    await autoUpdater.checkForUpdates();
    return { ok: true };
  } catch (error) {
    const message = error?.message || 'Failed to check for updates.';
    updateState = { state: 'error', message };
    emitUpdate(updateState);
    return { ok: false, message };
  }
});

ipcMain.handle('update:download', async () => {
  if (!app.isPackaged) {
    const message = 'Update downloads are only available in installed builds.';
    updateState = { state: 'error', message };
    emitUpdate(updateState);
    return { ok: false, message };
  }
  if (!updateAvailableInfo) {
    return { ok: false, message: 'No available update to download.' };
  }
  updateState = { state: 'downloading', percent: 0 };
  emitUpdate(updateState);
  try {
    await autoUpdater.downloadUpdate();
    return { ok: true };
  } catch (error) {
    const message = error?.message || 'Failed to download update.';
    updateState = { state: 'error', message };
    emitUpdate(updateState);
    return { ok: false, message };
  }
});

ipcMain.handle('config:openExternal', async (_event, configPath) => {
  const resolved = resolveWorkspacePath(configPath);
  if (!resolved.ok) return { ok: false, message: resolved.message };
  await shell.openPath(resolved.path);
  return { ok: true };
});

ipcMain.handle('dialog:chooseConfig', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Choose business YAML',
    defaultPath: desktopRuntime.businessConfigDir,
    filters: [{ name: 'YAML', extensions: ['yaml', 'yml'] }],
    properties: ['openFile'],
  });
  if (result.canceled || !result.filePaths.length) return null;
  return path.relative(desktopRuntime.workspaceRoot, result.filePaths[0]);
});

ipcMain.handle('agent:start', async (_event, options = {}) => {
  if (agentProcess) return { ok: false, message: 'Agent is already running.' };
  const check = preflightPython();
  if (!check.ok) {
    return { ok: false, message: check.message, error: check };
  }
  const env = buildPythonEnv(options.playgroundMode ? { RECEPTIONIST_AGENT_NAME: '' } : {});
  agentProcess = spawn(desktopRuntime.pythonCmd, ['-m', 'receptionist.agent', 'dev'], {
    cwd: desktopRuntime.workspaceRoot,
    env,
    shell: false,
  });
  emitLog('agent', `Started agent process pid=${agentProcess.pid}`);
  agentProcess.stdout.on('data', (chunk) => chunk.toString().split(/\r?\n/).filter(Boolean).forEach((line) => emitLog('agent', line)));
  agentProcess.stderr.on('data', (chunk) => chunk.toString().split(/\r?\n/).filter(Boolean).forEach((line) => emitLog('agent:err', line)));
  agentProcess.on('error', (error) => {
    emitLog('agent:err', error.message);
    agentProcess = null;
    mainWindow?.webContents.send('agent-status', { running: false });
  });
  agentProcess.on('close', (code) => {
    emitLog('agent', `Agent stopped with code ${code}`);
    agentProcess = null;
    mainWindow?.webContents.send('agent-status', { running: false });
  });
  mainWindow?.webContents.send('agent-status', { running: true });
  return { ok: true, pid: agentProcess.pid };
});

ipcMain.handle('agent:stop', async () => {
  if (!agentProcess) return { ok: false, message: 'Agent is not running.' };
  const pid = agentProcess.pid;
  agentProcess.kill('SIGTERM');
  emitLog('agent', `Stop requested for pid=${pid}`);
  return { ok: true };
});

ipcMain.handle('agent:status', async () => ({ running: Boolean(agentProcess), pid: agentProcess?.pid ?? null }));

ipcMain.handle('booking:setup', async (_event, businessSlug) => {
  const check = preflightPython({ businessSlug, requireOAuthClient: true });
  if (!check.ok) {
    return { ok: false, code: check.code, stderr: check.message, error: check };
  }
  return runPython(['-m', 'receptionist.booking', 'setup', businessSlug], { stream: 'booking' });
});

ipcMain.handle('appointment:send-email', async (_event, payload) => {
  const check = preflightPython({ configPath: payload?.configPath });
  if (!check.ok) throw new Error(formatPreflightError(check));
  const args = ['send-email', '--config', payload.configPath];
  if (payload.eventId !== undefined) args.push('--event-id', payload.eventId);
  if (payload.eventUid !== undefined) args.push('--event-uid', payload.eventUid);
  if (payload.calendarId !== undefined) args.push('--calendar-id', payload.calendarId);
  if (payload.summary !== undefined) args.push('--summary', payload.summary);
  if (payload.startIso !== undefined) args.push('--start-iso', payload.startIso);
  if (payload.endIso !== undefined) args.push('--end-iso', payload.endIso);
  if (payload.timezone !== undefined) args.push('--timezone', payload.timezone);
  if (payload.attendeeEmail !== undefined) args.push('--attendee-email', payload.attendeeEmail);
  return runDesktopConfig(args);
});

ipcMain.handle('appointment:rename', async (_event, payload) => {
  const check = preflightPython({ configPath: payload?.configPath });
  if (!check.ok) throw new Error(formatPreflightError(check));
  const args = ['appointment-rename', '--config', payload.configPath];
  if (payload.calendarId !== undefined) args.push('--calendar-id', payload.calendarId);
  if (payload.eventId !== undefined) args.push('--event-id', payload.eventId);
  if (payload.summary !== undefined) args.push('--summary', payload.summary);
  return runDesktopConfig(args);
});

ipcMain.handle('appointment:delete', async (_event, payload) => {
  const check = preflightPython({ configPath: payload?.configPath });
  if (!check.ok) throw new Error(formatPreflightError(check));
  const args = ['appointment-delete', '--config', payload.configPath];
  if (payload.calendarId !== undefined) args.push('--calendar-id', payload.calendarId);
  if (payload.eventId !== undefined) args.push('--event-id', payload.eventId);
  return runDesktopConfig(args);
});

ipcMain.handle('reminders:run', async (_event, payload) => {
  const check = preflightPython();
  if (!check.ok) {
    return { ok: false, code: check.code, stderr: check.message, error: check };
  }
  const commandParts = String(payload.command || '').split(' ').filter(Boolean);
  const args = ['-m', 'receptionist.reminders', ...commandParts, '--business', payload.businessSlug];
  if (payload.command === 'sync' && payload.fixture) args.push('--fixture', payload.fixture);
  if (payload.command === 'run-due' && payload.now) args.push('--now', payload.now);
  return runPython(args, { stream: 'reminders' });
});

app.whenReady().then(() => {
  bootstrapDesktopRuntime();
  createWindow();
  setupUpdaterEvents();
});
app.on('window-all-closed', () => {
  if (agentProcess) agentProcess.kill('SIGTERM');
  if (process.platform !== 'darwin') app.quit();
});
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
