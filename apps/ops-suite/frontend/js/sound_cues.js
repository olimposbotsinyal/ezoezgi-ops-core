// EzoEzgi Ops Suite -- ses ipucu cercevesi (BACKLOG.md B050, PLAN.md T46,
// DECISIONS.md ADR-024). Yerel (CDN'siz), Web Audio API ile SENTEZLENMIS
// tonlar -- gercek bir ses DOSYASI (mp3/wav) YOK, bu hem offline-first
// ilkesiyle (ADR-018) hem de test edilebilirlikle uyumludur.
//
// **Durustluk siniri:** bu ortamda hoparlor donanimi YOK -- sesin insan
// kulagiyla GERCEKTEN duyuldugu dogrulanamaz (NOT_COLLECTED, bkz.
// docs/RUNBOOK.md). Test edilen: dogru kosullarda (mute/policy KAPALI
// DEGILKEN) dogru parametrelerle GERCEK bir `OscillatorNode` cagrisinin
// yapilip YAPILMADIGI -- sessizce hicbir sey yapmadan "basarili" gibi
// DAVRANMAZ, her cagri `debugState().last_play` uzerinden GOZLEMLENEBILIR.

(function () {
  "use strict";

  var STORAGE_KEY_MUTED = "ops_suite_sound_muted";

  // 3 ayirt edilebilir ipucu -- her biri GERCEK, AYRI bir tetikleyici
  // kosula baglanir (bkz. app.js + ADR-024'un tetikleyici eslemesi).
  var CUES = {
    approval_needed: { freq: 660, durationMs: 180, type: "triangle" },
    task_complete: { freq: 880, durationMs: 120, type: "sine" },
    policy_block: { freq: 220, durationMs: 260, type: "sawtooth" },
  };

  function readMutedPref() {
    try {
      return typeof window !== "undefined" &&
        window.localStorage.getItem(STORAGE_KEY_MUTED) === "1";
    } catch (err) {
      return false; // localStorage erisilemezse GUVENLI varsayilan: SESLI
    }
  }

  function SoundCues(options) {
    options = options || {};
    // Politika kapisi -- v0'da gercek bir merkezi config kaynagi YOK
    // (bkz. ADR-024), bu yuzden varsayilan ACIK; programatik/testte
    // `setPolicyEnabled(false)` ile kapatilabilir.
    this.policyEnabled = options.policyEnabled !== false;
    this._muted = options.initialMuted !== undefined ? !!options.initialMuted : readMutedPref();
    this._AudioContextCtor = options.audioContextCtor ||
      (typeof window !== "undefined" ? (window.AudioContext || window.webkitAudioContext) : null);
    this._ctx = null;
    // Test edilebilirlik: en son GERCEKTEN calinan/ENGELLENEN cagri + nedeni.
    this._lastPlay = null;
  }

  SoundCues.prototype.isMuted = function () {
    return this._muted;
  };

  SoundCues.prototype.setMuted = function (muted) {
    this._muted = !!muted;
    try {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(STORAGE_KEY_MUTED, this._muted ? "1" : "0");
      }
    } catch (err) {
      // localStorage yoksa (ozel/gizli tarayici modu vb.) sessizce yut --
      // mute durumu bu oturum icinde hala GECERLI kalir, yalnizca
      // kalicilik kaybolur.
    }
  };

  SoundCues.prototype.setPolicyEnabled = function (enabled) {
    this.policyEnabled = !!enabled;
  };

  // `cueName` bilinmiyorsa VEYA mute/politika engelliyorsa GERCEKTEN
  // hicbir ses cagrisi YAPILMAZ -- `_lastPlay` yine de NEDEN
  // engellendigini kaydeder (sessizce "basarili" gibi davranmaz).
  SoundCues.prototype.play = function (cueName) {
    var cue = CUES[cueName];
    if (!cue) {
      this._lastPlay = { cue: cueName, played: false, reason: "unknown_cue" };
      return false;
    }
    if (this._muted) {
      this._lastPlay = { cue: cueName, played: false, reason: "muted" };
      return false;
    }
    if (!this.policyEnabled) {
      this._lastPlay = { cue: cueName, played: false, reason: "policy_disabled" };
      return false;
    }
    if (!this._AudioContextCtor) {
      this._lastPlay = { cue: cueName, played: false, reason: "no_audio_context" };
      return false;
    }
    try {
      if (!this._ctx) {
        this._ctx = new this._AudioContextCtor();
      }
      var osc = this._ctx.createOscillator();
      var gain = this._ctx.createGain();
      osc.type = cue.type;
      osc.frequency.value = cue.freq;
      osc.connect(gain);
      gain.connect(this._ctx.destination);
      var now = this._ctx.currentTime;
      gain.gain.setValueAtTime(0.15, now);
      osc.start(now);
      osc.stop(now + cue.durationMs / 1000);
      this._lastPlay = { cue: cueName, played: true, reason: null, freq: cue.freq, duration_ms: cue.durationMs };
      return true;
    } catch (err) {
      this._lastPlay = { cue: cueName, played: false, reason: "audio_error: " + err.message };
      return false;
    }
  };

  // Playwright'in gercek tarayicida cagirabilecegi test koprusu (T35/T42
  // ile AYNI desen -- bkz. scene.js::debugState).
  SoundCues.prototype.debugState = function () {
    return {
      muted: this._muted,
      policy_enabled: this.policyEnabled,
      last_play: this._lastPlay,
    };
  };

  window.OpsSuiteSoundCues = SoundCues;
})();
