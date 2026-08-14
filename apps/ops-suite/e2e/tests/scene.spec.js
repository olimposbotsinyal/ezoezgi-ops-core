// Ops Suite -- animasyonlu ofis sahnesi GERCEK tarayici testleri
// (BACKLOG.md B038, PLAN.md T36/T38). Canvas pikselleri Playwright'in
// DOM sorgulariyla goruLEMEZ (bkz. docs/DECISIONS.md ADR-020) -- bu
// yuzden `window.__ops_suite_scene_debug__()` koprusu kullanilir (bkz.
// apps/ops-suite/frontend/js/scene.js::debugState).
//
// **Deterministik olarak test EDILEBILEN gecisler:**
//   1) Baslangic: bilinen-canli ajanlar "offline" (henuz heartbeat yok).
//   2) Echo komutundan SONRA: orchestrator "offline" -> "idle" VE
//      asistan "idle" -> "speaking".
//   3) Irreversible komut + owner onayi: onay tepsisi rozeti 0 -> 1 -> 0.
//   4) (T38, BACKLOG.md B046) `working` -> `idle` gecisinin KENDISI --
//      PLAN.md T36'nin eski notu bunun "cok kisa omurlu, GOZLEMLENEMEZ"
//      oldugunu soyluyordu; T37 (agent.presence WS yayini) + bu test
//      (gercek WS FRAME'lerini dinleyerek, `waitForTimeout`/sleep
//      TAHMINI OLMADAN) bunu tersine cevirdi -- bkz. asagidaki test.

const { test, expect } = require('@playwright/test');
const { startTestServer } = require('../test-server');

// T44 -- bu dosya KENDI izole sunucusunu yonetir (bkz. test-server.js
// dokustringi) -- bu dosyanin testleri PRISTINE bir baslangic durumu
// (orchestrator=offline, assistant=idle, pending_approval_count=0)
// VARSAYAR; paylasilan bir sunucu kullansaydi diger dosyalarin (ozellikle
// sesli komut gonderen `interactions.spec.js`) yan etkileri bu varsayimi
// BOZARDI -- gercek bir kosuda GERCEKTEN yasandi.
let server;
test.beforeAll(async () => {
  server = await startTestServer();
});
test.afterAll(async () => {
  if (server) {
    await server.stop();
  }
});

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
  const ownerToken = server.ownerToken;
  test.skip(!ownerToken, 'Bu dosyanin sunucusu owner token uretemedi -- owner-onay gecisi SKIPPED (fabrike edilmedi)');

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

test('gecis 4 (T38): working->idle GERCEK WS frame yakalamasiyla deterministik dogrulanir (sleep/polling YOK)', async ({ page }) => {
  // Playwright'in native WebSocket frame API'si -- bir sleep/polling
  // DEGIL, tarayicinin GERCEKTEN aldigi her WS frame'i olay-tabanli
  // olarak yakalar. Bu, "working" gibi kisa omurlu bir durumun render
  // zamanlamasina BAGIMLI OLMADAN, tam olarak NE YAYINLANDIGINI kanitlar.
  // BULUNAN GERCEK HATA (T38): `#assistant-state === 'speaking'` DOM
  // beklentisi guvenilmez bir senkronizasyon sinyaliydi -- sunucu
  // surecinin `AssistantPresenceTracker`/`HeartbeatTracker` singleton'lari
  // TUM test dosyasi boyunca PAYLASILIYOR; onceki bir test ZATEN
  // "speaking" durumunu birakmissa, bu beklenti sayfa yuklenir
  // yuklenmez, bu testin KENDI komutu hic gonderilmeden DOGRU
  // olabiliyordu -- assertion ERKEN geciyor, frame'ler henuz
  // gelmeden kontrol calisiyordu (gercek bir kosuda GERCEKTEN
  // gozlemlendi). **Duzeltme:** bu testin SENKRONIZASYON sinyali
  // artik dogrudan yakalanan WS frame DIZISININ KENDISI -- harici bir
  // DOM degerine degil, `expect.poll()` (sleep DEGIL, sinirli/bounded
  // bir polling yardimcisi) ile "2 agent.presence frame'i geldi mi"
  // sorusuna bagli.
  const agentPresenceFrames = [];
  page.on('websocket', (ws) => {
    if (!ws.url().includes('/ws/live')) {
      return;
    }
    ws.on('framereceived', (frame) => {
      let data;
      try {
        data = JSON.parse(frame.payload);
      } catch (err) {
        return; // JSON-disi/beklenmeyen frame -- sessizce yoksay
      }
      if (data.topic === 'agent.presence' && data.payload.agent_id === 'orchestrator') {
        agentPresenceFrames.push(data.payload);
      }
    });
  });

  await page.goto('/');
  await expect(page.locator('#ws-status')).toHaveClass(/ws-status--open/, { timeout: 10000 });

  await page.locator('#voice-input').fill("Ezo, echo ile 'merhaba' yaz");
  await page.locator('#voice-form button[type="submit"]').click();

  await expect.poll(() => agentPresenceFrames.length, { timeout: 10000 }).toBe(2);

  const states = agentPresenceFrames.map((p) => p.state);
  expect(states).toEqual(['working', 'idle']);

  // Sahne, bu TEK WS mesajlarini DOGRUDAN tuketmis olmali (T38) --
  // debug koprusu artik "idle" (son durum) gosteriyor VE dinlenme
  // bolgesinde konumlanmis. Scene.js'in kendi guncellemesi WS frame
  // teslimatindan HEMEN SONRA ama ayri bir JS event-loop turunda
  // calisabildigi icin, burada da sabit bir DEGER yerine `poll` kullanilir.
  await expect.poll(async () => (await getSceneDebug(page)).agents.orchestrator.state, { timeout: 5000 }).toBe('idle');
  const debugState = await getSceneDebug(page);
  expect(debugState.agents.orchestrator.zone).toBe('rest');
});
