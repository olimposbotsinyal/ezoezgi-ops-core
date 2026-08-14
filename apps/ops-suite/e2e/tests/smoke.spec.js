// Ops Suite -- GERCEK tarayici duman (smoke) testleri (BACKLOG.md B039,
// PLAN.md T29). Onceki tek dogrulama katmani `node --check` (yalnizca
// sozdizimi) idi -- bu testler ILK KEZ gercek DOM render'ini, gercek
// fetch()'i ve gercek bir WebSocket el sikismasini bir tarayicida
// dogrular. Kapsam BILEREK minimal tutuldu (2 senaryo) -- tam
// gorsel/etkilesimli regresyon paketi B038 (animasyonlu sahne)
// tamamlandiktan SONRA genisletilecek.

const { test, expect } = require('@playwright/test');

test('kok sayfa yukleniyor, ajan kartlari GERCEK fetch ile render ediliyor, WS baglaniyor', async ({ page }) => {
  await page.goto('/');

  await expect(page).toHaveTitle(/EzoEzgi Ops Suite/);

  // `status_resolver.py`, en az KNOWN_LIVE_AGENTS + NOT_IMPLEMENTED_AGENTS
  // kadar kart URETMELIDIR (bkz. test_ops_suite_api.py::test_get_agents_...) --
  // burada GERCEK bir tarayicida DOM'a yazildigini kanitliyoruz.
  const agentCards = page.locator('.agent-card');
  await expect(agentCards.first()).toBeVisible({ timeout: 10000 });
  const cardCount = await agentCards.count();
  expect(cardCount).toBeGreaterThanOrEqual(9); // 3 KNOWN_LIVE + 6 NOT_IMPLEMENTED

  // WS baglantisi gercekten acilmali (ws_client.js -> /ws/live).
  await expect(page.locator('#ws-status')).toHaveClass(/ws-status--open/, { timeout: 10000 });
});

test('sesli komut gonderimi GERCEK bir tarayicidan onay kuyrugunu gunceller', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#ws-status')).toHaveClass(/ws-status--open/, { timeout: 10000 });

  await page.locator('#voice-input').fill("Ezo, tüm dosyaları sil");
  await page.locator('#voice-form button[type="submit"]').click();

  // Irreversible komut onay kuyruguna dusmeli -- approval-list GERCEKTEN
  // guncellenmeli (fetch + DOM render, mock DEGIL).
  await expect(page.locator('.approval-item')).toHaveCount(1, { timeout: 10000 });
  await expect(page.locator('.approval-item')).toContainText('RUN_DELETE_FILE');

  // Canli olay akisi (WS) da en az bir kayit almis olmali.
  await expect(page.locator('#event-feed li').first()).toBeVisible({ timeout: 10000 });
});
