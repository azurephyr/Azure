/**
 * WebSocket client for Azure Operating Platform.
 *
 * - Connects to /ws?token=<jwt>
 * - Exponential backoff reconnect with jitter
 * - Periodic keepalive pings (30s)
 * - Dispatches CustomEvents for each incoming message type
 * - Updates sidebar connection-status indicator
 *
 * Usage:
 *   AzureWS.connect(token);
 *   AzureWS.disconnect();
 *   document.addEventListener("azure:ws:message", (e) => { e.detail });
 *   document.addEventListener("azure:ws:discord_message", (e) => { e.detail.data });
 */
const AzureWS = (() => {
    let _ws = null;
    let _token = null;
    let _intentionalClose = false;
    let _reconnectTimer = null;
    let _pingTimer = null;
    let _reconnectDelay = 1000;
    const MAX_DELAY = 30000;
    const MIN_DELAY = 1000;
    const PING_INTERVAL = 30000;

    /* ── helpers ──────────────────────────────────────────── */
    function _setStatus(state, label) {
        // Update the canonical indicator if present
        const el = document.getElementById("ws-indicator") || document.getElementById("ws-ind");
        if (!el) return;
        el.className = el.className.replace(/\b(online|offline)\b/g, "").trim();
        el.classList.add(state);
        // Support both textContent and innerHTML styles
        const dot = el.querySelector(".dot");
        if (dot) {
            el.innerHTML = "";
            el.appendChild(dot);
            el.appendChild(document.createTextNode(" " + label));
        } else {
            el.textContent = label;
        }
    }

    function _dispatch(name, detail) {
        document.dispatchEvent(new CustomEvent(name, { detail }));
    }

    function _startPing() {
        _stopPing();
        _pingTimer = setInterval(() => {
            if (_ws && _ws.readyState === WebSocket.OPEN) {
                try { _ws.send(JSON.stringify({ action: "ping" })); } catch (_) {}
            }
        }, PING_INTERVAL);
    }

    function _stopPing() {
        if (_pingTimer) { clearInterval(_pingTimer); _pingTimer = null; }
    }

    /* ── core WS ─────────────────────────────────────────── */
    function connect(token) {
        if (!token) return;
        _token = token;

        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${proto}//${location.host}/ws?token=${encodeURIComponent(token)}`;

        try {
            if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) {
                _intentionalClose = true;
                _ws.close();
            }
        } catch (_) {}

        _intentionalClose = false;
        _ws = new WebSocket(url);

        _ws.onopen = () => {
            _reconnectDelay = MIN_DELAY;
            _setStatus("online", "Live Sync Active");
            _dispatch("azure:ws:connected", {});
            _startPing();
        };

        _ws.onerror = () => {
            _setStatus("offline", "Live Sync Error");
        };

        _ws.onclose = () => {
            _stopPing();
            _setStatus("offline", _intentionalClose ? "Disconnected" : "Reconnecting...");
            _dispatch("azure:ws:disconnected", { intentional: _intentionalClose });
            if (!_intentionalClose && _token) {
                // Exponential backoff with jitter
                const jitter = Math.random() * 1000;
                _reconnectTimer = setTimeout(() => {
                    _reconnectDelay = Math.min(_reconnectDelay * 2, MAX_DELAY);
                    connect(_token);
                }, _reconnectDelay + jitter);
            }
        };

        _ws.onmessage = (ev) => {
            let msg;
            try { msg = JSON.parse(ev.data); } catch (_) { return; }

            if (msg.action === "pong") return;

            _dispatch("azure:ws:message", msg);

            if (msg.type) {
                const safeType = msg.type.toLowerCase().replace(/[^a-z0-9]+/g, "_");
                _dispatch("azure:ws:" + safeType, msg);
            }
        };
    }

    function disconnect() {
        _intentionalClose = true;
        _stopPing();
        if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
        if (_ws) { try { _ws.close(); } catch (_) {} _ws = null; }
        _setStatus("offline", "Disconnected");
    }

    function send(data) {
        if (_ws && _ws.readyState === WebSocket.OPEN) {
            _ws.send(JSON.stringify(data));
        }
    }

    function isConnected() {
        return _ws && _ws.readyState === WebSocket.OPEN;
    }

    return { connect, disconnect, send, isConnected };
})();
