// Testlerden ONCE, GERCEK bir `python -m ops_suite.server` alt-surecini
// baslatir (scripts/ops_suite_demo.py'nin PYTHONPATH insa deseniyle AYNI --
// bu liste, pyproject.toml'daki [tool.pytest.ini_options].pythonpath ile
// senkron tutulmalidir, cunku alt-surec pytest'in path enjeksiyonundan
// YARARLANAMAZ). Donen fonksiyon Playwright tarafindan global teardown
// olarak cagrilir -- sureci GERCEKTEN sonlandirir (zombie process birakmaz).
//
// **Veri izolasyonu (PLAN.md T36 -- gercek bir kosuda GERCEKTEN kesfedilen
// hata):** `OPS_SUITE_DATA_DIR` YOKSA, sunucu projenin GERCEK
// `data/approvals/approval_queue.jsonl`/`data/audit/audit.log.jsonl`
// dosyalarini kullanir -- her test kosusu (ozellikle onaylanmamis bir
// irreversible komut gonderen testler) kalici, hic silinmeyen SUBMITTED
// kayitlari biriktirir ve sonraki kosularda testleri BOZAR (ilk kosuda
// tam olarak bu yasandi). Bu yuzden her E2E kosusu KENDI izole gecici
// veri dizinini kullanir (bkz. `ops_suite/server.py::OPS_SUITE_DATA_DIR`).

const { spawn } = require('child_process');
const path = require('path');
const http = require('http');
const os = require('os');
const fs = require('fs');
const crypto = require('crypto');

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

  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ops-suite-e2e-data-'));

  // scene.spec.js'in owner-onay gecisini (T36, gecis 3) GERCEK bir
  // Bearer token ile calistirabilmesi icin -- bu, GERCEK bir kisi
  // DEGIL, yalnizca bu E2E kosusu icin uretilen gecici bir kimlik
  // (bkz. scripts/ops_suite_demo.py'nin ayni deseni).
  const ownerToken = crypto.randomBytes(24).toString('base64url');
  const identityConfigPath = path.join(dataDir, 'e2e_identities.json');
  fs.writeFileSync(identityConfigPath, JSON.stringify({
    schema_version: 1,
    owner: {
      actor_id: 'ops_suite_e2e_owner',
      display_name: "E2E Owner (yalniz bu test kosusu icin -- GERCEK bir kisi DEGIL)",
      token_env_var: 'OPS_SUITE_OWNER_TOKEN',
    },
    delegates: [],
  }, null, 2), 'utf-8');

  process.env.OPS_SUITE_E2E_OWNER_TOKEN = ownerToken;

  const env = {
    ...process.env,
    PYTHONPATH: pythonPathEntries.join(path.delimiter),
    OPS_SUITE_PORT: PORT,
    OPS_SUITE_DATA_DIR: dataDir,
    OPS_SUITE_IDENTITY_CONFIG_PATH: identityConfigPath,
    OPS_SUITE_OWNER_TOKEN: ownerToken,
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
    try {
      fs.rmSync(dataDir, { recursive: true, force: true });
    } catch (err) {
      // temp dizin temizligi basarisiz olsa bile testlerin sonucunu ETKILEMEZ
      console.warn('ops-suite e2e: gecici veri dizini temizlenemedi', dataDir, err.message);
    }
  };
};
