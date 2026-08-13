// EzoEzgi Ops Suite v0 -- Command Center shell mantigi. Sayfa yuklendiginde
// REST ile ilk durumu ceker, sonra WebSocket'e abone olup canli gunceller.
// Gorsel/etkilesimli dogrulugu bu ortamda TARAYICI OLMADIGI icin test
// EDILEMEDI (SKIPPED, bkz. docs/RUNBOOK.md "Ops Suite -- Calistirma ve
// Dogrulama") -- yalnizca `node --check` ile sozdizimi dogrulandi.

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

  var MAX_FEED_ITEMS = 50;
  var TOKEN_STORAGE_KEY = "ops_suite_access_token";

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
