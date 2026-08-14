// Ops Suite E2E duman testi konfigurasyonu (BACKLOG.md B039, PLAN.md T29/T44).
// Gercek bir `python -m ops_suite.server` alt-surecine karsi, gercek bir
// (headless) tarayiciyla calisir -- `node --check` sozdizimi dogrulamasinin
// OTESINDE, GERCEK render/DOM/fetch davranisini kanitlar.
//
// **Dosya-basina sunucu (T44, PLAN.md -- gercek kesfedilen hata):** ONCEDEN tek
// bir paylasilan `globalSetup` sunucusu TUM dosyalar icin kullaniliyordu
// -- bu, dosyalar arasi durum sizintisina yol acti (bkz. `test-server.js`
// dokustringi). Artik HER spec dosyasi KENDI `test.beforeAll`/`afterAll`
// cifti ile kendi sunucusunu yonetir (bkz. `test-server.js`); bu yuzden
// `globalSetup` BURADA YOK. `workers: 1` (sirali calisma) bu modelin
// GUVENLE calismasi icin ZORUNLUDUR (ayni port sirayla yeniden kullanilir).

const { defineConfig, devices } = require('@playwright/test');
const { DEFAULT_PORT } = require('./test-server');

module.exports = defineConfig({
  testDir: './tests',
  timeout: 30000,
  retries: 0,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: 'test-results/results.json' }]],
  use: {
    baseURL: `http://127.0.0.1:${DEFAULT_PORT}`,
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
