// Ops Suite -- coklu-adimli gorev yasam dongusu animasyonu GERCEK
// tarayici testleri (BACKLOG.md B048, PLAN.md T45, DECISIONS.md
// ADR-023). Hicbir asama fabrike EDILMEZ -- her gecis, Playwright'in
// native WebSocket frame API'siyle (T38 ile AYNI desen: sleep/polling
// YOK) yakalanan GERCEK, ayri `task.lifecycle`/`agent.presence` WS
// mesajlarina dayanir.
//
// **Neden state-polling DEGIL, frame-yakalama:** gorev isaretcisinin
// `stage` alani TEK bir mutasyona ugrayan nesnede tutulur (bkz.
// scene.js::taskMarkers) -- bir onceki asama (ornegin "assigned")
// bir sonraki WS mesaji (working) geldigi anda UZERINE YAZILIR. T38'in
// "working anı" sorunuyla AYNI sinif: `expect.poll` ile bu state'i
// yoklamak, iki poll araligi arasinda GERCEKTEN gecen bir asamayi
// KACIRABILIR (yanlis-negatif DEGIL ama yanlis-GUVEN verir). Bu yuzden
// "en az 2 coklu-adim gecisi" kaniti, sahnenin TUKETTIGI degil,
// sunucunun GERCEKTEN yayinladigi ayri WS frame'lerinden gelir.

const { test, expect } = require('@playwright/test');
const { startTestServer } = require('../test-server');

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

// Sayfaya gidip WS baglantisini acar, iki GERCEK konuyu (task.lifecycle,
// agent.presence) ayri dizilerde toplayan bir dinleyici kurar.
async function gotoAndCaptureLifecycle(page) {
  const taskLifecycleFrames = [];
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
        return;
      }
      if (data.topic === 'task.lifecycle') {
        taskLifecycleFrames.push(data.payload);
      } else if (data.topic === 'agent.presence' && data.payload.agent_id === 'orchestrator') {
        agentPresenceFrames.push(data.payload);
      }
    });
  });
  await page.goto('/');
  await expect(page.locator('#ws-status')).toHaveClass(/ws-status--open/, { timeout: 10000 });
  return { taskLifecycleFrames, agentPresenceFrames };
}

test('yol 1 (basarili): echo komutu -- gorev isaretcisi GERCEK WS olaylariyla kuyruk->atandi->calisiyor->tamamlandi asamalarindan GECER', async ({ page }) => {
  const { taskLifecycleFrames, agentPresenceFrames } = await gotoAndCaptureLifecycle(page);

  await page.locator('#voice-input').fill("Ezo, echo ile 'merhaba' yaz");
  await page.locator('#voice-form button[type="submit"]').click();

  // 4 GERCEK task.lifecycle asamasi: received/translating/risk_checked/completed.
  await expect.poll(() => taskLifecycleFrames.length, { timeout: 10000 }).toBe(4);
  expect(taskLifecycleFrames.map((p) => p.state)).toEqual(['received', 'translating', 'risk_checked', 'completed']);
  // Yalnizca NIHAI olay agent_id tasir -- bu, isaretcinin "atandi"
  // asamasina GECEBILMESI icin GERCEKTEN gereken sinyaldir.
  expect(taskLifecycleFrames[0].agent_id).toBeFalsy();
  expect(taskLifecycleFrames[3].agent_id).toBe('orchestrator');

  // 2 GERCEK agent.presence olayi: working sonra idle (T37/T38 ile AYNI kanal).
  await expect.poll(() => agentPresenceFrames.length, { timeout: 10000 }).toBe(2);
  expect(agentPresenceFrames.map((p) => p.state)).toEqual(['working', 'idle']);

  const requestId = taskLifecycleFrames[0].request_id;
  expect(requestId).toBeTruthy();

  // Sahne artik TUM bu ayri olaylari tuketmis olmali -- nihai GORSEL
  // asama "tamamlandi" (working->idle cevrimi bitti), enterpolasyon
  // hedefine ULASMIS (at_rest_position).
  await expect.poll(async () => {
    const debugState = await getSceneDebug(page);
    const marker = debugState.task_markers[requestId];
    return marker ? marker.stage : null;
  }, { timeout: 5000 }).toBe('completed');
  await expect.poll(async () => {
    const debugState = await getSceneDebug(page);
    return debugState.task_markers[requestId].at_rest_position;
  }, { timeout: 5000 }).toBe(true);

  const finalState = await getSceneDebug(page);
  expect(finalState.task_markers[requestId].agent_id).toBe('orchestrator');
  expect(finalState.task_markers[requestId].lifecycle_state).toBe('completed');
});

test('yol 2 (onay bekliyor): irreversible komut -- gorev GERCEKTEN cozulmemis olsa da isaretci AYNI "tamamlandi" gorsel asamasina ulasir (ADR-023 durustluk notu)', async ({ page }) => {
  const { taskLifecycleFrames, agentPresenceFrames } = await gotoAndCaptureLifecycle(page);

  await page.locator('#voice-input').fill("Ezo, tüm dosyaları sil");
  await page.locator('#voice-form button[type="submit"]').click();

  await expect.poll(() => taskLifecycleFrames.length, { timeout: 10000 }).toBe(4);
  // Bu yolda nihai durum "completed" DEGIL, "awaiting_approval" --
  // gorev GERCEKTEN cozulmedi (onay kuyrugunda bekliyor).
  expect(taskLifecycleFrames[3].state).toBe('awaiting_approval');
  expect(taskLifecycleFrames[3].agent_id).toBe('orchestrator');

  // working->idle cevrimi voice_bridge.py'de KOSULSUZDUR -- bu yolda da
  // GERCEKTEN ayni sekilde yayinlanir (bkz. ADR-023).
  await expect.poll(() => agentPresenceFrames.length, { timeout: 10000 }).toBe(2);
  expect(agentPresenceFrames.map((p) => p.state)).toEqual(['working', 'idle']);

  const requestId = taskLifecycleFrames[0].request_id;

  // ADR-023'un durustluk notu: "tamamlandi" GORSEL asamasi, GOREVIN
  // BASARILI oldugu anlamina GELMEZ -- yalnizca ajanin bu gorev icin
  // islemeyi bitirdigi anlamina gelir. Burada lifecycle_state hala
  // "awaiting_approval" iken GORSEL stage "completed" olmalidir --
  // bu ikisinin KASITLI olarak AYRI alanlar olmasinin nedenidir.
  await expect.poll(async () => {
    const debugState = await getSceneDebug(page);
    const marker = debugState.task_markers[requestId];
    return marker ? marker.stage : null;
  }, { timeout: 5000 }).toBe('completed');

  const finalState = await getSceneDebug(page);
  expect(finalState.task_markers[requestId].lifecycle_state).toBe('awaiting_approval');
  expect(finalState.task_markers[requestId].stage).toBe('completed');
});
