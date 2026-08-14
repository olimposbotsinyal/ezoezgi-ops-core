// Ops Suite E2E duman testi konfigurasyonu (BACKLOG.md B039, PLAN.md T29).
// Gercek bir `python -m ops_suite.server` alt-surecine karsi, gercek bir
// (headless) tarayiciyla calisir -- `node --check` sozdizimi dogrulamasinin
// OTESINDE, GERCEK render/DOM/fetch davranisini kanitlar.

const { defineConfig, devices } = require('@playwright/test');

const PORT = process.env.OPS_SUITE_E2E_PORT || '8421';
const BASE_URL = `http://127.0.0.1:${PORT}`;

module.exports = defineConfig({
  testDir: './tests',
  timeout: 30000,
  retries: 0,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: 'test-results/results.json' }]],
  globalSetup: require.resolve('./global-setup.js'),
  use: {
    baseURL: BASE_URL,
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
