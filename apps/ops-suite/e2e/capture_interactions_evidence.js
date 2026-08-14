#!/usr/bin/env node
// Ops Suite -- sprite varliklari (B047) + tiklama etkilesimleri (B049)
// icin GERCEK kanit yakalama scripti (PLAN.md T40/T42). Gercek bir
// sunucu + gercek bir tarayici ile calisir, HER adimda bir ekran
// goruntusu (.png) + ilgili JSON durumunu alir,
// `reports/ops_suite_interactions_<UTC>/`'a yazar. Hicbir adim fabrike
// edilmez -- basarisiz bir adim ok:false olarak isaretlenir.

const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const { startTestServer } = require('./test-server');

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');

async function sceneDebug(page) {
  return page.evaluate(function () {
    return window.__ops_suite_scene_debug__ ? window.__ops_suite_scene_debug__() : null;
  });
}

async function clickSceneEntity(page, canvasX, canvasY) {
  const box = await page.evaluate(function (coords) {
    var canvas = document.getElementById('office-scene');
    var rect = canvas.getBoundingClientRect();
    var scaleX = rect.width / canvas.width;
    var scaleY = rect.height / canvas.height;
    return { clientX: rect.left + coords.x * scaleX, clientY: rect.top + coords.y * scaleY };
  }, { x: canvasX, y: canvasY });
  await page.mouse.click(box.clientX, box.clientY);
}

async function main() {
  const ts = new Date().toISOString().replace(/[:.]/g, '').slice(0, 15) + 'Z';
  const outDir = path.join(REPO_ROOT, 'reports', `ops_suite_interactions_${ts}`);
  fs.mkdirSync(outDir, { recursive: true });

  const evidence = { generated_at: new Date().toISOString(), steps: [], overall_ok: true };
  function record(step, ok, extra) {
    evidence.steps.push(Object.assign({ step, ok }, extra || {}));
    if (!ok) {
      evidence.overall_ok = false;
    }
  }

  let server;
  let browser;
  try {
    server = await startTestServer(8424);
    record('server_startup', true, { base_url: server.baseUrl });

    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 900, height: 800 } });
    await page.goto(server.baseUrl + '/');
    await page.waitForSelector('.agent-card', { timeout: 10000 });

    // --- B047: sprite varliklari GERCEKTEN yuklendi mi -------------------
    let spritesLoaded = false;
    for (let i = 0; i < 50 && !spritesLoaded; i++) {
      const debugState = await sceneDebug(page);
      const statuses = Object.values(debugState.sprites || {});
      spritesLoaded = statuses.length === 5 && statuses.every((s) => s === 'loaded');
      if (!spritesLoaded) {
        await page.waitForTimeout(100);
      }
    }
    const shot1 = path.join(outDir, '01_sprites_loaded.png');
    await page.screenshot({ path: shot1, fullPage: true });
    record('b047_sprites_loaded', spritesLoaded, {
      debug_state: await sceneDebug(page), screenshot: path.relative(REPO_ROOT, shot1),
    });

    // --- B049: bilinen-canli bir ajana tiklamak detay panelini acar ------
    const debugAfterLoad = await sceneDebug(page);
    await clickSceneEntity(page, debugAfterLoad.agents.orchestrator.x, debugAfterLoad.agents.orchestrator.y);
    await page.waitForSelector('#agent-detail-panel:not([hidden])', { timeout: 5000 });
    const shot2 = path.join(outDir, '02_agent_detail_panel.png');
    await page.screenshot({ path: shot2, fullPage: true });
    const panelName = await page.locator('#agent-detail-name').textContent();
    record('b049_agent_click_opens_panel', panelName.includes('orchestrator'), {
      panel_name: panelName, screenshot: path.relative(REPO_ROOT, shot2),
    });
    await page.locator('#agent-detail-close').click();

    // --- B049: hayalet raftaki bir ajana tiklamak da GERCEK detay gosterir
    await clickSceneEntity(page, debugAfterLoad.agents.finance_agent.x, debugAfterLoad.agents.finance_agent.y);
    await page.waitForSelector('#agent-detail-panel:not([hidden])', { timeout: 5000 });
    const shot3 = path.join(outDir, '03_ghost_agent_detail_panel.png');
    await page.screenshot({ path: shot3, fullPage: true });
    const ghostDetail = await page.locator('#agent-detail-detail').textContent();
    record('b049_ghost_agent_click_shows_honest_detail', ghostDetail.includes('not_implemented'), {
      detail_text: ghostDetail, screenshot: path.relative(REPO_ROOT, shot3),
    });
    await page.locator('#agent-detail-close').click();

    // --- B049: bekleyen bir onayla eslesen ajan -> onay baglantisi -------
    await page.fill('#voice-input', 'Ezo, tüm dosyaları sil');
    await page.click('#voice-form button[type="submit"]');
    await page.waitForSelector('.approval-item', { timeout: 10000 });
    const requestId = await page.locator('.approval-item').getAttribute('data-request-id');

    const debugAfterCommand = await sceneDebug(page);
    await clickSceneEntity(page, debugAfterCommand.agents.orchestrator.x, debugAfterCommand.agents.orchestrator.y);
    await page.waitForSelector('#agent-detail-approval-link:not([hidden])', { timeout: 5000 });
    const shot4 = path.join(outDir, '04_approval_link_visible.png');
    await page.screenshot({ path: shot4, fullPage: true });
    const taskField = await page.locator('#agent-detail-task').textContent();
    record('b049_pending_approval_link_shown', taskField === requestId, {
      request_id: requestId, task_field: taskField, screenshot: path.relative(REPO_ROOT, shot4),
    });

    await page.locator('#agent-detail-approval-link').click();
    const highlighted = await page.locator('.approval-item').evaluate((el) => el.classList.contains('approval-item--highlighted'));
    const shot5 = path.join(outDir, '05_approval_item_highlighted.png');
    await page.screenshot({ path: shot5, fullPage: true });
    record('b049_approval_link_click_highlights_item', highlighted, { screenshot: path.relative(REPO_ROOT, shot5) });
  } catch (err) {
    record('unexpected_error', false, { error: String(err && err.stack ? err.stack : err) });
  } finally {
    if (browser) {
      await browser.close();
    }
    if (server) {
      await server.stop();
    }
  }

  fs.writeFileSync(path.join(outDir, 'evidence.json'), JSON.stringify(evidence, null, 2), 'utf-8');

  const mdLines = [
    '# Ops Suite Sprite + Tiklama Etkilesimleri -- Gercek Kanit (B047/B049, PLAN.md T40/T42)',
    '',
    `Uretildi (UTC): ${evidence.generated_at}`,
    `Genel sonuc: **${evidence.overall_ok ? 'PASS' : 'FAIL'}**`,
    '',
  ];
  evidence.steps.forEach(function (step) {
    mdLines.push(`## ${step.step} -- ${step.ok ? 'OK' : 'FAIL'}`);
    mdLines.push('');
    if (step.screenshot) {
      mdLines.push(`![${step.step}](${path.basename(step.screenshot)})`);
      mdLines.push('');
    }
    const detail = Object.assign({}, step);
    delete detail.step;
    delete detail.ok;
    delete detail.screenshot;
    mdLines.push('```json');
    mdLines.push(JSON.stringify(detail, null, 2));
    mdLines.push('```');
    mdLines.push('');
  });
  fs.writeFileSync(path.join(outDir, 'evidence.md'), mdLines.join('\n'), 'utf-8');

  evidence.steps.forEach((step) => console.log(`[${step.ok ? 'OK' : 'FAIL'}] ${step.step}`));
  console.log(`genel_sonuc=${evidence.overall_ok ? 'PASS' : 'FAIL'}`);
  console.log(`evidence_dir=${outDir}`);
  process.exit(evidence.overall_ok ? 0 : 2);
}

main();
