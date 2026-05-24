const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

function parseArgs(argv) {
  const out = {};
  for (let i = 2; i < argv.length; i += 1) {
    const item = argv[i];
    if (!item.startsWith('--')) continue;
    const key = item.slice(2);
    const value = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : 'true';
    out[key] = value;
    if (value !== 'true') i += 1;
  }
  return out;
}

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: 'inherit',
    shell: false,
    env: process.env,
  });
  if (result.status !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(' ')}`);
  }
}

function findPythonCommand(platform) {
  const candidates = [];
  if (process.env.PYTHON) candidates.push({ cmd: process.env.PYTHON, args: ['--version'] });
  if (process.env.PYTHON_EXECUTABLE) candidates.push({ cmd: process.env.PYTHON_EXECUTABLE, args: ['--version'] });
  if (platform === 'win') {
    candidates.push({ cmd: 'py', args: ['-3.11', '--version'] });
    candidates.push({ cmd: 'py', args: ['-3', '--version'] });
    candidates.push({ cmd: 'python', args: ['--version'] });
  } else {
    candidates.push({ cmd: 'python3', args: ['--version'] });
    candidates.push({ cmd: 'python', args: ['--version'] });
  }

  for (const candidate of candidates) {
    const result = spawnSync(candidate.cmd, candidate.args, { stdio: 'ignore', shell: false, env: process.env });
    if (result.status === 0) {
      return candidate.cmd === 'py'
        ? { command: 'py', venvArgs: candidate.args[0] === '-3.11' ? ['-3.11', '-m', 'venv'] : ['-3', '-m', 'venv'] }
        : { command: candidate.cmd, venvArgs: ['-m', 'venv'] };
    }
  }
  throw new Error('No usable Python interpreter found on PATH.');
}

function main() {
  const args = parseArgs(process.argv);
  const repoRoot = path.resolve(__dirname, '..');
  const platform = String(args.platform || process.platform);
  const outDir = path.resolve(repoRoot, args.out || path.join('desktop', 'python-runtime', platform));

  if (fs.existsSync(outDir)) fs.rmSync(outDir, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(outDir), { recursive: true });

  const py = findPythonCommand(platform);
  run(py.command, [...py.venvArgs, outDir], repoRoot);

  const pythonBin = platform === 'win'
    ? path.join(outDir, 'Scripts', 'python.exe')
    : path.join(outDir, 'bin', 'python3');

  run(pythonBin, ['-m', 'pip', 'install', '--upgrade', 'pip'], repoRoot);
  run(pythonBin, ['-m', 'pip', 'install', '.'], repoRoot);
}

main();
