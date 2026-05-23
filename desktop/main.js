const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const { Menu } = require('electron');
const { autoUpdater } = require('electron-updater');

const projectRoot = path.resolve(__dirname, '..');
const pythonCmd = process.env.PYTHON || process.env.PYTHON_EXECUTABLE || 'python';
let mainWindow;
let agentProcess = null;
let updateReady = false;

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

function setupAutoUpdates() {
  if (!app.isPackaged) {
    emitLog('updater', 'Skipping auto-update check in development mode.');
    return;
  }

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.allowPrerelease = false;
  autoUpdater.allowDowngrade = false;

  autoUpdater.on('checking-for-update', () => {
    emitLog('updater', 'Checking for updates...');
    emitUpdate({ state: 'checking' });
  });

  autoUpdater.on('update-available', (info) => {
    const version = info?.version || '';
    const currentVersion = app.getVersion();
    if (!version || compareSemver(version, currentVersion) <= 0) {
      emitLog('updater', `Ignoring non-newer update candidate (${version || 'unknown'}) vs current ${currentVersion}.`);
      return;
    }
    emitLog('updater', `Update available: ${version}`);
    emitUpdate({ state: 'available', version });
  });

  autoUpdater.on('download-progress', (progress) => {
    const percent = Math.max(0, Math.min(100, Number(progress?.percent || 0)));
    emitUpdate({ state: 'downloading', percent: Math.round(percent) });
  });

  autoUpdater.on('update-not-available', () => {
    emitLog('updater', 'No updates available.');
    emitUpdate({ state: 'idle' });
  });

  autoUpdater.on('update-downloaded', (info) => {
    const version = info?.version || 'unknown';
    updateReady = true;
    emitLog('updater', `Update downloaded: ${version}. Awaiting restart.`);
    emitUpdate({ state: 'downloaded', version });
  });

  autoUpdater.on('error', (error) => {
    emitLog('updater:err', error?.message || 'Update error');
    emitUpdate({ state: 'error', message: error?.message || 'Auto-update failed.' });
  });

  autoUpdater.checkForUpdates().catch((error) => {
    emitLog('updater:err', error?.message || 'Failed to start auto-update check');
  });
}

function runPython(args, options = {}) {
  return new Promise((resolve) => {
    const child = spawn(pythonCmd, args, {
      cwd: projectRoot,
      env: { ...process.env, PYTHONUNBUFFERED: '1', ...(options.env || {}) },
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
    child.on('error', (error) => resolve({ ok: false, code: null, stdout, stderr: String(error) }));
    child.on('close', (code) => resolve({ ok: code === 0, code, stdout, stderr }));
  });
}

async function runDesktopConfig(args) {
  const result = await runPython(['-m', 'receptionist.desktop_config', ...args]);
  if (!result.ok) {
    throw new Error(result.stderr || result.stdout || `Command failed with code ${result.code}`);
  }
  try {
    return JSON.parse(result.stdout || '{}');
  } catch (error) {
    throw new Error(`Invalid JSON from desktop_config: ${error.message}\n${result.stdout}`);
  }
}

ipcMain.handle('business:list', async () => runDesktopConfig(['list-businesses']));
ipcMain.handle('business:get', async (_event, configPath) => runDesktopConfig(['get', '--config', configPath]));
ipcMain.handle('business:appointments', async (_event, configPath, options = {}) => {
  const args = ['appointments', '--config', configPath];
  if (options.startIso) args.push('--start-iso', options.startIso);
  if (options.endIso) args.push('--end-iso', options.endIso);
  if (options.limit) args.push('--limit', String(options.limit));
  return runDesktopConfig(args);
});
ipcMain.handle('business:emailSetup', async (_event, configPath) => runDesktopConfig(['email-setup', '--config', configPath]));
ipcMain.handle('business:emailUpdate', async (_event, payload) => {
  const args = ['email-update', '--config', payload.configPath];
  if (payload.fromAddress !== undefined) args.push('--from-address', payload.fromAddress);
  if (payload.smtpUsername !== undefined) args.push('--smtp-username', payload.smtpUsername);
  if (payload.smtpPassword !== undefined) args.push('--smtp-password', payload.smtpPassword);
  return runDesktopConfig(args);
});
ipcMain.handle('business:update', async (_event, payload) => {
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

ipcMain.handle('config:openExternal', async (_event, configPath) => {
  const { shell } = require('electron');
  await shell.openPath(path.resolve(projectRoot, configPath));
  return { ok: true };
});

ipcMain.handle('dialog:chooseConfig', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Choose business YAML',
    defaultPath: path.join(projectRoot, 'config', 'businesses'),
    filters: [{ name: 'YAML', extensions: ['yaml', 'yml'] }],
    properties: ['openFile'],
  });
  if (result.canceled || !result.filePaths.length) return null;
  return path.relative(projectRoot, result.filePaths[0]);
});

ipcMain.handle('agent:start', async (_event, options = {}) => {
  if (agentProcess) return { ok: false, message: 'Agent is already running.' };
  const env = { ...process.env, PYTHONUNBUFFERED: '1' };
  if (options.playgroundMode) env.RECEPTIONIST_AGENT_NAME = '';
  agentProcess = spawn(pythonCmd, ['-m', 'receptionist.agent', 'dev'], {
    cwd: projectRoot,
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
  const result = await runPython(['-m', 'receptionist.booking', 'setup', businessSlug], { stream: 'booking' });
  return result;
});

ipcMain.handle('appointment:send-email', async (_event, payload) => {
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
  const args = ['appointment-rename', '--config', payload.configPath];
  if (payload.calendarId !== undefined) args.push('--calendar-id', payload.calendarId);
  if (payload.eventId !== undefined) args.push('--event-id', payload.eventId);
  if (payload.summary !== undefined) args.push('--summary', payload.summary);
  return runDesktopConfig(args);
});

ipcMain.handle('appointment:delete', async (_event, payload) => {
  const args = ['appointment-delete', '--config', payload.configPath];
  if (payload.calendarId !== undefined) args.push('--calendar-id', payload.calendarId);
  if (payload.eventId !== undefined) args.push('--event-id', payload.eventId);
  return runDesktopConfig(args);
});

ipcMain.handle('reminders:run', async (_event, payload) => {
  const commandParts = String(payload.command || '').split(' ').filter(Boolean);
  const args = ['-m', 'receptionist.reminders', ...commandParts, '--business', payload.businessSlug];
  if (payload.command === 'sync' && payload.fixture) args.push('--fixture', payload.fixture);
  if (payload.command === 'run-due' && payload.now) args.push('--now', payload.now);
  const result = await runPython(args, { stream: 'reminders' });
  return result;
});

app.whenReady().then(() => {
  createWindow();
  setupAutoUpdates();
});
app.on('window-all-closed', () => {
  if (agentProcess) agentProcess.kill('SIGTERM');
  if (process.platform !== 'darwin') app.quit();
});
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
