document.addEventListener('DOMContentLoaded', () => {
    let authToken = localStorage.getItem('azure_token');
    let ws = null;
    let healthPollTimer = null;
    let logsPollTimer = null;
    let analyticsPollTimer = null;
    let wsReconnectTimer = null;
    let tokenRefreshTimer = null;
    let intentionalWsClose = false;
    let startTime = Date.now();

    const TOKEN_CHECK_INTERVAL = 5 * 60 * 1000;
    const TOKEN_REFRESH_BEFORE_EXPIRY = 24 * 60 * 60 * 1000;

    const loginView = document.getElementById('login-view');
    const dashView = document.getElementById('dashboard-view');
    const loginBtn = document.getElementById('btn-login');
    const logoutBtn = document.getElementById('btn-logout');
    const errorMsg = document.getElementById('login-error');

    const escapeHtml = (value) => {
        if (value == null) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    };

    const setText = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.innerText = text;
    };

    // Initialize shared navigation
    AzureNav.init();

    // Listen for navigation events from AzureNav
    document.addEventListener('azure:navigate', (e) => {
        const panelId = e.detail.panelId;
        const panels = document.querySelectorAll('.panel');
        const title = document.getElementById('current-panel-title');

        panels.forEach(p => p.classList.remove('active'));
        const target = document.getElementById(panelId);
        if (target) target.classList.add('active');

        const page = AzureNav.PAGES.find(p => p.id === panelId);
        if (title && page) title.innerText = page.label;

        // Load data for the newly visible panel
        if (panelId === 'panel-analytics') fetchAnalytics();
        if (panelId === 'panel-users') fetchUsers();
    });

    // Auth Logic
    const checkAuth = async () => {
        if (authToken) {
            const valid = await validateToken();
            if (!valid) {
                loginView.classList.add('active');
                dashView.classList.remove('active');
                stopDashboard();
                return;
            }
            loginView.classList.remove('active');
            dashView.classList.add('active');
            initDashboard();
        } else {
            loginView.classList.add('active');
            dashView.classList.remove('active');
            stopDashboard();
        }
    };

    const stopDashboard = () => {
        if (healthPollTimer) { clearInterval(healthPollTimer); healthPollTimer = null; }
        if (logsPollTimer) { clearInterval(logsPollTimer); logsPollTimer = null; }
        if (analyticsPollTimer) { clearInterval(analyticsPollTimer); analyticsPollTimer = null; }
        if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
        stopTokenRefresh();
        intentionalWsClose = true;
        if (ws) { try { ws.close(); } catch (_) {} ws = null; }
    };

    loginBtn.addEventListener('click', async () => {
        const u = document.getElementById('username').value;
        const p = document.getElementById('password').value;
        errorMsg.innerText = '';

        try {
            const formData = new URLSearchParams();
            formData.append('username', u);
            formData.append('password', p);

            const res = await fetch('/api/auth/token', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: formData
            });

            if (!res.ok) {
                let detail = 'Invalid credentials';
                try {
                    const errBody = await res.json();
                    if (errBody && errBody.detail) detail = errBody.detail;
                } catch (_) {}
                throw new Error(detail);
            }

            const data = await res.json();
            authToken = data.access_token;
            localStorage.setItem('azure_token', authToken);
            if (data.expires_at) {
                localStorage.setItem('azure_token_expires', String(data.expires_at));
            }
            if (data.role) {
                const badge = document.getElementById('user-role-badge');
                if (badge) badge.innerText = data.role;
            }
            AzureNav.addNotification('Logged in successfully', 'success');
            checkAuth();
        } catch (e) {
            errorMsg.innerText = e.message || 'Login failed';
        }
    });

    ['username', 'password'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') loginBtn.click(); });
    });

    logoutBtn.addEventListener('click', () => {
        authToken = null;
        localStorage.removeItem('azure_token');
        localStorage.removeItem('azure_token_expires');
        stopDashboard();
        checkAuth();
    });

    // Mobile hamburger
    const hamburger = document.getElementById('hamburger-btn');
    const mobileOverlay = document.getElementById('mobile-overlay');
    if (hamburger) hamburger.addEventListener('click', AzureNav.toggleMobile);
    if (mobileOverlay) mobileOverlay.addEventListener('click', AzureNav.toggleMobile);

    // Search modal close on backdrop click
    const searchModal = document.getElementById('search-modal');
    if (searchModal) {
        searchModal.addEventListener('click', (e) => {
            if (e.target === searchModal) AzureNav.toggleSearch();
        });
    }

    // Token validation and refresh
    async function validateToken() {
        if (!authToken) return false;
        try {
            const res = await fetch('/api/auth/me', {
                headers: { 'Authorization': `Bearer ${authToken}` }
            });
            if (res.status === 401) {
                authToken = null;
                localStorage.removeItem('azure_token');
                localStorage.removeItem('azure_token_expires');
                return false;
            }
            return res.ok;
        } catch {
            return false;
        }
    }

    async function tryRefreshToken() {
        if (!authToken) return;
        const expiresAt = parseInt(localStorage.getItem('azure_token_expires') || '0', 10);
        if (!expiresAt) return;
        const now = Math.floor(Date.now() / 1000);
        const remaining = expiresAt - now;
        if (remaining > 86400) return;
        try {
            const res = await fetch('/api/auth/refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: authToken })
            });
            if (res.ok) {
                const data = await res.json();
                if (data.access_token && data.access_token !== authToken) {
                    authToken = data.access_token;
                    localStorage.setItem('azure_token', authToken);
                    if (data.expires_at) {
                        localStorage.setItem('azure_token_expires', String(data.expires_at));
                    }
                }
            }
        } catch {
            // Silent fail — next check will catch expiry
        }
    }

    function startTokenRefresh() {
        stopTokenRefresh();
        tokenRefreshTimer = setInterval(tryRefreshToken, TOKEN_CHECK_INTERVAL);
    }

    function stopTokenRefresh() {
        if (tokenRefreshTimer) { clearInterval(tokenRefreshTimer); tokenRefreshTimer = null; }
    }

    // Dashboard Initialization
    const initDashboard = () => {
        stopDashboard();
        intentionalWsClose = false;
        fetchHealth();
        fetchLogs();
        fetchServers();
        initWebSocket();
        updateFooterUptime();
        startTokenRefresh();
        healthPollTimer = setInterval(fetchHealth, 10000);
        logsPollTimer = setInterval(fetchLogs, 15000);
        analyticsPollTimer = setInterval(updateFooterUptime, 1000);
    };

    // Footer uptime updater
    const updateFooterUptime = () => {
        const sec = Math.floor((Date.now() - startTime) / 1000);
        AzureNav.updateFooter(sec, startTime);
    };

    // API helpers
    const apiGet = async (endpoint) => {
        const res = await fetch(endpoint, {
            headers: { 'Authorization': `Bearer ${authToken}` }
        });
        if (res.status === 401) { logoutBtn.click(); throw new Error('Unauthorized'); }
        if (!res.ok) {
            let detail = `Request failed (${res.status})`;
            try { const body = await res.json(); if (body && body.detail) detail = body.detail; } catch (_) {}
            throw new Error(detail);
        }
        return res.json();
    };

    const apiPost = async (endpoint, data) => {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data || {})
        });
        if (res.status === 401) { logoutBtn.click(); throw new Error('Unauthorized'); }
        if (!res.ok) {
            let detail = `Request failed (${res.status})`;
            try { const body = await res.json(); if (body && body.detail) detail = body.detail; } catch (_) {}
            throw new Error(detail);
        }
        return res.json();
    };

    // Health Panel
    const fetchHealth = async () => {
        try {
            const data = await apiGet('/api/health/detailed');
            const db = data.database || {};
            const agent = data.agent || {};
            const system = data.system || {};
            setText('stat-msgs', String(db.total_messages || 0));
            setText('stat-cache', `${((db.cache_hit_rate || 0) * 100).toFixed(1)}%`);
            setText('stat-servers', String(db.peak_servers || 0));
            setText('stat-mem', `${Math.round(system.memory_mb || 0)} MB`);

            setText('ops-health', data.status || 'online');
            setText('ops-memory', `${Math.round(system.memory_mb || 0)} MB`);
            if (db.total_messages != null) setText('ops-tasks', String(system.threads || 0));

            const phaseEl = document.getElementById('select-phase');
            const modeEl = document.getElementById('select-mode');
            if (phaseEl && agent.moderation_phase) phaseEl.value = agent.moderation_phase;
            if (modeEl && agent.moderation_mode) modeEl.value = agent.moderation_mode;
            setText('current-phase-badge', agent.moderation_phase || 'dry_run');

            if (data.uptime_seconds != null) {
                AzureNav.updateFooter(data.uptime_seconds, startTime);
            }
        } catch (e) {
            console.error('fetchHealth', e);
        }
    };

    const fetchServers = async () => {
        try {
            const servers = await apiGet('/api/analytics/servers');
            if (Array.isArray(servers) && servers.length) {
                setText('stat-servers', String(servers.length));
            }
        } catch (e) {
            console.debug('fetchServers', e);
        }
    };

    // Analytics Panel
    const fetchAnalytics = async () => {
        try {
            const servers = await apiGet('/api/analytics/servers');
            const tbody = document.querySelector('#servers-table tbody');
            const emptyRow = document.getElementById('servers-empty');
            if (Array.isArray(servers) && servers.length) {
                if (emptyRow) emptyRow.remove();
                if (tbody) {
                    tbody.innerHTML = servers.map(s => `
                        <tr>
                            <td>${escapeHtml(s.name)}</td>
                            <td>${escapeHtml(String(s.member_count || 0))}</td>
                            <td style="font-family:var(--font-mono);font-size:0.8rem;color:var(--text-tertiary)">${escapeHtml(s.id)}</td>
                        </tr>
                    `).join('');
                }
                setText('analytics-msgs', String(servers.reduce((a, s) => a + (s.member_count || 0), 0)));
            }

            // Try timeseries for charts
            try {
                const ts = await apiGet('/api/analytics/timeseries?hours=24');
                if (ts && ts.labels && ts.labels.length) {
                    setText('analytics-latency', ts.latency.length ? `${(ts.latency.reduce((a, b) => a + b, 0) / ts.latency.length).toFixed(0)}ms` : '--');
                    setText('analytics-tokens', String(ts.tokens.reduce((a, b) => a + b, 0)));
                    setText('analytics-cache', String(ts.cache_hits.reduce((a, b) => a + b, 0)));
                    renderChart(ts);
                }
            } catch (_) {}
        } catch (e) {
            console.error('fetchAnalytics', e);
        }
    };

    const renderChart = (ts) => {
        const container = document.getElementById('analytics-chart');
        if (!container || !ts.messages) return;
        const max = Math.max(...ts.messages, 1);
        container.classList.remove('skeleton', 'skeleton-chart');
        container.style.cssText = 'display:flex;align-items:flex-end;gap:3px;height:120px;padding:16px;';
        container.innerHTML = ts.messages.map((v, i) => {
            const pct = (v / max) * 100;
            const time = ts.labels[i] ? new Date(ts.labels[i]).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
            return `<div style="flex:1;background:linear-gradient(to top,var(--accent-primary),var(--accent-secondary));height:${Math.max(pct, 2)}%;border-radius:3px 3px 0 0;min-width:4px;position:relative;cursor:pointer;" title="${time}: ${v} msgs"></div>`;
        }).join('');
    };

    // Users Panel
    const fetchUsers = async () => {
        try {
            const servers = await apiGet('/api/analytics/servers');
            const tbody = document.querySelector('#users-table tbody');
            if (tbody && Array.isArray(servers) && servers.length) {
                tbody.innerHTML = servers.map(s => `
                    <tr>
                        <td>${escapeHtml(s.name)}</td>
                        <td>${escapeHtml(String(s.member_count || 0))} members</td>
                        <td><span class="badge" style="background:rgba(59,130,246,0.1);color:var(--accent-primary);border:1px solid rgba(59,130,246,0.2)">Server</span></td>
                        <td style="color:var(--text-tertiary)">${escapeHtml(s.created_at ? new Date(s.created_at * 1000).toLocaleDateString() : '--')}</td>
                    </tr>
                `).join('');
            }
        } catch (e) {
            console.error('fetchUsers', e);
        }
    };

    // Moderation Panel
    const fetchLogs = async () => {
        try {
            const logs = await apiGet('/api/moderation/logs');
            const tbody = document.querySelector('#audit-table tbody');
            if (tbody) {
                if (!Array.isArray(logs) || logs.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" class="empty-row">No audit events yet</td></tr>';
                } else {
                    tbody.innerHTML = logs.map(l => `
                        <tr>
                            <td>${escapeHtml(l.timestamp != null ? new Date(l.timestamp * 1000).toLocaleTimeString() : '--')}</td>
                            <td>${escapeHtml(l.user_name || '--')}</td>
                            <td><span class="badge" style="background:rgba(255,255,255,0.1)">${escapeHtml(l.action || '--')}</span></td>
                            <td>${escapeHtml(l.subsystem || l.target || '--')}</td>
                        </tr>
                    `).join('');
                }
            }

            const queue = await apiGet('/api/moderation/queue');
            const qbody = document.querySelector('#queue-table tbody');
            if (qbody) {
                if (!Array.isArray(queue) || queue.length === 0) {
                    qbody.innerHTML = '<tr><td colspan="5" class="empty-row">Queue empty</td></tr>';
                } else {
                    qbody.innerHTML = queue.map(q => {
                        const conf = (typeof q.confidence === 'number') ? `${(q.confidence * 100).toFixed(0)}%` : '--';
                        const mid = escapeHtml(q.message_id || '');
                        return `
                        <tr>
                            <td>${escapeHtml(q.user_name || q.user_id || '--')}</td>
                            <td>${escapeHtml(q.action || q.action_type || '--')}</td>
                            <td>${escapeHtml(q.reason || '--')}</td>
                            <td>${conf}</td>
                            <td>
                                <button class="btn primary small" data-confirm="${mid}">Approve</button>
                                <button class="btn secondary small" data-cancel="${mid}">Deny</button>
                            </td>
                        </tr>`;
                    }).join('');

                    qbody.querySelectorAll('[data-confirm]').forEach((btn) => {
                        btn.addEventListener('click', () => confirmAction(btn.getAttribute('data-confirm')));
                    });
                    qbody.querySelectorAll('[data-cancel]').forEach((btn) => {
                        btn.addEventListener('click', () => cancelAction(btn.getAttribute('data-cancel')));
                    });
                }
            }
        } catch (e) {
            console.error('fetchLogs', e);
        }
    };

    const confirmAction = async (id) => {
        try {
            await apiPost('/api/moderation/confirm', { message_id: id });
            AzureNav.addNotification('Moderation action confirmed', 'success');
            fetchLogs();
        } catch (e) {
            console.error('confirmAction', e);
            AzureNav.addNotification('Confirm failed: ' + e.message, 'error');
        }
    };

    const cancelAction = async (id) => {
        try {
            await apiPost('/api/moderation/cancel', { message_id: id });
            AzureNav.addNotification('Moderation action denied', 'warning');
            fetchLogs();
        } catch (e) {
            console.error('cancelAction', e);
            AzureNav.addNotification('Deny failed: ' + e.message, 'error');
        }
    };

    window.confirmAction = confirmAction;
    window.cancelAction = cancelAction;

    // Config Panel
    const bindConfigButton = (btnId, handler) => {
        const btn = document.getElementById(btnId);
        if (btn) btn.addEventListener('click', handler);
    };

    bindConfigButton('btn-save-phase', async () => {
        try {
            const val = document.getElementById('select-phase').value;
            await apiPost('/api/config/phase', { phase: val });
            AzureNav.addNotification(`Moderation phase set to ${val}`, 'info');
            fetchHealth();
        } catch (e) {
            AzureNav.addNotification('Phase update failed: ' + e.message, 'error');
        }
    });

    bindConfigButton('btn-save-mode', async () => {
        try {
            const val = document.getElementById('select-mode').value;
            await apiPost('/api/config/mode', { mode: val });
            AzureNav.addNotification(`Agent mode set to ${val}`, 'info');
            fetchHealth();
        } catch (e) {
            AzureNav.addNotification('Mode update failed: ' + e.message, 'error');
        }
    });

    bindConfigButton('btn-emergency', async () => {
        if (!confirm('Engage Emergency Stop? Azure will instantly revert to DRY_RUN and stop all enforcement.')) return;
        try {
            await apiPost('/api/config/emergency_stop', {});
            AzureNav.addNotification('EMERGENCY STOP activated!', 'error');
            fetchHealth();
        } catch (e) {
            AzureNav.addNotification('Emergency stop failed: ' + e.message, 'error');
        }
    });

    // Live execution telemetry
    const activeExecutions = new Map();

    const stageIcon = (status) => {
        if (status === 'running') return '\u27F3';
        if (status === 'done' || status === 'success') return '\u2705';
        if (status === 'error') return '\u274C';
        if (status === 'warning') return '\u26A0\uFE0F';
        return '\u2022';
    };

    const formatMs = (ms) => {
        const n = Number(ms) || 0;
        if (n < 1000) return `${n}ms`;
        if (n < 60000) return `${(n / 1000).toFixed(1)}s`;
        const m = Math.floor(n / 60000);
        const s = Math.round((n % 60000) / 1000);
        return `${m}m ${s}s`;
    };

    const renderActiveExecutions = () => {
        const root = document.getElementById('active-executions');
        if (!root) return;

        const now = Date.now();
        for (const [id, snap] of activeExecutions.entries()) {
            if (snap.finished && snap._uiTs && (now - snap._uiTs) > 120000) {
                activeExecutions.delete(id);
            }
        }

        if (activeExecutions.size === 0) {
            root.innerHTML = '<div class="exec-empty" id="exec-empty-state">Waiting for a real request...</div>';
            return;
        }

        const items = Array.from(activeExecutions.values())
            .sort((a, b) => (b._uiTs || 0) - (a._uiTs || 0))
            .slice(0, 8);

        root.innerHTML = items.map((p) => {
            const stages = Array.isArray(p.stages) ? p.stages : [];
            const stageHtml = stages.length
                ? stages.map((s) => `
                    <div class="pipe-stage status-${escapeHtml(s.status || 'info')}">
                        <span class="pipe-icon">${stageIcon(s.status)}</span>
                        <div class="pipe-body">
                            <div class="pipe-label">${escapeHtml(s.label || s.action || 'Step')}</div>
                            <div class="pipe-detail">${escapeHtml(s.detail || '')}</div>
                        </div>
                        <div class="pipe-meta">${escapeHtml(formatMs(s.duration_ms))}</div>
                    </div>
                `).join('')
                : '<div class="pipe-detail">No stages yet...</div>';

            const statusClass = p.finished
                ? (p.finish_status === 'success' ? 'exec-done' : 'exec-error')
                : 'exec-running';

            return `
                <div class="exec-card ${statusClass}" data-exec="${escapeHtml(p.execution_id || '')}">
                    <div class="exec-card-header">
                        <div>
                            <div class="exec-user">${escapeHtml(p.user || '?')} \u00B7 ${escapeHtml(p.guild || '')}</div>
                            <div class="exec-preview">${escapeHtml(p.request_preview || '')}</div>
                        </div>
                        <div class="exec-elapsed">${escapeHtml(p.elapsed_label || formatMs(p.elapsed_ms))}</div>
                    </div>
                    <div class="pipe-stages">${stageHtml}</div>
                </div>
            `;
        }).join('');
    };

    const handleExecutionTelemetry = (msg) => {
        const presentation = msg.presentation || null;
        const event = msg.event || {};
        const execId = msg.execution_id || (presentation && presentation.execution_id) || event.execution_id;

        if (presentation && execId) {
            presentation._uiTs = Date.now();
            presentation.execution_id = execId;
            activeExecutions.set(execId, presentation);
            renderActiveExecutions();
        }

        const feed = document.getElementById('live-telemetry-feed');
        if (!feed) return;

        const status = (event.status || event.level || 'info').toLowerCase();
        const filterStatus = (document.getElementById('exec-filter-status') || {}).value || '';
        const filterUser = ((document.getElementById('exec-filter-user') || {}).value || '').toLowerCase();
        const filterGuild = ((document.getElementById('exec-filter-guild') || {}).value || '').toLowerCase();

        if (filterStatus && status !== filterStatus && (event.action || '').toLowerCase() !== filterStatus) return;
        if (filterUser && !(String(msg.user || '').toLowerCase().includes(filterUser))) return;
        if (filterGuild && !(String(msg.guild || '').toLowerCase().includes(filterGuild))) return;

        if (feed.children.length === 1 && feed.firstChild.classList && feed.firstChild.classList.contains('terminal-line')) {
            const t = feed.firstChild.textContent || '';
            if (t.includes('Waiting for execution')) feed.innerHTML = '';
        }

        const line = document.createElement('div');
        line.className = `terminal-line status-${escapeHtml(status)}`;
        const ts = new Date().toLocaleTimeString();
        const phase = event.action || event.phase || event.step || event.name || 'event';
        const detail = event.message || event.detail || event.description || '';
        line.innerHTML =
            `<span class="prompt">[${escapeHtml(ts)}]</span> ` +
            `<span class="tele-user">${escapeHtml(msg.user || '?')}</span> ` +
            `<span class="tele-phase">${escapeHtml(phase)}</span> ` +
            `<span class="tele-detail">${escapeHtml(detail)}</span>`;
        feed.appendChild(line);

        while (feed.children.length > 200) feed.removeChild(feed.firstChild);
        feed.scrollTop = feed.scrollHeight;
    };

    const updateHealthDashboard = (metrics) => {
        if (!metrics || typeof metrics !== 'object') return;
        if (metrics.memory_mb != null) setText('stat-mem', `${Math.round(metrics.memory_mb)} MB`);
        if (metrics.total_messages != null) setText('stat-msgs', String(metrics.total_messages));
        if (metrics.cache_hit_rate != null) setText('stat-cache', `${(metrics.cache_hit_rate * 100).toFixed(1)}%`);
    };

    const clearBtn = document.getElementById('btn-clear-telemetry');
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            const feed = document.getElementById('live-telemetry-feed');
            if (feed) feed.innerHTML = '<div class="terminal-line"><span class="prompt">azure@core:~$</span> Cleared. Waiting for execution events...</div>';
            activeExecutions.clear();
            renderActiveExecutions();
        });
    }

    // WebSocket
    const initWebSocket = () => {
        if (!authToken) return;

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const tokenQs = encodeURIComponent(authToken);
        const url = `${protocol}//${window.location.host}/ws?token=${tokenQs}`;

        try {
            if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
                intentionalWsClose = true;
                ws.close();
            }
        } catch (_) {}

        intentionalWsClose = false;
        ws = new WebSocket(url);
        const statusInd = document.getElementById('ws-indicator');

        ws.onopen = () => {
            if (statusInd) {
                statusInd.className = 'ws-status online';
                statusInd.innerText = 'Live Sync Active';
            }
            try { ws.send(JSON.stringify({ action: 'ping' })); } catch (_) {}
        };

        ws.onerror = () => {
            if (statusInd) {
                statusInd.className = 'ws-status offline';
                statusInd.innerText = 'Live Sync Error';
            }
        };

        ws.onclose = () => {
            if (statusInd) {
                statusInd.className = 'ws-status offline';
                statusInd.innerText = intentionalWsClose ? 'Live Sync Off' : 'Reconnecting...';
            }
            if (!intentionalWsClose && authToken) {
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(initWebSocket, 3000);
            }
        };

        ws.onmessage = (event) => {
            let msg;
            try { msg = JSON.parse(event.data); } catch (_) { return; }

            if (msg.action === 'pong') return;

            if (msg.type === 'DISCORD_MESSAGE') {
                const term = document.getElementById('live-chat-stream');
                if (!term) return;
                const d = msg.data || {};
                const entry = document.createElement('div');
                entry.className = 'log-entry user';
                entry.innerText = `[${d.channel || d.channel_name || '?'}] ${d.author || d.author_name || '?'}: ${d.content || ''}`;
                term.appendChild(entry);
                term.scrollTop = term.scrollHeight;
                while (term.children.length > 50) term.removeChild(term.firstChild);
            } else if (msg.type === 'execution_telemetry') {
                handleExecutionTelemetry(msg);
            } else if (msg.type === 'system_metrics') {
                updateHealthDashboard(msg.data || {});
            } else if (msg.type === 'CONFIG_UPDATE' || msg.type === 'EMERGENCY_STOP_TRIGGERED') {
                AzureNav.addNotification(msg.type === 'EMERGENCY_STOP_TRIGGERED' ? 'Emergency stop triggered!' : 'Configuration updated', msg.type === 'EMERGENCY_STOP_TRIGGERED' ? 'warning' : 'info');
                fetchHealth();
                fetchLogs();
            }
        };
    };

    checkAuth();
});
