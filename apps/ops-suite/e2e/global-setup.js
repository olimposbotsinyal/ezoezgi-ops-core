// Testlerden ONCE, GERCEK bir `python -m ops_suite.server` alt-surecini
// baslatir (scripts/ops_suite_demo.py'nin PYTHONPATH insa deseniyle AYNI --
// bu liste, pyproject.toml'daki [tool.pytest.ini_options].pythonpath ile
// senkron tutulmalidir, cunku alt-surec pytest'in path enjeksiyonundan
// YARARLANAMAZ). Donen fonksiyon Playwright tarafindan global teardown
// olarak cagrilir -- sureci GERCEKTEN sonlandirir (zombie process birakmaz).

const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const E2E_DIR = __dirname;
const REPO_ROOT = path.resolve(E2E_DIR, '..', '..', '..');
const OPS_SUITE_BACKEND_SRC = path.join(REPO_ROOT, 'apps', 'ops-suite', 'backend', 'src');
const PYTHON_EXE = path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe');
const PORT = process.env.OPS_SUITE_E2E_PORT || '8421';

function waitForServer(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    function attempt() {
      const req = http.get(url, (res) => {
        res.resume();
        resolve();
      });
      req.on('error', () => {
        if (Date.now() > deadline) {
          reject(new Error(`Sunucu ${timeoutMs}ms icinde ayaga kalkmadi: ${url}`));
          return;
        }
        setTimeout(attempt, 200);
      });
    }
    attempt();
  });
}

module.exports = async function globalSetup() {
  const pythonPathEntries = [
    OPS_SUITE_BACKEND_SRC,
    path.join(REPO_ROOT, 'apps', 'orchestrator', 'src'),
    path.join(REPO_ROOT, 'services', 'tr-en-bridge', 'src'),
    path.join(REPO_ROOT, 'services', 'model-gateway', 'src'),
    path.join(REPO_ROOT, 'tools', 'cli-runner', 'src'),
    path.join(REPO_ROOT, 'tools'),
  ];

  const env = {
    ...process.env,
    PYTHONPATH: pythonPathEntries.join(path.delimiter),
    OPS_SUITE_PORT: PORT,
  };

  const serverProcess = spawn(PYTHON_EXE, ['-m', 'ops_suite.server'], {
    cwd: REPO_ROOT,
    env,
    stdio: 'pipe',
  });

  let serverLog = '';
  serverProcess.stdout.on('data', (chunk) => { serverLog += chunk.toString(); });
  serverProcess.stderr.on('data', (chunk) => { serverLog += chunk.toString(); });

  try {
    await waitForServer(`http://127.0.0.1:${PORT}/api/agents`, 15000);
  } catch (err) {
    serverProcess.kill();
    throw new Error(`${err.message}\n--- sunucu log kuyrugu ---\n${serverLog.slice(-4000)}`);
  }

  return async function globalTeardown() {
    serverProcess.kill();
  };
};
