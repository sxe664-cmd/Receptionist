const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const projectRoot = path.resolve(__dirname, '..');
const electronBin = path.join(
  projectRoot,
  'node_modules',
  '.bin',
  process.platform === 'win32' ? 'electron.cmd' : 'electron',
);

const watchedDirectories = [
  path.join(projectRoot, 'desktop'),
  path.join(projectRoot, 'config', 'businesses'),
];

const watchedFiles = [
  path.join(projectRoot, 'receptionist', 'desktop_config.py'),
];

let child = null;
let restarting = false;
let shuttingDown = false;
let restartTimer = null;
const directoryWatchers = [];

function startElectron() {
  child = spawn(electronBin, ['desktop/main.js'], {
    cwd: projectRoot,
    env: process.env,
    stdio: 'inherit',
    shell: false,
  });

  child.on('exit', (code, signal) => {
    child = null;
    if (shuttingDown) {
      process.exit(code ?? 0);
      return;
    }
    if (restarting) {
      restarting = false;
      startElectron();
      return;
    }
    const message = signal ? `Electron exited via ${signal}` : `Electron exited with code ${code ?? 0}`;
    console.error(message);
    process.exit(code ?? 0);
  });
}

function restartElectron(reason) {
  if (shuttingDown) return;
  clearTimeout(restartTimer);
  restartTimer = setTimeout(() => {
    console.log(`[desktop:dev] restart due to ${reason}`);
    if (!child) {
      startElectron();
      return;
    }
    if (!restarting) {
      restarting = true;
      child.kill();
    }
  }, 150);
}

function watchDirectory(directory) {
  try {
    const watcher = fs.watch(directory, { recursive: true }, (_eventType, filename) => {
      if (!filename) {
        restartElectron(directory);
        return;
      }
      restartElectron(path.join(directory, filename.toString()));
    });
    directoryWatchers.push(watcher);
    watcher.on('error', (error) => {
      console.error(`[desktop:dev] watch error for ${directory}: ${error.message}`);
    });
  } catch (error) {
    console.error(`[desktop:dev] failed to watch ${directory}: ${error.message}`);
  }
}

function watchFile(filePath) {
  try {
    fs.watchFile(filePath, { interval: 250 }, (curr, prev) => {
      if (curr.mtimeMs !== prev.mtimeMs || curr.size !== prev.size) {
        restartElectron(filePath);
      }
    });
  } catch (error) {
    console.error(`[desktop:dev] failed to watch ${filePath}: ${error.message}`);
  }
}

function stopWatching() {
  shuttingDown = true;
  clearTimeout(restartTimer);
  for (const watcher of directoryWatchers) {
    try {
      watcher.close();
    } catch (_error) {
      // noop
    }
  }
  for (const filePath of watchedFiles) {
    try {
      fs.unwatchFile(filePath);
    } catch (_error) {
      // noop
    }
  }
  if (child) {
    child.kill();
  } else {
    process.exit(0);
  }
}

process.on('SIGINT', stopWatching);
process.on('SIGTERM', stopWatching);

for (const directory of watchedDirectories) {
  watchDirectory(directory);
}
for (const filePath of watchedFiles) {
  watchFile(filePath);
}

startElectron();
