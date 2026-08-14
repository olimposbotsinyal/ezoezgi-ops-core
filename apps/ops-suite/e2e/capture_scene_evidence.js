#!/usr/bin/env node
// Ops Suite ofis sahnesi -- GERCEK kanit yakalama scripti (BACKLOG.md
// B038, PLAN.md T36). `apps/ops-suite/e2e/tests/scene.spec.js`'in
// TAMAMLAYICISIDIR (Playwright test runner'i DEGIL, bagimsiz bir Node
// scripti -- `scripts/ops_suite_demo.py`'nin ayni "gercek E2E kanit"
// felsefesiyle, ama Node/tarayici tarafinda). Gercek bir sunucu +
// gercek bir tarayici ile 3 gecisi calistirir, HER birinde bir ekran
// goruntusu (.png) + sahne debug JSON'i alir, `reports/ops_suite_scene_<UTC>/`'a yazar.
//
// Hicbir adim fabrike edilmez -- bir adim basarisiz olursa evidence.json'da
// ok:false olarak ISARETLENIR, sessizce atlanmaz/uydurulmaz.

const { chromium } = require('playwright');
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
const PORT = process.env.OPS_SUITE_E2E_PORT || '8422';
const BASE_URL = `http://127.0.0.1:${PORT}`;

function waitForServer(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    function attempt() {
      const req = http.get(url, (res) => { res.resume(); resolve(); });
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

function startServer() {
  const pythonPathEntries = [
    OPS_SUITE_BACKEND_SRC,
    path.join(REPO_ROOT, 'apps', 'orchestrator', 'src'),
    path.join(REPO_ROOT, 'services', 'tr-en-bridge', 'src'),
    path.join(REPO_ROOT, 'services', 'model-gateway', 'src'),
    path.join(REPO_ROOT, 'tools', 'cli-runner', 'src'),
    path.join(REPO_ROOT, 'tools'),
  ];
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ops-suite-scene-evidence-data-'));
  const ownerToken = crypto.randomBytes(24).toString('base64url');
  const identityConfigPath = path.join(dataDir, 'identities.json');
  fs.writeFileSync(identityConfigPath, JSON.stringify({
    schema_version: 1,
    owner: {
      actor_id: 'ops_suite_scene_evidence_owner',
      display_name: "Scene Evidence Owner (yalniz bu kosum icin -- GERCEK bir kisi DEGIL)",
      token_env_var: 'OPS_SUITE_OWNER_TOKEN',
    },
    delegates: [],
  }, null, 2), 'utf-8');

  const env = {
    ...process.env,
    PYTHONPATH: pythonPathEntries.join(path.delimiter),
    OPS_SUITE_PORT: PORT,
    OPS_SUITE_DATA_DIR: dataDir,
    OPS_SUITE_IDENTITY_CONFIG_PATH: identityConfigPath,
    OPS_SUITE_OWNER_TOKEN: ownerToken,
  };

  const proc = spawn(PYTHON_EXE, ['-m', 'ops_suite.server'], { cwd: REPO_ROOT, env, stdio: 'pipe' });
  let serverLog = '';
  proc.stdout.on('data', (c) => { serverLog += c.toString(); });
  proc.stderr.on('data', (c) => { serverLog += c.toString(); });
  return { proc, dataDir, ownerToken, getLog: () => serverLog };
}

async function sceneDebug(page) {
  return page.evaluate(function () {
    return window.__ops_suite_scene_debug__ ? window.__ops_suite_scene_debug__() : null;
  });
}

async function main() {
  const ts = new Date().toISOString().replace(/[:.]/g, '').replace('T', 'T').slice(0, 15) + 'Z';
  const outDir = path.join(REPO_ROOT, 'reports', `ops_suite_scene_${ts}`);
  fs.mkdirSync(outDir, { recursive: true });

  const evidence = { generated_at: new Date().toISOString(), base_url: BASE_URL, steps: [], overall_ok: true };
  function record(step, ok, extra) {
    evidence.steps.push(Object.assign({ step, ok }, extra || {}));
    if (!ok) {
      evidence.overall_ok = false;
    }
  }

  const server = startServer();
  let browser;
  try {
    let serverUp = true;
    try {
      await waitForServer(BASE_URL + '/api/agents', 15000);
    } catch (err) {
      serverUp = false;
      record('server_startup', false, { error: err.message, server_log_tail: server.getLog().slice(-3000) });
    }
    if (!serverUp) {
      throw new Error('sunucu ayaga kalkmadi');
    }
    record('server_startup', true, {});

    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
    await page.goto(BASE_URL + '/');
    await page.waitForSelector('.agent-card', { timeout: 10000 });

    // --- Gecis 1: baslangic durumu -------------------------------------
    const s1 = await sceneDebug(page);
    const shot1 = path.join(outDir, '01_initial_state.png');
    await page.screenshot({ path: shot1, fullPage: true });
    record('transition_1_initial_state', s1 !== null && s1.agents.orchestrator.state === 'offline', {
      debug_state: s1, screenshot: path.relative(REPO_ROOT, shot1),
    });

    // --- Gecis 2: echo komutu -> asistan speaking + orchestrator idle --
    await page.fill('#voice-input', "Ezo, echo ile 'merhaba' yaz");
    await page.click('#voice-form button[type="submit"]');
    await page.waitForFunction(function () {
      return document.getElementById('assistant-state').textContent === 'speaking';
    }, { timeout: 10000 });
    await page.waitForTimeout(300); // requestAnimationFrame donguculerinin en az bir kez calismasi icin
    const s2 = await sceneDebug(page);
    const shot2 = path.join(outDir, '02_after_echo_command.png');
    await page.screenshot({ path: shot2, fullPage: true });
    record(
      'transition_2_echo_command',
      s2 !== null && s2.assistant_state === 'speaking' && s2.agents.orchestrator.state === 'idle',
      { debug_state: s2, screenshot: path.relative(REPO_ROOT, shot2) },
    );

    // --- Gecis 3: irreversible komut -> onay rozeti 1, owner onayi -> 0
    await page.fill('#voice-input', 'Ezo, tüm dosyaları sil');
    await page.click('#voice-form button[type="submit"]');
    await page.waitForSelector('.approval-item', { timeout: 10000 });
    await page.waitForTimeout(300);
    const s3a = await sceneDebug(page);
    const shot3a = path.join(outDir, '03a_pending_approval.png');
    await page.screenshot({ path: shot3a, fullPage: true });
    record('transition_3a_pending_approval', s3a !== null && s3a.pending_approval_count === 1, {
      debug_state: s3a, screenshot: path.relative(REPO_ROOT, shot3a),
    });

    await page.evaluate(function (token) {
      window.localStorage.setItem('ops_suite_access_token', token);
    }, server.ownerToken);
    await page.reload();
    await page.waitForSelector('.whoami--ok', { timeout: 10000 });
    await page.click('.approval-item .approve');
    await page.waitForFunction(function () {
      return document.querySelectorAll('.approval-item').length === 0;
    }, { timeout: 10000 });
    await page.waitForTimeout(300);
    const s3b = await sceneDebug(page);
    const shot3b = path.join(outDir, '03b_after_owner_approval.png');
    await page.screenshot({ path: shot3b, fullPage: true });
    record('transition_3b_owner_approved', s3b !== null && s3b.pending_approval_count === 0, {
      debug_state: s3b, screenshot: path.relative(REPO_ROOT, shot3b),
    });
  } catch (err) {
    record('unexpected_error', false, { error: String(err && err.stack ? err.stack : err) });
  } finally {
    if (browser) {
      await browser.close();
    }
    server.proc.kill();
    try {
      fs.rmSync(server.dataDir, { recursive: true, force: true });
    } catch (e) {
      // yoksay -- kanit sonucunu etkilemez
    }
  }

  fs.writeFileSync(path.join(outDir, 'evidence.json'), JSON.stringify(evidence, null, 2), 'utf-8');

  const mdLines = [
    '# Ops Suite Ofis Sahnesi -- Gerçek Kanıt (B038, PLAN.md T36)',
    '',
    `Üretildi (UTC): ${evidence.generated_at}`,
    `base_url: ${evidence.base_url}`,
    `Genel sonuç: **${evidence.overall_ok ? 'PASS' : 'FAIL'}**`,
    '',
  ];
  evidence.steps.forEach(function (step) {
    mdLines.push(`## ${step.step} -- ${step.ok ? 'OK' : 'FAIL'}`);
    mdLines.push('');
    if (step.screenshot) {
      mdLines.push(`![${step.step}](${path.basename(step.screenshot)})`);
      mdLines.push('');
    }
    if (step.debug_state) {
      mdLines.push('```json');
      mdLines.push(JSON.stringify(step.debug_state, null, 2));
      mdLines.push('```');
      mdLines.push('');
    }
    if (step.error) {
      mdLines.push('```');
      mdLines.push(step.error);
      mdLines.push('```');
      mdLines.push('');
    }
  });
  fs.writeFileSync(path.join(outDir, 'evidence.md'), mdLines.join('\n'), 'utf-8');

  evidence.steps.forEach(function (step) {
    console.log(`[${step.ok ? 'OK' : 'FAIL'}] ${step.step}`);
  });
  console.log(`genel_sonuc=${evidence.overall_ok ? 'PASS' : 'FAIL'}`);
  console.log(`evidence_dir=${outDir}`);

  process.exit(evidence.overall_ok ? 0 : 2);
}

main();
