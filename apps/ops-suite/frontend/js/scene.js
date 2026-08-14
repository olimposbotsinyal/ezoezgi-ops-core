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

  // B048 (BACKLOG.md B048, PLAN.md T45, DECISIONS.md ADR-023) -- gorev
  // isaretcisi (task marker) sabit konumlari. "kuyrukta" -> bir masaya
  // henuz ATANMAMIS (agent_id yok) gorevlerin bekledigi nokta;
  // "tamamlandi" -> islemesi biten (basarili/basarisiz/onay-bekliyor
  // farketmeksizin, bkz. ADR-023) gorevlerin biriktigi rafin baslangici.
  var TASK_QUEUE_POS = { x: 40, y: 130 };
  var TASK_COMPLETED_TRAY_POS = { x: 600, y: 190 };
  var TASK_MARKER_SPACING = 22;
  var MAX_TASK_MARKERS = 8; // v0 bellek siniri -- asimda en eski TAMAMLANMIS isaretciler GC edilir

  var TASK_STAGE_COLORS = {
    queued: "#8a97ad",
    assigned: "#38bdf8",
    working: "#4ade80",
    completed: "#5a6580",
  };

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

  // B047 (BACKLOG.md B047, PLAN.md T40) -- yerel (CDN'siz) sprite hatti.
  // `spriteBasePath` constructor'da EZILEBILIR (test edilebilirlik icin --
  // ornegin var-olmayan bir yola isaret ederek GERI DUSME yolunu GERCEKTEN
  // tetiklemek) -- varsayilan gercek uygulama yolunu kullanir.
  var DEFAULT_SPRITE_BASE_PATH = "/assets/sprites/";
  var AGENT_SPRITE_FILES = {
    orchestrator: "orchestrator.svg",
    bridge_agent: "bridge_agent.svg",
    tool_runners: "tool_runners.svg",
  };
  var ASSISTANT_SPRITE_FILE = "assistant.svg";
  var GHOST_SPRITE_FILE = "ghost.svg"; // NOT_IMPLEMENTED_AGENTS icin ORTAK

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function distance(x1, y1, x2, y2) {
    var dx = x1 - x2;
    var dy = y1 - y2;
    return Math.sqrt(dx * dx + dy * dy);
  }

  function OpsSuiteScene(canvasEl, options) {
    options = options || {};
    this.canvas = canvasEl;
    this.ctx = canvasEl.getContext ? canvasEl.getContext("2d") : null;
    this.agents = {}; // agent_id -> {x,y,targetX,targetY,state,display_name,zone}
    this.assistant = { x: ASSISTANT_POS.x, y: ASSISTANT_POS.y, state: "idle" };
    this.pendingApprovalCount = 0;
    this._running = false;
    // B049 (PLAN.md T42) -- bir varlik (ajan/asistan) tiklandiginda
    // cagirilir: `{kind: "agent"|"assistant", id: string}`. `app.js`
    // bunu detay panelini acmak icin baglar -- scene.js KENDISI DOM
    // paneli hakkinda hicbir sey BILMEZ (ayri sorumluluk).
    this.onEntityClick = null;
    this._sprites = {};
    this._loadSprites(options.spriteBasePath || DEFAULT_SPRITE_BASE_PATH);
    this._bindClickHandler();
    // B048 -- request_id -> {x,y,targetX,targetY,stage,agentId,lifecycleState,seq}.
    // `stage` in {"queued","assigned","working","completed"} -- bkz.
    // ADR-023 icin GERCEK olay eslemesi.
    this.taskMarkers = {};
    this._taskSeq = 0;
  }

  // B047 -- her sprite GERCEK bir `Image` yuklemesi dener; sonuc
  // ("loaded"/"error") `debugState()` uzerinden gozlemlenebilir (test
  // edilebilirlik). `window.Image` yoksa (ornegin bir SSR/test ortami)
  // GUVENLI sekilde "error" sayilir -- render KESINLIKLE COKMEZ.
  OpsSuiteScene.prototype._loadSprites = function (basePath) {
    var self = this;
    function load(key, filename) {
      var record = { img: null, status: "pending" };
      self._sprites[key] = record;
      if (typeof window === "undefined" || !window.Image) {
        record.status = "error";
        return;
      }
      var img = new window.Image();
      record.img = img;
      img.onload = function () {
        record.status = "loaded";
      };
      img.onerror = function () {
        record.status = "error";
      };
      img.src = basePath + filename;
    }
    load("assistant", ASSISTANT_SPRITE_FILE);
    load("ghost", GHOST_SPRITE_FILE);
    Object.keys(AGENT_SPRITE_FILES).forEach(function (agentId) {
      load(agentId, AGENT_SPRITE_FILES[agentId]);
    });
  };

  // Yuklenmis (GERCEKTEN kullanilabilir) bir sprite doner, YOKSA `null`
  // -- cagiran taraf `null` durumunda MEVCUT baş-harf render'ina GERI
  // DUSMELIDIR (bkz. `_drawAgent`/`_drawAssistant`).
  OpsSuiteScene.prototype._spriteFor = function (key) {
    var record = this._sprites[key];
    return record && record.status === "loaded" ? record.img : null;
  };

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

  OpsSuiteScene.prototype._upsertAgentRaw = function (agentId, state, displayName) {
    var existing = this.agents[agentId];
    if (!existing) {
      // `x`/`y` BILEREK `undefined` -- `_recomputeZones()` bunu, ilk
      // hedefine ESITLEYEREK doldurur (yeni bir ajanin (0,0)'dan
      // sahneye KAYMASINI onlemek icin, bkz. asagisi).
      this.agents[agentId] = {
        x: undefined, y: undefined, targetX: 0, targetY: 0,
        state: state, display_name: displayName, zone: "rest",
      };
    } else {
      existing.state = state;
      existing.display_name = displayName;
    }
  };

  // Su an izlenen TUM ajanlarin bolge/hedef konumunu YENIDEN hesaplar.
  // Hem `setAgents()` (toplu REST anlik goruntusu) HEM DE
  // `applyAgentPresenceEvent()` (T38, tek bir WS mesaji) TARAFINDAN
  // cagrilir -- boylece "rest" gibi PAYLASILAN bir bolgedeki yayilma
  // (bkz. `_targetForZone`) HER ZAMAN TUM ajanlarla TUTARLI kalir,
  // ister toplu ister tekli bir guncelleme sonrasi olsun.
  OpsSuiteScene.prototype._recomputeZones = function () {
    var self = this;
    var ids = Object.keys(this.agents);
    var zoneCounts = { ghost: 0, rest: 0 };
    var zoneOf = {};
    ids.forEach(function (id) {
      var zone = self._zoneForAgent(id, self.agents[id].state);
      zoneOf[id] = zone;
      if (zone === "ghost" || zone === "rest") {
        zoneCounts[zone] += 1;
      }
    });
    var runningIndex = { ghost: 0, rest: 0 };
    ids.forEach(function (id) {
      var zone = zoneOf[id];
      var index = zone === "ghost" || zone === "rest" ? runningIndex[zone] : 0;
      var target = self._targetForZone(id, zone, index, zoneCounts[zone]);
      if (zone === "ghost" || zone === "rest") {
        runningIndex[zone] += 1;
      }
      var entry = self.agents[id];
      entry.targetX = target.x;
      entry.targetY = target.y;
      entry.zone = zone;
      if (entry.x === undefined) {
        entry.x = target.x;
        entry.y = target.y;
      }
    });
  };

  // T32/T33 -- ajan listesini (GET /api/agents ciktisi) sahne
  // varliklarina esler; zaten var olan bir varlik icin yalnizca
  // HEDEF konumu gunceller (GERCEK konum, animasyon dongusunde ADIM
  // ADIM hedefe yaklasir -- aninda ZIPLAMAZ).
  OpsSuiteScene.prototype.setAgents = function (agentList) {
    var self = this;
    (agentList || []).forEach(function (agent) {
      self._upsertAgentRaw(agent.agent_id, agent.state, agent.display_name);
    });
    this._recomputeZones();
  };

  // T38 (BACKLOG.md B046) -- TEK bir `agent.presence` WS mesajini
  // ANINDA uygular, bir sonraki `GET /api/agents` REST-polling'ini
  // BEKLEMEDEN. T37'nin yayinladigi `working` gibi kisa omurlu ara
  // durumlarin sahnede GERCEKTEN gorunur olmasini saglayan taraf
  // BUDUR -- T37 yalnizca olayi YAYINLIYORDU, bunu TUKETEN yoktu.
  OpsSuiteScene.prototype.applyAgentPresenceEvent = function (payload) {
    if (!payload || !payload.agent_id) {
      return;
    }
    this._upsertAgentRaw(payload.agent_id, payload.state, payload.display_name);
    this._recomputeZones();
    // B048 -- eslesen bir gorev isaretcisi varsa (last_task_id capraz
    // referansi, B049'un onay-baglantisiyla AYNI desen), working/idle
    // gecisini o gorevin GORSEL asamasina da yansit.
    if (payload.last_task_id && this.taskMarkers[payload.last_task_id]) {
      var marker = this.taskMarkers[payload.last_task_id];
      if (payload.state === "working") {
        marker.stage = "working";
      } else if (payload.state === "idle" && marker.stage === "working") {
        marker.stage = "completed";
      }
      this._recomputeTaskMarkerPositions();
      this._evictOldTaskMarkers();
    }
  };

  // B048 -- TEK bir `task.lifecycle` WS mesajini isler. Yalnizca GERCEKTEN
  // yayinlanan durumlar goruluyor (received/translating/risk_checked +
  // nihai completed/failed/awaiting_approval, bkz. ADR-023) -- `routed`/
  // `executing` semada TANIMLI ama HICBIR kod yolunda URETILMEZ, bu
  // yuzden burada da ELLE simule EDILMEZ.
  OpsSuiteScene.prototype.applyTaskLifecycleEvent = function (payload) {
    if (!payload || !payload.request_id || !payload.state) {
      return;
    }
    var marker = this.taskMarkers[payload.request_id];
    if (!marker) {
      marker = this.taskMarkers[payload.request_id] = {
        x: TASK_QUEUE_POS.x, y: TASK_QUEUE_POS.y,
        targetX: TASK_QUEUE_POS.x, targetY: TASK_QUEUE_POS.y,
        stage: "queued", agentId: null, lifecycleState: payload.state,
        seq: this._taskSeq++,
      };
    }
    marker.lifecycleState = payload.state;
    if (payload.agent_id) {
      // Nihai olay artik bir ajana atanmis oldugunu GERCEKTEN gosteriyor
      // (agent_id yalnizca completed/failed/awaiting_approval olaylarinda
      // dolu gelir, bkz. voice_bridge.py).
      marker.agentId = payload.agent_id;
      if (marker.stage === "queued") {
        marker.stage = "assigned";
      }
    }
    this._recomputeTaskMarkerPositions();
    this._evictOldTaskMarkers();
  };

  // Butun gorev isaretcilerinin hedef konumunu, GUNCEL asamalarina gore
  // yeniden hesaplar. "tamamlandi" asamasindaki isaretciler, üst üste
  // binmemeleri icin (REST_SPACING ile AYNI desen) rafta yatay yayilir.
  OpsSuiteScene.prototype._recomputeTaskMarkerPositions = function () {
    var self = this;
    var completedIds = Object.keys(this.taskMarkers).filter(function (id) {
      return self.taskMarkers[id].stage === "completed";
    });
    completedIds.sort(function (a, b) {
      return self.taskMarkers[a].seq - self.taskMarkers[b].seq;
    });
    Object.keys(this.taskMarkers).forEach(function (id) {
      var m = self.taskMarkers[id];
      var target;
      if (m.stage === "assigned" || m.stage === "working") {
        var desk = DESK_POSITIONS[m.agentId];
        // Guvenli geri-dusme: agent_id bilinen bir masaya karsilik
        // GELMIYORSA (v0'da gerceklesmez -- task.lifecycle'in agent_id'si
        // her zaman orchestrator -- ama fabrike bir varsayim YAPILMAZ)
        // isaretci kuyrukta KALIR.
        target = desk ? { x: desk.x + 24, y: desk.y - 24 } : TASK_QUEUE_POS;
      } else if (m.stage === "completed") {
        var idx = completedIds.indexOf(id);
        target = { x: TASK_COMPLETED_TRAY_POS.x, y: TASK_COMPLETED_TRAY_POS.y + idx * TASK_MARKER_SPACING };
      } else {
        target = TASK_QUEUE_POS;
      }
      m.targetX = target.x;
      m.targetY = target.y;
    });
  };

  // Iz sayisi MAX_TASK_MARKERS'i asarsa, en eski TAMAMLANMIS isaretciler
  // (seq sirasina gore) silinir -- aktif (kuyrukta/atandi/calisiyor)
  // isaretciler ASLA silinmez (v0 bellek siniri, bkz. ADR-023).
  OpsSuiteScene.prototype._evictOldTaskMarkers = function () {
    var self = this;
    var ids = Object.keys(this.taskMarkers);
    if (ids.length <= MAX_TASK_MARKERS) {
      return;
    }
    var completedIds = ids.filter(function (id) { return self.taskMarkers[id].stage === "completed"; });
    completedIds.sort(function (a, b) { return self.taskMarkers[a].seq - self.taskMarkers[b].seq; });
    while (Object.keys(self.taskMarkers).length > MAX_TASK_MARKERS && completedIds.length) {
      delete self.taskMarkers[completedIds.shift()];
    }
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
    // B048 -- gorev isaretcileri de AYNI enterpolasyon dongusuyle hedefe yaklasir.
    Object.keys(this.taskMarkers).forEach(function (id) {
      var m = self.taskMarkers[id];
      m.x = lerp(m.x, m.targetX, LERP_SPEED);
      m.y = lerp(m.y, m.targetY, LERP_SPEED);
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

    ctx.fillText("Görev Kuyruğu", TASK_QUEUE_POS.x, TASK_QUEUE_POS.y + 20);
    ctx.fillText("Tamamlanan Görevler", TASK_COMPLETED_TRAY_POS.x, TASK_COMPLETED_TRAY_POS.y - 16);
  };

  // B048 -- her gorev isaretcisini kucuk bir renkli nokta olarak cizer.
  // "working" asamasindaki isaretciler GORSEL olarak nabiz atar (yalnizca
  // estetik -- debugState()'te test edilen GERCEK veri stage/pozisyondur,
  // piksel-tam nabiz genligi DEGIL).
  OpsSuiteScene.prototype._drawTaskMarkers = function (ctx) {
    var self = this;
    Object.keys(this.taskMarkers).forEach(function (id) {
      var m = self.taskMarkers[id];
      var color = TASK_STAGE_COLORS[m.stage] || TASK_STAGE_COLORS.queued;
      var radius = 6;
      if (m.stage === "working") {
        radius = 6 + 2 * Math.abs(Math.sin(Date.now() / 200));
      }
      ctx.beginPath();
      ctx.fillStyle = color;
      ctx.arc(m.x, m.y, radius, 0, Math.PI * 2);
      ctx.fill();
    });
  };

  OpsSuiteScene.prototype._drawAgent = function (ctx, agent, agentId) {
    var color = AGENT_STATE_COLORS[agent.state] || AGENT_STATE_COLORS.offline;
    var isGhost = agent.zone === "ghost";
    var radius = isGhost ? 14 : 18;
    ctx.beginPath();
    ctx.globalAlpha = isGhost ? 0.35 : 1.0;
    ctx.fillStyle = color;
    ctx.arc(agent.x, agent.y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1.0;

    // B047 -- sprite varsa cizilir; YOKSA (henuz yuklenmedi/basarisiz)
    // MEVCUT baş-harf render'ina GERI DUSER -- sessizce bos BIRAKILMAZ.
    var sprite = this._spriteFor(isGhost ? "ghost" : agentId);
    if (sprite) {
      var size = radius * 1.3;
      ctx.globalAlpha = isGhost ? 0.55 : 1.0;
      ctx.drawImage(sprite, agent.x - size / 2, agent.y - size / 2, size, size);
      ctx.globalAlpha = 1.0;
    } else {
      ctx.fillStyle = "#0b1220";
      ctx.font = "10px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText((agent.display_name || "?").slice(0, 2).toUpperCase(), agent.x, agent.y + 3);
    }

    ctx.fillStyle = isGhost ? "#5a6580" : "#e6ecf5";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
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

    var sprite = this._spriteFor("assistant");
    if (sprite) {
      var size = radius * 1.3;
      ctx.drawImage(sprite, this.assistant.x - size / 2, this.assistant.y - size / 2, size, size);
    } else {
      ctx.fillStyle = "#0b1220";
      ctx.font = "10px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Ez", this.assistant.x, this.assistant.y + 3);
    }

    ctx.fillStyle = "#e6ecf5";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(this.assistant.state, this.assistant.x, this.assistant.y + radius + 14);
  };

  // B049 (PLAN.md T42) -- canvas-ici koordinatta (x,y) hangi varlik var,
  // varsa doner (`{kind, id}`), YOKSA `null`. Asistan + TUM ajanlar
  // (hayalet raftakiler DAHIL) tiklanabilir.
  OpsSuiteScene.prototype._hitTestAt = function (canvasX, canvasY) {
    var assistantRadius = this.assistant.state === "speaking" ? 26 : 20;
    if (distance(canvasX, canvasY, this.assistant.x, this.assistant.y) <= assistantRadius) {
      return { kind: "assistant", id: "assistant" };
    }
    var ids = Object.keys(this.agents);
    for (var i = 0; i < ids.length; i++) {
      var a = this.agents[ids[i]];
      var r = a.zone === "ghost" ? 14 : 18;
      if (distance(canvasX, canvasY, a.x, a.y) <= r) {
        return { kind: "agent", id: ids[i] };
      }
    }
    return null;
  };

  // Gercek bir DOM `click` olayina baglanir; canvas'in EKRANDAKI boyutu
  // ile ICTEKI cizim cozunurlugu FARKLI olabilecegi icin (bkz. CSS
  // `max-width`), tiklama noktasi canvas-ici koordinata OLCEKLENEREK
  // cevrilir -- yalnizca 1:1 gorunum icin dogru varsayimlar YAPILMAZ.
  OpsSuiteScene.prototype._bindClickHandler = function () {
    var self = this;
    if (!this.canvas || typeof this.canvas.addEventListener !== "function") {
      return;
    }
    this.canvas.addEventListener("click", function (event) {
      var rect = self.canvas.getBoundingClientRect();
      var scaleX = self.canvas.width / rect.width;
      var scaleY = self.canvas.height / rect.height;
      var canvasX = (event.clientX - rect.left) * scaleX;
      var canvasY = (event.clientY - rect.top) * scaleY;
      var hit = self._hitTestAt(canvasX, canvasY);
      if (hit && typeof self.onEntityClick === "function") {
        self.onEntityClick(hit);
      }
    });
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
      self._drawAgent(ctx, self.agents[id], id);
    });
    this._drawAssistant(ctx);
    this._drawApprovalTray(ctx);
    this._drawTaskMarkers(ctx);
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
    var spritesOut = {};
    Object.keys(this._sprites).forEach(function (key) {
      spritesOut[key] = this._sprites[key].status;
    }, this);
    var taskMarkersOut = {};
    Object.keys(this.taskMarkers).forEach(function (id) {
      var m = this.taskMarkers[id];
      taskMarkersOut[id] = {
        stage: m.stage,
        agent_id: m.agentId,
        lifecycle_state: m.lifecycleState,
        x: Math.round(m.x),
        y: Math.round(m.y),
        target_x: Math.round(m.targetX),
        target_y: Math.round(m.targetY),
        at_rest_position: Math.round(m.x) === Math.round(m.targetX) && Math.round(m.y) === Math.round(m.targetY),
      };
    }, this);
    return {
      assistant_state: this.assistant.state,
      pending_approval_count: this.pendingApprovalCount,
      agents: agentsOut,
      sprites: spritesOut, // B047 -- test edilebilirlik: hangi varliklar GERCEKTEN yuklendi/basarisiz oldu
      task_markers: taskMarkersOut, // B048 -- test edilebilirlik: her gorevin GERCEK asama/pozisyonu
    };
  };

  window.OpsSuiteScene = OpsSuiteScene;
})();
