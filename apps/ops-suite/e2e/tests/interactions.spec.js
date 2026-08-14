// Ops Suite -- sprite varlik hatti (B047, PLAN.md T40) + sahne tiklama
// etkilesimleri (B049, PLAN.md T42) GERCEK tarayici testleri. Hicbir
// gorsel/etkilesim fabrike edilmez -- her assertion ya gercek bir DOM
// durumunu ya da `window.__ops_suite_scene_debug__()`'in gercek
// alanlarini kontrol eder.

const { test, expect } = require('@playwright/test');
const { startTestServer } = require('../test-server');

// T44 -- bu dosya KENDI izole sunucusunu yonetir (bkz. test-server.js
// dokustringi) -- bu dosyanin testleri GERCEK sesli komutlar gonderdigi
// (heartbeat/onay durumunu degistiren) icin, PAYLASILAN bir sunucu
// kullansaydi `scene.spec.js`'in pristine-baslangic varsayimlarini
// BOZARDI -- gercek bir kosuda GERCEKTEN yasandi (4 test basarisiz oldu).
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

// B049 -- sahnedeki bir varligin (agent.x/y, canvas-ici koordinat) GERCEK
// bir fare tiklamasiyla tiklanabilmesi icin, canvas'in GORUNTULENEN
// boyutuna (CSS ile olceklenmis olabilir) donusturur.
async function clickSceneEntity(page, canvasX, canvasY) {
  const box = await page.evaluate(function (coords) {
    var canvas = document.getElementById('office-scene');
    var rect = canvas.getBoundingClientRect();
    var scaleX = rect.width / canvas.width;
    var scaleY = rect.height / canvas.height;
    return {
      clientX: rect.left + coords.x * scaleX,
      clientY: rect.top + coords.y * scaleY,
    };
  }, { x: canvasX, y: canvasY });
  await page.mouse.click(box.clientX, box.clientY);
}

test.describe('B047 -- sprite varlik hatti', () => {
  test('tum sprite varliklari GERCEKTEN yukleniyor (yerel dosya, CDN yok)', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.agent-card')).toHaveCount(9, { timeout: 10000 });

    await expect.poll(async () => {
      const debugState = await getSceneDebug(page);
      const statuses = Object.values(debugState.sprites);
      return statuses.every((s) => s === 'loaded') && statuses.length === 5;
    }, { timeout: 10000 }).toBe(true);

    // Yerel/ayni-origin oldugunu dogrudan da kontrol et -- CDN'e HICBIR istek YOK.
    const spriteResponse = await page.request.get('/assets/sprites/orchestrator.svg');
    expect(spriteResponse.ok()).toBe(true);
    expect(await spriteResponse.text()).toContain('<svg');
  });

  test('geri dusme: sprite yolu BOZUKSA render COKMEZ, mevcut bas-harf render\'ina doner', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.agent-card')).toHaveCount(9, { timeout: 10000 });

    const result = await page.evaluate(async function () {
      var canvas = document.createElement('canvas');
      canvas.width = 640;
      canvas.height = 320;
      var brokenScene = new window.OpsSuiteScene(canvas, { spriteBasePath: '/assets/sprites/DOES-NOT-EXIST/' });
      brokenScene.setAgents([
        { agent_id: 'orchestrator', display_name: 'Orchestrator', state: 'idle' },
      ]);
      // Yuklemelerin (basarisiz olacak sekilde) SONUCLANMASINI bekle --
      // sabit bir sleep DEGIL, "tum durumlar artik 'pending' degil" kosulu.
      await new Promise(function (resolve) {
        var deadline = Date.now() + 5000;
        (function poll() {
          var statuses = Object.keys(brokenScene._sprites).map(function (k) { return brokenScene._sprites[k].status; });
          if (statuses.every(function (s) { return s !== 'pending'; }) || Date.now() > deadline) {
            resolve();
            return;
          }
          setTimeout(poll, 50);
        })();
      });
      var threw = false;
      try {
        brokenScene.render(); // COKMEMELI
      } catch (err) {
        threw = true;
      }
      var debugState = brokenScene.debugState();
      return { threw: threw, sprites: debugState.sprites, agentState: debugState.agents.orchestrator.state };
    });

    expect(result.threw).toBe(false);
    expect(Object.values(result.sprites).every((s) => s === 'error')).toBe(true);
    expect(result.agentState).toBe('idle'); // sahne verisi hala DOGRU, yalnizca gorsel GERI DUSTU
  });
});

test.describe('B049 -- sahne tiklama etkilesimleri', () => {
  test('bilinen-canli bir ajana tiklamak GERCEK verilerle detay panelini acar', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.agent-card')).toHaveCount(9, { timeout: 10000 });

    const debugState = await getSceneDebug(page);
    const orchestrator = debugState.agents.orchestrator;
    await clickSceneEntity(page, orchestrator.x, orchestrator.y);

    await expect(page.locator('#agent-detail-panel')).toBeVisible();
    await expect(page.locator('#agent-detail-name')).toContainText('orchestrator');
    await expect(page.locator('#agent-detail-state')).toHaveText('offline');
    // Ajanlarin KENDI bir yetki kapsami OLMADIGI acikca belirtilmeli (fabrike edilmez).
    await expect(page.locator('#agent-detail-scope')).toContainText('YOKTUR');

    await page.locator('#agent-detail-close').click();
    await expect(page.locator('#agent-detail-panel')).toBeHidden();
  });

  test('hayalet raftaki (not_implemented) bir ajana tiklamak dahi GERCEK detayi gosterir', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.agent-card')).toHaveCount(9, { timeout: 10000 });

    const debugState = await getSceneDebug(page);
    const financeAgent = debugState.agents.finance_agent;
    expect(financeAgent.zone).toBe('ghost');
    await clickSceneEntity(page, financeAgent.x, financeAgent.y);

    await expect(page.locator('#agent-detail-panel')).toBeVisible();
    await expect(page.locator('#agent-detail-name')).toContainText('finance_agent');
    await expect(page.locator('#agent-detail-detail')).toContainText('not_implemented');
  });

  test('asistana tiklamak asistan detayini gosterir', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.agent-card')).toHaveCount(9, { timeout: 10000 });
    const debugState = await getSceneDebug(page);
    // Asistanin sabit konumu -- scene.js::ASSISTANT_POS ile AYNI (590, 35).
    await clickSceneEntity(page, 590, 35);
    await expect(page.locator('#agent-detail-panel')).toBeVisible();
    await expect(page.locator('#agent-detail-name')).toContainText('Asistan');
  });

  test('bekleyen bir onayla eslesen ajana tiklamak onay baglantisini gosterir VE tiklaninca ilgili kaydi vurgular', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#ws-status')).toHaveClass(/ws-status--open/, { timeout: 10000 });

    await page.locator('#voice-input').fill('Ezo, tüm dosyaları sil');
    await page.locator('#voice-form button[type="submit"]').click();
    await expect(page.locator('.approval-item')).toHaveCount(1, { timeout: 10000 });

    const requestId = await page.locator('.approval-item').getAttribute('data-request-id');

    const debugState = await getSceneDebug(page);
    const orchestrator = debugState.agents.orchestrator;
    await clickSceneEntity(page, orchestrator.x, orchestrator.y);

    await expect(page.locator('#agent-detail-panel')).toBeVisible();
    await expect(page.locator('#agent-detail-task')).toHaveText(requestId);
    await expect(page.locator('#agent-detail-approval-link')).toBeVisible();

    await page.locator('#agent-detail-approval-link').click();
    await expect(page.locator('.approval-item')).toHaveClass(/approval-item--highlighted/);
  });
});
