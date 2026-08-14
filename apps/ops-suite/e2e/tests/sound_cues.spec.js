// Ops Suite -- ses ipucu cercevesi GERCEK tarayici testleri (BACKLOG.md
// B050, PLAN.md T46, DECISIONS.md ADR-024). Bu ortamda hoparlor donanimi
// YOK (bkz. docs/RUNBOOK.md NOT_COLLECTED notu) -- bu yuzden "insan
// kulagiyla GERCEKTEN duyuldu" test EDILEMEZ. Test edilen: dogru
// kosullarda (mute/politika) GERCEK bir `OscillatorNode` cagrisinin
// yapilip YAPILMADIGI, `SoundCues.debugState().last_play` uzerinden.

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

function getSoundDebug(page) {
  return page.evaluate(function () {
    return window.__ops_suite_sound_debug__ ? window.__ops_suite_sound_debug__() : null;
  });
}

test.describe('B050 -- ses ipucu birimi (bagimsiz OpsSuiteSoundCues ornegi)', () => {
  test('varsayilan (sessize alinmamis, politika acik): play() GERCEK bir OscillatorNode cagrisi yapar', async ({ page }) => {
    await page.goto('/');
    const result = await page.evaluate(function () {
      var cues = new window.OpsSuiteSoundCues();
      var played = cues.play('task_complete');
      return { played: played, state: cues.debugState() };
    });
    expect(result.played).toBe(true);
    expect(result.state.last_play).toEqual(
      expect.objectContaining({ cue: 'task_complete', played: true, reason: null, freq: 880, duration_ms: 120 })
    );
  });

  test('mute ACIKKEN: play() GERCEK hicbir ses cagrisi YAPMAZ', async ({ page }) => {
    await page.goto('/');
    const result = await page.evaluate(function () {
      var cues = new window.OpsSuiteSoundCues({ initialMuted: true });
      var played = cues.play('approval_needed');
      return { played: played, state: cues.debugState() };
    });
    expect(result.played).toBe(false);
    expect(result.state.last_play).toEqual(
      expect.objectContaining({ cue: 'approval_needed', played: false, reason: 'muted' })
    );
  });

  test('politika kapisi KAPALIYKEN: play() GERCEK hicbir ses cagrisi YAPMAZ', async ({ page }) => {
    await page.goto('/');
    const result = await page.evaluate(function () {
      var cues = new window.OpsSuiteSoundCues({ policyEnabled: false });
      var played = cues.play('policy_block');
      return { played: played, state: cues.debugState() };
    });
    expect(result.played).toBe(false);
    expect(result.state.last_play).toEqual(
      expect.objectContaining({ cue: 'policy_block', played: false, reason: 'policy_disabled' })
    );
  });

  test('bilinmeyen bir ipucu adi: play() GERCEK hicbir ses cagrisi YAPMAZ', async ({ page }) => {
    await page.goto('/');
    const result = await page.evaluate(function () {
      var cues = new window.OpsSuiteSoundCues();
      return { played: cues.play('does_not_exist'), state: cues.debugState() };
    });
    expect(result.played).toBe(false);
    expect(result.state.last_play.reason).toBe('unknown_cue');
  });
});

test.describe('B050 -- uygulama entegrasyonu (GERCEK mute dugmesi + GERCEK tetikleyiciler)', () => {
  test('mute dugmesi: ACIKKEN gercek bir sesli komut CUE\'yu BASTIRIR, tekrar acilinca AYNI cue GERCEKTEN calinir', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#ws-status')).toHaveClass(/ws-status--open/, { timeout: 10000 });

    // Varsayilan: taze bir tarayici baglami (localStorage bos) -> sessiz DEGIL.
    var initial = await getSoundDebug(page);
    expect(initial.muted).toBe(false);

    await page.locator('#sound-mute-toggle').click();
    await expect(page.locator('#sound-mute-toggle')).toHaveText('Ses: Kapalı');
    await expect(page.locator('#sound-mute-toggle')).toHaveAttribute('aria-pressed', 'true');

    await page.locator('#voice-input').fill("Ezo, echo ile 'merhaba' yaz");
    await page.locator('#voice-form button[type="submit"]').click();
    await expect(page.locator('#assistant-state')).toHaveText('speaking', { timeout: 10000 });

    await expect.poll(async () => {
      var s = await getSoundDebug(page);
      return s.last_play ? s.last_play.cue : null;
    }, { timeout: 5000 }).toBe('task_complete');
    var mutedState = await getSoundDebug(page);
    expect(mutedState.last_play.played).toBe(false);
    expect(mutedState.last_play.reason).toBe('muted');

    // Simdi ac -- AYNI tetikleyici koşulu GERCEKTEN calmali.
    await page.locator('#sound-mute-toggle').click();
    await expect(page.locator('#sound-mute-toggle')).toHaveText('Ses: Açık');

    await page.locator('#voice-input').fill("Ezo, echo ile 'tekrar' yaz");
    await page.locator('#voice-form button[type="submit"]').click();

    await expect.poll(async () => {
      var s = await getSoundDebug(page);
      return s.last_play && s.last_play.cue === 'task_complete' ? s.last_play.played : null;
    }, { timeout: 5000 }).toBe(true);
  });

  test('onay-gerekli tetikleyicisi: GERCEK bir irreversible komut kuyruga dustugunde approval_needed cue\'su GERCEKTEN calinir', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#ws-status')).toHaveClass(/ws-status--open/, { timeout: 10000 });

    await page.locator('#voice-input').fill("Ezo, tüm dosyaları sil");
    await page.locator('#voice-form button[type="submit"]').click();
    await expect(page.locator('.approval-item')).toHaveCount(1, { timeout: 10000 });

    await expect.poll(async () => {
      var s = await getSoundDebug(page);
      return s.last_play ? s.last_play.cue : null;
    }, { timeout: 5000 }).toBe('approval_needed');
    var state = await getSoundDebug(page);
    expect(state.last_play.played).toBe(true);
  });

  test('politika-engeli tetikleyicisi: token OLMADAN bir onayi onaylamaya calismak GERCEK bir 401 doner VE policy_block cue\'su GERCEKTEN calinir', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#ws-status')).toHaveClass(/ws-status--open/, { timeout: 10000 });

    var before = await page.locator('.approval-item').count();
    await page.locator('#voice-input').fill("Ezo, tüm dosyaları sil");
    await page.locator('#voice-form button[type="submit"]').click();
    await expect(page.locator('.approval-item')).toHaveCount(before + 1, { timeout: 10000 });
    // `list_pending()` eskiden-yeniye SIRALIDIR (bkz. approval_queue.py --
    // dict ekleme sirasi) -- bu dosyadaki ONCEKI bir testten kalan kayit
    // olabileceginden (paylasilan dosya-basi sunucu, T44), BU testte
    // YENI eklenen kayit her zaman SON DOM elemanidir.
    var newItem = page.locator('.approval-item').last();
    await expect(newItem).toBeVisible();

    // BILEREK hicbir token ayarlanmadi -- B044'un GERCEK auth guard'i
    // bu istegi 401 ile reddetmeli (bkz. identity.py::MissingTokenError -> 401).
    page.once('dialog', (dialog) => dialog.accept()); // decide()'in window.alert() cagrisi
    await newItem.locator('.approve').click();

    await expect.poll(async () => {
      var s = await getSoundDebug(page);
      return s.last_play ? s.last_play.cue : null;
    }, { timeout: 5000 }).toBe('policy_block');
    var state = await getSoundDebug(page);
    expect(state.last_play.played).toBe(true);
    expect(state.last_play.reason).toBeNull();
  });
});
