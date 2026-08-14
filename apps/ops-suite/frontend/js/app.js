// EzoEzgi Ops Suite v0 -- Command Center shell mantigi. Sayfa yuklendiginde
// REST ile ilk durumu ceker, sonra WebSocket'e abone olup canli gunceller.
// Gorsel/etkilesimli dogruluk artik GERCEK bir tarayicida (Playwright,
// bkz. apps/ops-suite/e2e/, BACKLOG.md B039/B038) test EDILIYOR --
// `node --check` yalnizca sozdizimini dogrular, render/etkilesimi DEGIL.

(function () {
  "use strict";

  var agentGrid = document.getElementById("agent-grid");
  var approvalList = document.getElementById("approval-list");
  var eventFeed = document.getElementById("event-feed");
  var assistantState = document.getElementById("assistant-state");
  var assistantUtterance = document.getElementById("assistant-utterance");
  var wsStatusEl = document.getElementById("ws-status");
  var voiceForm = document.getElementById("voice-form");
  var voiceInput = document.getElementById("voice-input");
  var tokenForm = document.getElementById("token-form");
  var tokenInput = document.getElementById("token-input");
  var whoamiEl = document.getElementById("whoami");
  var sceneCanvas = document.getElementById("office-scene");
  var detailPanel = document.getElementById("agent-detail-panel");
  var detailName = document.getElementById("agent-detail-name");
  var detailState = document.getElementById("agent-detail-state");
  var detailTask = document.getElementById("agent-detail-task");
  var detailHeartbeat = document.getElementById("agent-detail-heartbeat");
  var detailDetail = document.getElementById("agent-detail-detail");
  var detailScope = document.getElementById("agent-detail-scope");
  var detailApprovalLink = document.getElementById("agent-detail-approval-link");
  var detailCloseBtn = document.getElementById("agent-detail-close");

  var MAX_FEED_ITEMS = 50;
  var TOKEN_STORAGE_KEY = "ops_suite_access_token";

  // B049 (PLAN.md T42) -- son cekilen ajan/onay listeleri, tiklama
  // panelinin capraz-referans kurabilmesi icin burada tutulur (app.js
  // KENDI sorumlulugu -- scene.js bu VERININ VARLIGINDAN HABERSIZDIR).
  var lastAgents = [];
  var lastApprovals = [];
  var lastClickedApprovalRequestId = null;

  // B038 -- animasyonlu ofis sahnesi (bkz. scene.js). `OpsSuiteScene`
  // henuz yuklenmemis/canvas desteklenmiyorsa (cok kucultulmus bir
  // checkout, eski tarayici) SESSIZCE atlanir -- geri kalan uygulama
  // (kart tabanli paneller) sahneye BAGIMLI DEGILDIR.
  var scene = (sceneCanvas && window.OpsSuiteScene) ? new window.OpsSuiteScene(sceneCanvas) : null;
  if (scene) {
    scene.start();
    scene.onEntityClick = openAgentDetailPanel;
    // PLAN.md T35 -- Playwright'in gercek bir tarayicida cagirabilecegi
    // deterministik durum koprusu (Canvas pikselleri DOM'dan GORULEMEZ).
    window.__ops_suite_scene_debug__ = function () {
      return scene.debugState();
    };
  }

  // B049 -- bir ajan/asistan tiklandiginda cagirilir (bkz.
  // `scene.js::onEntityClick`). Panel yalnizca GERCEK, o an bilinen
  // veriyi gosterir -- hicbir alan fabrike EDILMEZ.
  function openAgentDetailPanel(hit) {
    if (hit.kind === "assistant") {
      detailName.textContent = "EzoEzgi (Asistan)";
      detailState.textContent = assistantState.textContent || "—";
      detailTask.textContent = "—";
      detailHeartbeat.textContent = "—";
      detailDetail.textContent = assistantUtterance.textContent || "—";
      detailScope.textContent = "Asistanın kendisi bir yetki kapsamı taşımaz -- yetki/scope yalnızca insan kimliklerine (owner/delegate) aittir, bkz. IDENTITY_AND_DELEGATION_POLICY.md.";
      detailApprovalLink.hidden = true;
      detailPanel.hidden = false;
      return;
    }

    var agent = lastAgents.filter(function (a) { return a.agent_id === hit.id; })[0];
    if (!agent) {
      return;
    }
    detailName.textContent = agent.display_name + " (" + agent.agent_id + ")";
    detailState.textContent = agent.state;
    detailTask.textContent = agent.last_task_id || "—";
    detailHeartbeat.textContent = agent.last_heartbeat_ts || "—";
    detailDetail.textContent = agent.detail || "—";
    detailScope.textContent = "Ajanların kendi bir yetki kapsamı (authority scope) YOKTUR -- bu bir insan-kimlik kavramıdır (owner/delegate), bkz. IDENTITY_AND_DELEGATION_POLICY.md. Uydurulmuş bir kapsam GÖSTERİLMEZ.";

    // B049 -- bu ajanin SON gorevi, bekleyen bir onay kaydiyla eslesiyorsa
    // (agent.state'in kendisi DEGIL -- "awaiting_approval" ajan-durumu
    // v0'da GERCEKTE hic uretilmiyor, bkz. PLAN.md T42 notu) bir
    // bagliyi/vurguyu goster.
    var matchingApproval = agent.last_task_id
      ? lastApprovals.filter(function (p) { return p.request_id === agent.last_task_id; })[0]
      : null;
    if (matchingApproval) {
      lastClickedApprovalRequestId = matchingApproval.request_id;
      detailApprovalLink.hidden = false;
    } else {
      lastClickedApprovalRequestId = null;
      detailApprovalLink.hidden = true;
    }

    detailPanel.hidden = false;
  }

  detailCloseBtn.addEventListener("click", function () {
    detailPanel.hidden = true;
  });

  detailApprovalLink.addEventListener("click", function () {
    if (!lastClickedApprovalRequestId) {
      return;
    }
    var item = approvalList.querySelector('[data-request-id="' + lastClickedApprovalRequestId + '"]');
    if (item) {
      item.scrollIntoView({ behavior: "smooth", block: "center" });
      item.classList.add("approval-item--highlighted");
      window.setTimeout(function () {
        item.classList.remove("approval-item--highlighted");
      }, 2000);
    }
  });

  function apiUrl(path) {
    return path; // ayni-origin: FastAPI hem API'yi hem frontend'i sunuyor
  }

  // B044 -- onay/red uc noktalari artik kimlik dogrulama ISTIYOR
  // (Authorization: Bearer <token>). Token yalnizca bu tarayicinin
  // localStorage'inda tutulur, hicbir zaman sunucuya baska bir yolla
  // gonderilmez/loglanmaz.
  function getToken() {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY) || "";
  }

  function authHeaders() {
    var token = getToken();
    return token ? { Authorization: "Bearer " + token } : {};
  }

  function refreshWhoami() {
    var token = getToken();
    if (!token) {
      whoamiEl.textContent = "kimlik doğrulanmadı";
      whoamiEl.className = "whoami whoami--anon";
      return;
    }
    fetch(apiUrl("/api/whoami"), { headers: authHeaders() })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("HTTP " + r.status);
        }
        return r.json();
      })
      .then(function (identity) {
        whoamiEl.textContent = identity.display_name + " (" + identity.authority_source + ")";
        whoamiEl.className = "whoami whoami--ok";
      })
      .catch(function () {
        whoamiEl.textContent = "token geçersiz";
        whoamiEl.className = "whoami whoami--error";
      });
  }

  tokenForm.addEventListener("submit", function (event) {
    event.preventDefault();
    window.localStorage.setItem(TOKEN_STORAGE_KEY, tokenInput.value.trim());
    tokenInput.value = "";
    refreshWhoami();
  });

  function renderAgents(agents) {
    lastAgents = agents; // B049 -- tiklama panelinin capraz-referansi icin
    if (scene) {
      scene.setAgents(agents);
    }
    agentGrid.innerHTML = "";
    agents.forEach(function (agent) {
      var card = document.createElement("div");
      card.className = "agent-card";
      card.innerHTML =
        '<div class="agent-card__name">' + escapeHtml(agent.display_name) + "</div>" +
        '<div class="agent-card__state agent-card__state--' + escapeHtml(agent.state) + '">' + escapeHtml(agent.state) + "</div>";
      if (agent.detail) {
        var detail = document.createElement("div");
        detail.className = "agent-card__detail";
        detail.textContent = agent.detail;
        card.appendChild(detail);
      }
      agentGrid.appendChild(card);
    });
  }

  function renderApprovals(entries) {
    lastApprovals = entries; // B049 -- tiklama panelinin capraz-referansi icin
    if (scene) {
      scene.setPendingApprovalCount(entries.length);
    }
    approvalList.innerHTML = "";
    if (entries.length === 0) {
      var empty = document.createElement("li");
      empty.textContent = "Onay bekleyen görev yok.";
      approvalList.appendChild(empty);
      return;
    }
    entries.forEach(function (entry) {
      var item = document.createElement("li");
      item.className = "approval-item";
      item.dataset.requestId = entry.request_id;

      var label = document.createElement("span");
      label.textContent = (entry.task || "?") + " [" + (entry.risk_level || "?") + "] -- " + (entry.original_tr || "");
      item.appendChild(label);

      var actions = document.createElement("span");
      actions.className = "approval-item__actions";

      var approveBtn = document.createElement("button");
      approveBtn.className = "approve";
      approveBtn.textContent = "Onayla";
      approveBtn.addEventListener("click", function () {
        decide(entry.request_id, "approve");
      });

      var rejectBtn = document.createElement("button");
      rejectBtn.className = "reject";
      rejectBtn.textContent = "Reddet";
      rejectBtn.addEventListener("click", function () {
        decide(entry.request_id, "reject");
      });

      actions.appendChild(approveBtn);
      actions.appendChild(rejectBtn);
      item.appendChild(actions);

      approvalList.appendChild(item);
    });
  }

  function decide(requestId, action) {
    var headers = { "Content-Type": "application/json" };
    Object.keys(authHeaders()).forEach(function (key) {
      headers[key] = authHeaders()[key];
    });
    fetch(apiUrl("/api/approvals/" + encodeURIComponent(requestId) + "/" + action), {
      method: "POST",
      headers: headers,
      body: JSON.stringify({}),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (body) {
            throw new Error("HTTP " + r.status + ": " + (body.detail || "bilinmeyen hata"));
          });
        }
        refreshApprovals();
      })
      .catch(function (err) {
        console.error("ops-suite: karar gonderilemedi", err);
        window.alert("Karar gönderilemedi: " + err.message);
      });
  }

  function appendFeedItem(envelope) {
    var item = document.createElement("li");
    var topicSpan = document.createElement("span");
    topicSpan.className = "topic";
    topicSpan.textContent = envelope.topic;
    item.appendChild(topicSpan);
    item.appendChild(document.createTextNode(" -- " + JSON.stringify(envelope.payload)));
    eventFeed.insertBefore(item, eventFeed.firstChild);
    while (eventFeed.children.length > MAX_FEED_ITEMS) {
      eventFeed.removeChild(eventFeed.lastChild);
    }
  }

  function renderAssistant(presence) {
    if (scene) {
      scene.setAssistant(presence);
    }
    assistantState.textContent = presence.state;
    assistantUtterance.textContent = presence.utterance_tr || "";
  }

  function escapeHtml(value) {
    var div = document.createElement("div");
    div.textContent = String(value);
    return div.innerHTML;
  }

  function refreshAgents() {
    fetch(apiUrl("/api/agents")).then(function (r) { return r.json(); }).then(renderAgents);
  }

  function refreshApprovals() {
    fetch(apiUrl("/api/approvals")).then(function (r) { return r.json(); }).then(renderApprovals);
  }

  function refreshAssistant() {
    fetch(apiUrl("/api/assistant")).then(function (r) { return r.json(); }).then(renderAssistant);
  }

  function handleLiveEvent(envelope) {
    appendFeedItem(envelope);
    if (envelope.topic === "assistant.presence") {
      renderAssistant(envelope.payload);
    } else if (envelope.topic === "approval.queue") {
      refreshApprovals();
    } else if (envelope.topic === "task.lifecycle") {
      refreshAgents();
      // B048 (BACKLOG.md B048, PLAN.md T45) -- sahnenin (varsa) gorev
      // isaretcisini bu GERCEK olayla GUNCELLER -- ONCEDEN bu payload
      // burada tamamen ATILIYORDU (yalnizca refreshAgents() cagriliyordu).
      if (scene) {
        scene.applyTaskLifecycleEvent(envelope.payload);
      }
    } else if (envelope.topic === "agent.presence") {
      // T38 (BACKLOG.md B046) -- sahneyi (varsa) DOGRUDAN bu tek WS
      // mesajiyla gunceller, bir sonraki `GET /api/agents` polling'ini
      // BEKLEMEDEN -- `working` gibi kisa omurlu durumlar boylece
      // GERCEKTEN render edilebiliyor (T37 yalnizca olayi
      // YAYINLIYORDU, burasi onu TUKETIYOR). Kart-tabanli `#agent-grid`
      // panel HALA yalnizca `task.lifecycle` sonrasi REST ile
      // guncellenir (degismedi) -- bu, yalnizca Canvas sahnesine ozel
      // ek bir gercek-zamanlilik katmanidir.
      if (scene) {
        scene.applyAgentPresenceEvent(envelope.payload);
      }
    }
  }

  function setWsStatus(status) {
    wsStatusEl.className = "ws-status ws-status--" + status;
    wsStatusEl.textContent = status === "open" ? "bağlı" : status === "connecting" ? "bağlanıyor…" : "bağlantı koptu, yeniden deneniyor…";
  }

  voiceForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = voiceInput.value.trim();
    if (!text) {
      return;
    }
    fetch(apiUrl("/api/voice/command"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input_tr: text }),
    })
      .then(function (r) { return r.json(); })
      .then(function () {
        voiceInput.value = "";
        refreshApprovals();
      })
      .catch(function (err) {
        console.error("ops-suite: sesli komut gonderilemedi", err);
      });
  });

  refreshAgents();
  refreshApprovals();
  refreshAssistant();
  refreshWhoami();

  var wsClient = new OpsSuiteWSClient("/ws/live", handleLiveEvent, setWsStatus);
  wsClient.connect();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/service-worker.js").catch(function (err) {
      console.warn("ops-suite: service worker kaydi basarisiz", err);
    });
  }
})();
