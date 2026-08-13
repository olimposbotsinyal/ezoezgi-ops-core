// EzoEzgi Ops Suite v0 -- WebSocket istemcisi, otomatik yeniden baglanma
// (ustel geri-cekilme) ile. Framework/bundler YOK (bkz. DECISIONS.md
// ADR-018) -- saf tarayici API'leri (WebSocket, fetch).

(function (global) {
  "use strict";

  function OpsSuiteWSClient(path, onEvent, onStatusChange) {
    this._path = path;
    this._onEvent = onEvent;
    this._onStatusChange = onStatusChange || function () {};
    this._socket = null;
    this._retryDelayMs = 500;
    this._maxRetryDelayMs = 10000;
    this._closedByUser = false;
  }

  OpsSuiteWSClient.prototype._wsUrl = function () {
    var protocol = global.location.protocol === "https:" ? "wss:" : "ws:";
    return protocol + "//" + global.location.host + this._path;
  };

  OpsSuiteWSClient.prototype.connect = function () {
    this._closedByUser = false;
    this._onStatusChange("connecting");
    var self = this;
    var socket = new WebSocket(this._wsUrl());
    this._socket = socket;

    socket.onopen = function () {
      self._retryDelayMs = 500;
      self._onStatusChange("open");
    };

    socket.onmessage = function (messageEvent) {
      var envelope;
      try {
        envelope = JSON.parse(messageEvent.data);
      } catch (err) {
        console.warn("ops-suite ws: gecersiz JSON, atlandi", err);
        return;
      }
      self._onEvent(envelope);
    };

    socket.onclose = function () {
      self._onStatusChange("closed");
      if (!self._closedByUser) {
        setTimeout(function () {
          self.connect();
        }, self._retryDelayMs);
        self._retryDelayMs = Math.min(self._retryDelayMs * 2, self._maxRetryDelayMs);
      }
    };

    socket.onerror = function () {
      socket.close();
    };
  };

  OpsSuiteWSClient.prototype.close = function () {
    this._closedByUser = true;
    if (this._socket) {
      this._socket.close();
    }
  };

  global.OpsSuiteWSClient = OpsSuiteWSClient;
})(window);
