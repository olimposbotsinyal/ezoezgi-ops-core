// EzoEzgi Ops Suite -- animasyonlu 2D ofis sahnesi (BACKLOG.md B038,
// docs/DECISIONS.md ADR-020, docs/PLAN.md T31-T35). Saf Canvas2D,
// ucuncu taraf kutuphane YOK (ADR-020 ile tutarli). Bu modul app.js'e
// DOKUNMAZ -- app.js, mevcut refreshAgents/refreshAssistant/refreshApprovals
// cagrilarinin SONUNA scene.set*() ekler (bkz. PLAN.md T35).
//
// **Durustluk ilkesi (bkz. docs/AGENT_PRESENCE_STATE_MODEL.md SS3):**
// NOT_IMPLEMENTED_AGENTS (finance/social/research/doc/device/voice)
// ASLA canli personel gibi (masada calisiyor) gosterilmez -- ayri,
// acikca "hayalet" stilli bir rafta, sabit "not_implemented" durumuyla
// render edilir.
//
// **Test edilebilirlik koprusu:** Canvas pikselleri Playwright'in DOM
// sorgulariyla GORULEMEZ (bkz. ADR-020) -- bu yuzden `debugState()`,
// sahnenin o anki durumunu DUZ JSON olarak dondurur; `app.js` bunu
// `window.__ops_suite_scene_debug__`'e baglar.

(function () {
  "use strict";

  var CANVAS_W = 640;
  var CANVAS_H = 320;

  // Bilinen-canli 3 ajanin SABIT masa konumlari (bkz. status_resolver.py::KNOWN_LIVE_AGENTS).
  var DESK_POSITIONS = {
    orchestrator: { x: 130, y: 90 },
    bridge_agent: { x: 320, y: 90 },
    tool_runners: { x: 510, y: 90 },
  };

  // Bos/dinlenme bolgesi -- "idle"/"offline" durumundaki bilinen-canli
  // ajanlarin GITTIGI konum (masalarin biraz altinda, ayri/etiketli).
  var REST_ZONE = { x: 320, y: 180 };

  // "Henuz yok" rafi -- NOT_IMPLEMENTED_AGENTS icin, masalardan GORSEL
  // OLARAK AYRIK, soluk stilli sabit sira (dizilim sirasi status_resolver.py'nin
  // NOT_IMPLEMENTED_AGENTS sozluk sirasiyla ESLESIR, ama bu sadece gorsel
  // duzen icin -- durustluk kurali render fonksiyonunda uygulanir).
  var GHOST_SHELF_Y = 275;
  var GHOST_SHELF_START_X = 70;
  var GHOST_SHELF_SPACING = 95;

  var ASSISTANT_POS = { x: 590, y: 35 };
  var APPROVAL_TRAY_POS = { x: 40, y: 35 };

  var AGENT_STATE_COLORS = {
    working: "#4ade80",
    idle: "#8a97ad",
    blocked: "#f87171",
    awaiting_approval: "#f59e0b",
    offline: "#3a4560",
  };

  var ASSISTANT_STATE_COLORS = {
    idle: "#8a97ad",
    listening: "#4ade80",
    thinking: "#f59e0b",
    speaking: "#38bdf8",
    blocked_policy: "#f87171",
  };

  var LERP_SPEED = 0.18; // kare-basi enterpolasyon orani (0-1) -- T33

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function OpsSuiteScene(canvasEl) {
    this.canvas = canvasEl;
    this.ctx = canvasEl.getContext ? canvasEl.getContext("2d") : null;
    this.agents = {}; // agent_id -> {x,y,targetX,targetY,state,display_name,zone}
    this.assistant = { x: ASSISTANT_POS.x, y: ASSISTANT_POS.y, state: "idle" };
    this.pendingApprovalCount = 0;
    this._running = false;
  }

  OpsSuiteScene.prototype._zoneForAgent = function (agentId, state) {
    if (!DESK_POSITIONS[agentId]) {
      return "ghost"; // not_implemented -- HER ZAMAN hayalet raf, state'ten BAGIMSIZ
    }
    if (state === "working" || state === "blocked" || state === "awaiting_approval") {
      return "desk";
    }
    return "rest"; // idle | offline
  };

  var REST_SPACING = 34; // T33 fix -- birden fazla ajan AYNI dinlenme noktasinda UST USTE binmesin diye yatay yayilma

  OpsSuiteScene.prototype._targetForZone = function (agentId, zone, index, countInZone) {
    if (zone === "desk") {
      return DESK_POSITIONS[agentId];
    }
    if (zone === "ghost") {
      return { x: GHOST_SHELF_START_X + index * GHOST_SHELF_SPACING, y: GHOST_SHELF_Y };
    }
    // "rest" -- birden fazla ajan varsa REST_ZONE.x etrafinda ORTALANMIS yatay sira
    var offset = (index - (countInZone - 1) / 2) * REST_SPACING;
    return { x: REST_ZONE.x + offset, y: REST_ZONE.y };
  };

  // T32/T33 -- ajan listesini (GET /api/agents ciktisi) sahne
  // varliklarina esler; zaten var olan bir varlik icin yalnizca
  // HEDEF konumu gunceller (GERCEK konum, animasyon dongusunde ADIM
  // ADIM hedefe yaklasir -- aninda ZIPLAMAZ). Ayni bolgeye (ozellikle
  // "rest") birden fazla ajan dusebilir -- UST USTE binmemeleri icin
  // once her ajanin bolgesi hesaplanir, SONRA o bolge icindeki sirasina
  // gore yatay olarak yayilir (bkz. `_targetForZone`).
  OpsSuiteScene.prototype.setAgents = function (agentList) {
    var self = this;
    var zoneCounts = { ghost: 0, rest: 0 };
    var withZones = (agentList || []).map(function (agent) {
      var zone = self._zoneForAgent(agent.agent_id, agent.state);
      return { agent: agent, zone: zone };
    });
    withZones.forEach(function (entry) {
      if (entry.zone === "ghost" || entry.zone === "rest") {
        zoneCounts[entry.zone] += 1;
      }
    });
    var zoneRunningIndex = { ghost: 0, rest: 0 };
    withZones.forEach(function (entry) {
      var agent = entry.agent;
      var zone = entry.zone;
      var index = zone === "ghost" || zone === "rest" ? zoneRunningIndex[zone] : 0;
      var target = self._targetForZone(agent.agent_id, zone, index, zoneCounts[zone]);
      if (zone === "ghost" || zone === "rest") {
        zoneRunningIndex[zone] += 1;
      }
      var existing = self.agents[agent.agent_id];
      if (!existing) {
        self.agents[agent.agent_id] = {
          x: target.x, y: target.y, targetX: target.x, targetY: target.y,
          state: agent.state, display_name: agent.display_name, zone: zone,
        };
      } else {
        existing.targetX = target.x;
        existing.targetY = target.y;
        existing.state = agent.state;
        existing.display_name = agent.display_name;
        existing.zone = zone;
      }
    });
  };

  // T34 -- asistan durumunu gunceller (konum sabit, yalnizca durum/renk degisir).
  OpsSuiteScene.prototype.setAssistant = function (presence) {
    if (presence && presence.state) {
      this.assistant.state = presence.state;
    }
  };

  // T35 -- onay kuyrugu derinligi (bkz. approval-tray rozeti).
  OpsSuiteScene.prototype.setPendingApprovalCount = function (count) {
    this.pendingApprovalCount = count || 0;
  };

  OpsSuiteScene.prototype._step = function () {
    var self = this;
    Object.keys(this.agents).forEach(function (id) {
      var a = self.agents[id];
      a.x = lerp(a.x, a.targetX, LERP_SPEED);
      a.y = lerp(a.y, a.targetY, LERP_SPEED);
    });
  };

  OpsSuiteScene.prototype._drawZoneLabels = function (ctx) {
    ctx.fillStyle = "#8a97ad";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    Object.keys(DESK_POSITIONS).forEach(function (id) {
      var p = DESK_POSITIONS[id];
      ctx.strokeStyle = "#24304a";
      ctx.strokeRect(p.x - 28, p.y - 28, 56, 56);
      ctx.fillText(id, p.x, p.y + 45);
    });
    ctx.beginPath();
    ctx.strokeStyle = "#24304a";
    ctx.setLineDash([4, 3]);
    ctx.arc(REST_ZONE.x, REST_ZONE.y, 34, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillText("Dinlenme Bölgesi", REST_ZONE.x, REST_ZONE.y + 48);

    ctx.fillText("Henüz Uygulanmadı", GHOST_SHELF_START_X + GHOST_SHELF_SPACING * 2.5, GHOST_SHELF_Y - 24);
  };

  OpsSuiteScene.prototype._drawAgent = function (ctx, agent) {
    var color = AGENT_STATE_COLORS[agent.state] || AGENT_STATE_COLORS.offline;
    var isGhost = agent.zone === "ghost";
    ctx.beginPath();
    ctx.globalAlpha = isGhost ? 0.35 : 1.0;
    ctx.fillStyle = color;
    ctx.arc(agent.x, agent.y, isGhost ? 14 : 18, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1.0;
    ctx.fillStyle = "#0b1220";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText((agent.display_name || "?").slice(0, 2).toUpperCase(), agent.x, agent.y + 3);
    ctx.fillStyle = isGhost ? "#5a6580" : "#e6ecf5";
    ctx.font = "10px sans-serif";
    ctx.fillText(agent.state, agent.x, agent.y + (isGhost ? 26 : 32));
  };

  OpsSuiteScene.prototype._drawAssistant = function (ctx) {
    var color = ASSISTANT_STATE_COLORS[this.assistant.state] || ASSISTANT_STATE_COLORS.idle;
    var radius = this.assistant.state === "speaking" ? 26 : 20; // T34 rapor-modu buyume
    ctx.beginPath();
    ctx.fillStyle = color;
    ctx.arc(this.assistant.x, this.assistant.y, radius, 0, Math.PI * 2);
    ctx.fill();
    if (this.assistant.state === "speaking") {
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.arc(this.assistant.x, this.assistant.y, radius + 6, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.fillStyle = "#0b1220";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Ez", this.assistant.x, this.assistant.y + 3);
    ctx.fillStyle = "#e6ecf5";
    ctx.fillText(this.assistant.state, this.assistant.x, this.assistant.y + radius + 14);
  };

  OpsSuiteScene.prototype._drawApprovalTray = function (ctx) {
    ctx.beginPath();
    ctx.fillStyle = this.pendingApprovalCount > 0 ? "#f59e0b" : "#3a4560";
    ctx.arc(APPROVAL_TRAY_POS.x, APPROVAL_TRAY_POS.y, 16, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#0b1220";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(String(this.pendingApprovalCount), APPROVAL_TRAY_POS.x, APPROVAL_TRAY_POS.y + 4);
    ctx.fillStyle = "#8a97ad";
    ctx.font = "10px sans-serif";
    ctx.fillText("Onay", APPROVAL_TRAY_POS.x, APPROVAL_TRAY_POS.y + 28);
  };

  OpsSuiteScene.prototype.render = function () {
    if (!this.ctx) {
      return;
    }
    var ctx = this.ctx;
    ctx.clearRect(0, 0, CANVAS_W, CANVAS_H);
    ctx.fillStyle = "#0b1220";
    ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);

    this._drawZoneLabels(ctx);
    var self = this;
    Object.keys(this.agents).forEach(function (id) {
      self._drawAgent(ctx, self.agents[id]);
    });
    this._drawAssistant(ctx);
    this._drawApprovalTray(ctx);
  };

  OpsSuiteScene.prototype._loop = function () {
    if (!this._running) {
      return;
    }
    this._step();
    this.render();
    var self = this;
    window.requestAnimationFrame(function () {
      self._loop();
    });
  };

  OpsSuiteScene.prototype.start = function () {
    if (this._running) {
      return;
    }
    this._running = true;
    this._loop();
  };

  OpsSuiteScene.prototype.stop = function () {
    this._running = false;
  };

  // T35 -- Playwright'in gercek tarayicida cagirabilecegi, sahnenin O
  // ANKI durumunu duz JSON olarak dondurur (piksel-diff GEREKMEZ).
  OpsSuiteScene.prototype.debugState = function () {
    var agentsOut = {};
    Object.keys(this.agents).forEach(function (id) {
      var a = this.agents[id];
      agentsOut[id] = {
        state: a.state,
        zone: a.zone,
        x: Math.round(a.x),
        y: Math.round(a.y),
        target_x: Math.round(a.targetX),
        target_y: Math.round(a.targetY),
        at_rest_position: Math.round(a.x) === Math.round(a.targetX) && Math.round(a.y) === Math.round(a.targetY),
      };
    }, this);
    return {
      assistant_state: this.assistant.state,
      pending_approval_count: this.pendingApprovalCount,
      agents: agentsOut,
    };
  };

  window.OpsSuiteScene = OpsSuiteScene;
})();
