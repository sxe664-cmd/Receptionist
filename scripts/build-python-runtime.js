#!/usr/bin/env node

const fs = require('fs');
const https = require('https');
const path = require('path');
const { spawnSync } = require('child_process');

const rootDir = path.resolve(__dirname, '..');
const runtimeDir = path.join(rootDir, 'python-runtime');
const bundledBaseDirName = 'base';
const runtimeManifestName = 'runtime-manifest.json';
const standaloneRuntimeDir = path.join(rootDir, '.python-standalone-runtime');
const pythonBuildStandaloneRepo = 'astral-sh/python-build-standalone';
const macStandalonePythonMajorMinor = '3.14';

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

function download(url, destination) {
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  return new Promise((resolve, reject) => {
    const request = https.get(url, {
      headers: { 'User-Agent': 'AIReceptionist-build-runtime' },
    }, (response) => {
      if ([301, 302, 303, 307, 308].includes(response.statusCode || 0) && response.headers.location) {
        response.resume();
        download(response.headers.location, destination).then(resolve, reject);
        return;
      }
      if (response.statusCode !== 200) {
        response.resume();
        reject(new Error(`Download failed (${response.statusCode}): ${url}`));
        return;
      }
      const file = fs.createWriteStream(destination);
      response.pipe(file);
      file.on('finish', () => file.close(resolve));
      file.on('error', reject);
    });
    request.on('error', reject);
  });
}

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const request = https.get(url, {
      headers: { 'User-Agent': 'AIReceptionist-build-runtime' },
    }, (response) => {
      if (response.statusCode !== 200) {
        response.resume();
        reject(new Error(`GitHub API request failed (${response.statusCode}): ${url}`));
        return;
      }
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => { body += chunk; });
      response.on('end', () => {
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on('error', reject);
  });
}

function macStandaloneTarget() {
  if (process.arch === 'arm64') return 'aarch64-apple-darwin';
  if (process.arch === 'x64') return 'x86_64-apple-darwin';
  throw new Error(`Unsupported macOS architecture for standalone Python: ${process.arch}`);
}

async function resolveMacStandalonePython() {
  const target = macStandaloneTarget();
  const releaseOverride = process.env.PYTHON_BUILD_STANDALONE_RELEASE;
  const releasesUrl = releaseOverride
    ? `https://api.github.com/repos/${pythonBuildStandaloneRepo}/releases/tags/${releaseOverride}`
    : `https://api.github.com/repos/${pythonBuildStandaloneRepo}/releases/latest`;
  const release = await fetchJson(releasesUrl);
  const asset = (release.assets || []).find((candidate) => {
    const name = candidate.name || '';
    return name.startsWith(`cpython-${macStandalonePythonMajorMinor}.`)
      && name.includes(`-${target}-`)
      && !name.includes('-freethreaded-')
      && name.endsWith('-install_only_stripped.tar.gz');
  });
  if (!asset?.browser_download_url) {
    throw new Error(`No Python ${macStandalonePythonMajorMinor} standalone asset found for ${target} in ${release.tag_name || releasesUrl}`);
  }

  fs.rmSync(standaloneRuntimeDir, { recursive: true, force: true });
  fs.mkdirSync(standaloneRuntimeDir, { recursive: true });
  const archivePath = path.join(standaloneRuntimeDir, asset.name);
  process.stdout.write(`\n[python-runtime] Downloading ${asset.name}\n`);
  await download(asset.browser_download_url, archivePath);
  run('tar', ['-xzf', archivePath, '-C', standaloneRuntimeDir]);

  const installDir = path.join(standaloneRuntimeDir, 'python', 'install');
  const executable = path.join(installDir, 'bin', 'python3');
  if (!fs.existsSync(executable)) {
    throw new Error(`Standalone Python executable missing at ${executable}`);
  }
  return { command: executable, prefix: [] };
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

function capture(command, args) {
  const result = spawnSync(command, args, {
    cwd: rootDir,
    encoding: 'utf8',
    shell: false,
    env: process.env,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`Command failed (${result.status}): ${command} ${args.join(' ')}\n${result.stderr || ''}`);
  }
  return String(result.stdout || '').trim();
}

function normalizeForConfig(value) {
  return value.replace(/\\/g, '\\');
}

function rewritePyvenvConfig({ baseExecutable, venvExecutable }) {
  const cfgPath = path.join(runtimeDir, 'pyvenv.cfg');
  if (!fs.existsSync(cfgPath)) {
    throw new Error(`Bundled runtime config missing at ${cfgPath}`);
  }
  const home = path.dirname(baseExecutable);
  const lines = fs.readFileSync(cfgPath, 'utf8').split(/\r?\n/).filter(Boolean);
  const next = [];
  const seen = new Set();
  for (const line of lines) {
    const key = line.split('=')[0]?.trim();
    if (key === 'home') {
      next.push(`home = ${normalizeForConfig(home)}`);
      seen.add('home');
    } else if (key === 'executable') {
      next.push(`executable = ${normalizeForConfig(baseExecutable)}`);
      seen.add('executable');
    } else if (key === 'command') {
      next.push(`command = ${normalizeForConfig(baseExecutable)} -m venv ${normalizeForConfig(runtimeDir)}`);
      seen.add('command');
    } else {
      next.push(line);
    }
  }
  if (!seen.has('home')) next.push(`home = ${normalizeForConfig(home)}`);
  if (!seen.has('executable')) next.push(`executable = ${normalizeForConfig(baseExecutable)}`);
  if (!seen.has('command')) next.push(`command = ${normalizeForConfig(baseExecutable)} -m venv ${normalizeForConfig(runtimeDir)}`);
  fs.writeFileSync(cfgPath, `${next.join('\n')}\n`, 'utf8');

  const manifest = {
    baseDir: bundledBaseDirName,
    baseExecutable: path.relative(path.join(runtimeDir, bundledBaseDirName), baseExecutable),
    venvExecutable: path.relative(runtimeDir, venvExecutable),
  };
  fs.writeFileSync(path.join(runtimeDir, runtimeManifestName), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
}

function bundledExecutableCandidates(basePrefix, bundledBaseDir, baseExecutable) {
  const candidates = [];
  const relative = path.relative(basePrefix, baseExecutable);
  if (relative && !relative.startsWith('..') && !path.isAbsolute(relative)) {
    candidates.push(path.join(bundledBaseDir, relative));
  }
  candidates.push(path.join(bundledBaseDir, path.basename(baseExecutable)));
  candidates.push(path.join(bundledBaseDir, 'bin', path.basename(baseExecutable)));
  if (process.platform === 'win32') {
    candidates.push(path.join(bundledBaseDir, 'python.exe'));
  } else {
    candidates.push(path.join(bundledBaseDir, 'bin', 'python3'));
    candidates.push(path.join(bundledBaseDir, 'bin', 'python'));
  }
  return [...new Set(candidates)];
}

function bundleBasePython(pythonExe) {
  const raw = capture(pythonExe, ['-c', [
    'import json, sys',
    'print(json.dumps({"base_prefix": sys.base_prefix, "base_executable": getattr(sys, "_base_executable", sys.executable)}))',
  ].join('; ')]);
  const info = JSON.parse(raw);
  const basePrefix = path.resolve(info.base_prefix);
  const baseExecutable = path.resolve(info.base_executable);
  if (!fs.existsSync(basePrefix)) {
    throw new Error(`Base Python prefix not found at ${basePrefix}`);
  }
  if (!fs.existsSync(baseExecutable)) {
    throw new Error(`Base Python executable not found at ${baseExecutable}`);
  }

  const bundledBaseDir = path.join(runtimeDir, bundledBaseDirName);
  fs.rmSync(bundledBaseDir, { recursive: true, force: true });
  fs.cpSync(basePrefix, bundledBaseDir, {
    recursive: true,
    force: true,
    dereference: true,
    filter: (source) => {
      const relative = path.relative(basePrefix, source);
      if (!relative) return true;
      const parts = relative.split(path.sep);
      return !parts.some((part) => part === '__pycache__');
    },
  });

  const bundledBaseExecutable = bundledExecutableCandidates(basePrefix, bundledBaseDir, baseExecutable)
    .find((candidate) => fs.existsSync(candidate));
  if (!bundledBaseExecutable) {
    throw new Error(`Copied base Python executable missing. Checked: ${bundledExecutableCandidates(basePrefix, bundledBaseDir, baseExecutable).join(', ')}`);
  }
  rewritePyvenvConfig({ baseExecutable: bundledBaseExecutable, venvExecutable: pythonExe });
}

async function buildRuntime() {
  const hostPython = process.platform === 'darwin'
    ? await resolveMacStandalonePython()
    : resolveHostPython();
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

  bundleBasePython(pythonExe);

  // Minimal sanity import set used by desktop flows.
  run(pythonExe, ['-c', 'import yaml, dotenv, google_auth_oauthlib; print("runtime-ok")']);
  process.stdout.write('\n[python-runtime] Build complete.\n');
}

try {
  buildRuntime().catch((error) => {
    process.stderr.write(`\n[python-runtime] ERROR: ${error.message}\n`);
    process.exit(1);
  });
} catch (error) {
  process.stderr.write(`\n[python-runtime] ERROR: ${error.message}\n`);
  process.exit(1);
}
