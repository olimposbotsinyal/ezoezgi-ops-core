// Ops Suite -- animasyonlu ofis sahnesi GERCEK tarayici testleri
// (BACKLOG.md B038, PLAN.md T36). Canvas pikselleri Playwright'in DOM
// sorgulariyla goruLEMEZ (bkz. docs/DECISIONS.md ADR-020) -- bu yuzden
// `window.__ops_suite_scene_debug__()` koprusu kullanilir (bkz.
// apps/ops-suite/frontend/js/scene.js::debugState).
//
// **Deterministik olarak test EDILEBILEN 3 gecis** (bkz. PLAN.md T36
// notu -- backend senkron oldugu icin "working" ara-durumu cok kisa
// omurlu/gozlemlenemez, bkz. asagidaki "Bilinen sinirlama"):
//   1) Baslangic: bilinen-canli ajanlar "offline" (henuz heartbeat yok).
//   2) Echo komutundan SONRA: orchestrator "offline" -> "idle" VE
//      asistan "idle" -> "speaking".
//   3) Irreversible komut + owner onayi: onay tepsisi rozeti 0 -> 1 -> 0.

const { test, expect } = require('@playwright/test');

function getSceneDebug(page) {
  return page.evaluate(function () {
    return window.__ops_suite_scene_debug__ ? window.__ops_suite_scene_debug__() : null;
  });
}

test('gecis 1: baslangicta bilinen-canli ajanlar offline, hayalet raftaki not_implemented ajanlar ayirt edilebilir', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.agent-card')).toHaveCount(9, { timeout: 10000 });

  const debugState = await getSceneDebug(page);
  expect(debugState).not.toBeNull();
  expect(debugState.agents.orchestrator.state).toBe('offline');
  expect(debugState.agents.orchestrator.zone).toBe('rest');
  expect(debugState.agents.finance_agent.zone).toBe('ghost');
  expect(debugState.pending_approval_count).toBe(0);
});

test('gecis 2: echo komutu sonrasi orchestrator offline->idle VE asistan idle->speaking', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#ws-status')).toHaveClass(/ws-status--open/, { timeout: 10000 });

  var before = await getSceneDebug(page);
  expect(before.assistant_state).toBe('idle');

  await page.locator('#voice-input').fill("Ezo, echo ile 'merhaba' yaz");
  await page.locator('#voice-form button[type="submit"]').click();

  await expect(page.locator('#assistant-state')).toHaveText('speaking', { timeout: 10000 });

  // Sahne render dongusu asenkron (requestAnimationFrame) -- debug
  // durumunun DOM ile senkronlasmasi icin kisa bir polling penceresi.
  await expect
    .poll(async () => (await getSceneDebug(page)).assistant_state, { timeout: 5000 })
    .toBe('speaking');
  await expect
    .poll(async () => (await getSceneDebug(page)).agents.orchestrator.state, { timeout: 5000 })
    .toBe('idle');

  const after = await getSceneDebug(page);
  expect(after.agents.orchestrator.zone).toBe('rest');
});

test('gecis 3: irreversible komut + owner onayi -- onay tepsisi rozeti 0 -> 1 -> 0', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#ws-status')).toHaveClass(/ws-status--open/, { timeout: 10000 });

  var initial = await getSceneDebug(page);
  expect(initial.pending_approval_count).toBe(0);

  await page.locator('#voice-input').fill("Ezo, tüm dosyaları sil");
  await page.locator('#voice-form button[type="submit"]').click();

  await expect(page.locator('.approval-item')).toHaveCount(1, { timeout: 10000 });
  await expect
    .poll(async () => (await getSceneDebug(page)).pending_approval_count, { timeout: 5000 })
    .toBe(1);

  // B044 -- onaylama GERCEK bir Bearer token gerektirir; sahne testi
  // kendi token'ini localStorage'a yazip whoami'yi tetikler (T35'in
  // debug koprusu ile AYNI ruhta -- gercek auth akisini BYPASS ETMEZ,
  // yalnizca UI'nin token akisini gercek bir tarayicidan kullanir).
  const ownerToken = process.env.OPS_SUITE_E2E_OWNER_TOKEN;
  test.skip(!ownerToken, 'OPS_SUITE_E2E_OWNER_TOKEN set edilmemis -- owner-onay gecisi SKIPPED (fabrike edilmedi)');

  await page.evaluate(function (token) {
    window.localStorage.setItem('ops_suite_access_token', token);
  }, ownerToken);
  await page.reload();
  await expect(page.locator('#whoami')).toHaveClass(/whoami--ok/, { timeout: 10000 });

  await page.locator('.approval-item .approve').click();
  await expect(page.locator('.approval-item')).toHaveCount(0, { timeout: 10000 });
  await expect
    .poll(async () => (await getSceneDebug(page)).pending_approval_count, { timeout: 5000 })
    .toBe(0);
});
