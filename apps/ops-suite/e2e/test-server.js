// Ops Suite E2E -- paylasilan test-sunucusu yardimcisi.
//
// **Neden dosya-basina bir sunucu (T44, PLAN.md -- gercek bir kosuda GERCEKTEN
// kesfedilen hata):** Tek bir PAYLASILAN sunucu (eskiden `global-setup.js`
// TUM test dosyalari icin TEK SEFER baslatiyordu), dosyalar ARASI durum
// sizintisina yol acti -- `interactions.spec.js` (B049) gercek sesli
// komutlar gonderip sunucunun `HeartbeatTracker`/`AssistantPresenceTracker`/
// `ApprovalQueueStore` durumunu degistiriyordu; `scene.spec.js`'in "gecis 1/2/3"
// testleri ise BASLANGIC/PRISTINE durumu (orchestrator=offline,
// assistant=idle, pending_approval_count=0) VARSAYIYORDU -- dosyalar
// birlikte calistirildiginda (tek `npx playwright test`, ayri ayri
// DEGIL) 4 test GERCEKTEN basarisiz oldu. **Duzeltme:** her spec dosyasi
// artik KENDI `test.beforeAll`/`afterAll` cifti ile KENDI izole
// sunucusunu (kendi gecici veri dizini + GERCEK bir alt-surec) baslatip
// durduruyor -- dosyalar arasi SIZINTI YAPISAL olarak imkansiz.

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
const DEFAULT_PORT = Number(process.env.OPS_SUITE_E2E_PORT || '8421');

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

// `port` verilmezse varsayilan (8421) kullanilir -- dosyalar SIRALI
// calistigi surece (playwright.config.js::workers=1) ayni portun
// TEKRAR kullanilmasi GUVENLIDIR (onceki sunucu `stop()` ile TAMAMEN
// kapatildiktan SONRA bir sonraki `beforeAll` calisir).
async function startTestServer(port) {
  port = port || DEFAULT_PORT;
  const pythonPathEntries = [
    OPS_SUITE_BACKEND_SRC,
    path.join(REPO_ROOT, 'apps', 'orchestrator', 'src'),
    path.join(REPO_ROOT, 'services', 'tr-en-bridge', 'src'),
    path.join(REPO_ROOT, 'services', 'model-gateway', 'src'),
    path.join(REPO_ROOT, 'tools', 'cli-runner', 'src'),
    path.join(REPO_ROOT, 'tools'),
  ];

  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ops-suite-e2e-data-'));

  // scene.spec.js'in owner-onay gecisi GERCEK bir Bearer token
  // gerektirir -- bu, GERCEK bir kisi DEGIL, yalnizca bu kosum icin
  // uretilen gecici bir kimlik (bkz. scripts/ops_suite_demo.py'nin
  // ayni deseni).
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

  const env = {
    ...process.env,
    PYTHONPATH: pythonPathEntries.join(path.delimiter),
    OPS_SUITE_PORT: String(port),
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

  const baseUrl = `http://127.0.0.1:${port}`;
  try {
    await waitForServer(baseUrl + '/api/agents', 15000);
  } catch (err) {
    serverProcess.kill();
    throw new Error(`${err.message}\n--- sunucu log kuyrugu ---\n${serverLog.slice(-4000)}`);
  }

  return {
    baseUrl,
    ownerToken,
    async stop() {
      serverProcess.kill();
      try {
        fs.rmSync(dataDir, { recursive: true, force: true });
      } catch (err) {
        console.warn('ops-suite e2e: gecici veri dizini temizlenemedi', dataDir, err.message);
      }
    },
  };
}

module.exports = { startTestServer, DEFAULT_PORT };
