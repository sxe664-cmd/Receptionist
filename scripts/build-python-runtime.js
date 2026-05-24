#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const rootDir = path.resolve(__dirname, '..');
const runtimeDir = path.join(rootDir, 'python-runtime');

function run(command, args, options = {}) {
  const pretty = `${command} ${args.join(' ')}`.trim();
  process.stdout.write(`\n[python-runtime] ${pretty}\n`);
  const result = spawnSync(command, args, {
    cwd: rootDir,
    stdio: 'inherit',
    shell: false,
    env: process.env,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`Command failed (${result.status}): ${pretty}`);
  }
}

function commandExists(command, args = ['--version']) {
  const result = spawnSync(command, args, {
    cwd: rootDir,
    stdio: 'ignore',
    shell: false,
    env: process.env,
  });
  if (result.error) return false;
  return result.status === 0;
}

function resolveHostPython() {
  if (process.env.PYTHON) return { command: process.env.PYTHON, prefix: [] };
  if (process.env.PYTHON_EXECUTABLE) return { command: process.env.PYTHON_EXECUTABLE, prefix: [] };
  if (process.platform === 'win32') {
    if (commandExists('py', ['-3', '--version'])) return { command: 'py', prefix: ['-3'] };
    if (commandExists('python', ['--version'])) return { command: 'python', prefix: [] };
    if (commandExists('python3', ['--version'])) return { command: 'python3', prefix: [] };
  } else {
    if (commandExists('python3', ['--version'])) return { command: 'python3', prefix: [] };
    if (commandExists('python', ['--version'])) return { command: 'python', prefix: [] };
  }
  return null;
}

function runtimePythonPath() {
  if (process.platform === 'win32') {
    return path.join(runtimeDir, 'Scripts', 'python.exe');
  }
  return path.join(runtimeDir, 'bin', 'python3');
}

function buildRuntime() {
  const hostPython = resolveHostPython();
  if (!hostPython) {
    throw new Error('No host Python found. Set PYTHON or install Python 3 on the build machine.');
  }

  fs.rmSync(runtimeDir, { recursive: true, force: true });
  const venvArgs = [...hostPython.prefix, '-m', 'venv', runtimeDir, '--copies'];
  run(hostPython.command, venvArgs);

  const pythonExe = runtimePythonPath();
  if (!fs.existsSync(pythonExe)) {
    throw new Error(`Bundled runtime executable missing at ${pythonExe}`);
  }

  run(pythonExe, ['-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel']);
  run(pythonExe, ['-m', 'pip', 'install', '.']);

  // Minimal sanity import set used by desktop flows.
  run(pythonExe, ['-c', 'import yaml, dotenv, google_auth_oauthlib; print("runtime-ok")']);
  process.stdout.write('\n[python-runtime] Build complete.\n');
}

try {
  buildRuntime();
} catch (error) {
  process.stderr.write(`\n[python-runtime] ERROR: ${error.message}\n`);
  process.exit(1);
}
