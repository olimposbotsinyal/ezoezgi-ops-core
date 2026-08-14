#!/usr/bin/env node
// Ops Suite -- coklu-adimli gorev animasyonu (B048) + ses ipuclari
// (B050) icin GERCEK kanit yakalama scripti (PLAN.md T45/T46). Gercek
// bir sunucu + gercek bir tarayici ile calisir, HER adimda bir ekran
// goruntusu (.png) + ilgili JSON durumunu alir,
// `reports/ops_suite_av_<UTC>/`'a yazar. Hicbir adim fabrike edilmez --
// basarisiz bir adim ok:false olarak isaretlenir.

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

async function soundDebug(page) {
  return page.evaluate(function () {
    return window.__ops_suite_sound_debug__ ? window.__ops_suite_sound_debug__() : null;
  });
}

async function submitVoiceCommand(page, text) {
  await page.locator('#voice-input').fill(text);
  await page.locator('#voice-form button[type="submit"]').click();
}

async function main() {
  const ts = new Date().toISOString().replace(/[:.]/g, '').slice(0, 15) + 'Z';
  const outDir = path.join(REPO_ROOT, 'reports', `ops_suite_av_${ts}`);
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
    server = await startTestServer(8425);
    record('server_startup', true, { base_url: server.baseUrl });

    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 900, height: 800 } });
    await page.goto(server.baseUrl + '/');
    await page.waitForSelector('.agent-card', { timeout: 10000 });

    // --- B048 yol 1: basarili gorev -- kuyruk->atandi->calisiyor->tamamlandi ---
    await submitVoiceCommand(page, "Ezo, echo ile 'merhaba' yaz");
    await page.waitForFunction(function () {
      var s = window.__ops_suite_scene_debug__();
      var markers = Object.values(s.task_markers || {});
      return markers.length > 0 && markers[markers.length - 1].stage === 'completed';
    }, { timeout: 10000 });
    const shot1 = path.join(outDir, '01_task_marker_completed.png');
    await page.screenshot({ path: shot1, fullPage: true });
    const debug1 = await sceneDebug(page);
    const marker1 = Object.values(debug1.task_markers)[0];
    record('b048_completed_path', marker1.stage === 'completed' && marker1.lifecycle_state === 'completed', {
      task_marker: marker1, screenshot: path.relative(REPO_ROOT, shot1),
    });

    // --- B048 yol 2: onay-bekleyen gorev -- ayni "tamamlandi" GORSEL asamasi (ADR-023) ---
    await submitVoiceCommand(page, "Ezo, tüm dosyaları sil");
    await page.waitForSelector('.approval-item', { timeout: 10000 });
    await page.waitForFunction(function () {
      var s = window.__ops_suite_scene_debug__();
      var markers = Object.values(s.task_markers || {});
      var newest = markers[markers.length - 1];
      return newest && newest.stage === 'completed' && newest.lifecycle_state === 'awaiting_approval';
    }, { timeout: 10000 });
    const shot2 = path.join(outDir, '02_task_marker_awaiting_approval.png');
    await page.screenshot({ path: shot2, fullPage: true });
    const debug2 = await sceneDebug(page);
    const markers2 = Object.values(debug2.task_markers);
    const marker2 = markers2[markers2.length - 1];
    record('b048_awaiting_approval_path_same_visual_stage', marker2.stage === 'completed' && marker2.lifecycle_state === 'awaiting_approval', {
      task_marker: marker2, screenshot: path.relative(REPO_ROOT, shot2),
    });

    // --- B050: mute ACIKKEN gercek bir cue BASTIRILIR ---
    await page.locator('#sound-mute-toggle').click();
    await submitVoiceCommand(page, "Ezo, echo ile 'tekrar' yaz");
    await page.waitForFunction(function () {
      var s = window.__ops_suite_sound_debug__();
      return s.last_play && s.last_play.cue === 'task_complete';
    }, { timeout: 10000 });
    const shot3 = path.join(outDir, '03_sound_muted_suppressed.png');
    await page.screenshot({ path: shot3, fullPage: true });
    const sound3 = await soundDebug(page);
    record('b050_mute_suppresses_real_cue', sound3.muted === true && sound3.last_play.played === false && sound3.last_play.reason === 'muted', {
      sound_state: sound3, screenshot: path.relative(REPO_ROOT, shot3),
    });

    // --- B050: mute KAPATILINCA ayni tetik GERCEKTEN calinir ---
    await page.locator('#sound-mute-toggle').click();
    await submitVoiceCommand(page, "Ezo, echo ile 'tekrar iki' yaz");
    await page.waitForFunction(function () {
      var s = window.__ops_suite_sound_debug__();
      return s.last_play && s.last_play.cue === 'task_complete' && s.last_play.played === true;
    }, { timeout: 10000 });
    const shot4 = path.join(outDir, '04_sound_unmuted_played.png');
    await page.screenshot({ path: shot4, fullPage: true });
    const sound4 = await soundDebug(page);
    record('b050_unmuted_real_cue_plays', sound4.muted === false && sound4.last_play.played === true, {
      sound_state: sound4, screenshot: path.relative(REPO_ROOT, shot4),
    });

    // --- B050: onay-gerekli tetikleyicisi zaten adim 2'de kuyruga girdi;
    // burada policy_block tetikleyicisi -- token OLMADAN onaylama denemesi ---
    page.once('dialog', function (dialog) { dialog.accept(); });
    await page.locator('.approval-item').last().locator('.approve').click();
    await page.waitForFunction(function () {
      var s = window.__ops_suite_sound_debug__();
      return s.last_play && s.last_play.cue === 'policy_block';
    }, { timeout: 10000 });
    const shot5 = path.join(outDir, '05_sound_policy_block.png');
    await page.screenshot({ path: shot5, fullPage: true });
    const sound5 = await soundDebug(page);
    record('b050_policy_block_real_401_triggers_cue', sound5.last_play.cue === 'policy_block' && sound5.last_play.played === true, {
      sound_state: sound5, screenshot: path.relative(REPO_ROOT, shot5),
    });

    // --- B050: onay-gerekli tetikleyicisinin kendisi (adim 2'nin approval.queue yayini) ---
    // Bu, ayri bir cue oldugu icin kendi kaydini hak ediyor -- adim 2
    // sirasinda GERCEKTEN calindi (approval_needed), simdi geriye donuk
    // dogrulanamaz (last_play adim 5'te policy_block'a EZILDI) -- bu
    // yuzden AYRI, taze bir irreversible komutla YENIDEN tetiklenir.
    await submitVoiceCommand(page, "Ezo, tüm dosyaları sil");
    await page.waitForFunction(function () {
      var s = window.__ops_suite_sound_debug__();
      return s.last_play && s.last_play.cue === 'approval_needed';
    }, { timeout: 10000 });
    const shot6 = path.join(outDir, '06_sound_approval_needed.png');
    await page.screenshot({ path: shot6, fullPage: true });
    const sound6 = await soundDebug(page);
    record('b050_approval_needed_real_trigger', sound6.last_play.cue === 'approval_needed' && sound6.last_play.played === true, {
      sound_state: sound6, screenshot: path.relative(REPO_ROOT, shot6),
    });
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
    '# Ops Suite Coklu-Adimli Animasyon + Ses Ipuclari -- Gercek Kanit (B048/B050, PLAN.md T45/T46)',
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
