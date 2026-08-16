/**
 * Shared navigation component for Azure Operating Platform.
 * Injects sidebar, topbar, footer, breadcrumbs, notification bell,
 * theme toggle, keyboard shortcuts, and loading skeletons.
 */
const AzureNav = (() => {
    const PAGES = [
        { id: 'panel-health', label: 'Dashboard', icon: '&#9632;', shortcut: '1' },
        { id: 'panel-analytics', label: 'Analytics', icon: '&#9650;', shortcut: '2' },
        { id: 'panel-mod', label: 'Moderation', icon: '&#9650;', shortcut: '3' },
        { id: 'panel-config', label: 'Settings', icon: '&#9881;', shortcut: '4' },
        { id: 'panel-live', label: 'Logs', icon: '&#9617;', shortcut: '5' },
        { id: 'panel-users', label: 'Users', icon: '&#9787;', shortcut: '6' },
    ];

    let currentPage = 'panel-health';
    let notifications = [];
    let notifOpen = false;
    let searchOpen = false;

    const LS_THEME = 'azure_theme';
    const LS_NOTIFS = 'azure_notifications';

    function getTheme() {
        return localStorage.getItem(LS_THEME) || 'dark';
    }

    function setTheme(t) {
        localStorage.setItem(LS_THEME, t);
        document.documentElement.setAttribute('data-theme', t);
    }

    function toggleTheme() {
        setTheme(getTheme() === 'dark' ? 'light' : 'dark');
        renderThemeBtn();
    }

    function renderThemeBtn() {
        const btn = document.getElementById('theme-toggle');
        if (!btn) return;
        const dark = getTheme() === 'dark';
        btn.innerHTML = dark ? '&#9789;' : '&#9788;';
        btn.title = dark ? 'Switch to light mode' : 'Switch to dark mode';
    }

    function loadNotifications() {
        try {
            const raw = localStorage.getItem(LS_NOTIFS);
            notifications = raw ? JSON.parse(raw) : [];
        } catch (_) {
            notifications = [];
        }
    }

    function saveNotifications() {
        localStorage.setItem(LS_NOTIFS, JSON.stringify(notifications.slice(0, 50)));
    }

    function addNotification(msg, type) {
        notifications.unshift({ msg, type: type || 'info', time: Date.now(), read: false });
        if (notifications.length > 50) notifications = notifications.slice(0, 50);
        saveNotifications();
        renderNotifBadge();
    }

    function renderNotifBadge() {
        const badge = document.getElementById('notif-badge');
        if (!badge) return;
        const unread = notifications.filter(n => !n.read).length;
        badge.textContent = unread > 99 ? '99+' : String(unread);
        badge.style.display = unread > 0 ? 'flex' : 'none';
    }

    function toggleNotifications() {
        notifOpen = !notifOpen;
        const dropdown = document.getElementById('notif-dropdown');
        if (dropdown) {
            dropdown.style.display = notifOpen ? 'block' : 'none';
            if (notifOpen) {
                notifications.forEach(n => n.read = true);
                saveNotifications();
                renderNotifBadge();
                renderNotifList();
            }
        }
    }

    function renderNotifList() {
        const list = document.getElementById('notif-list');
        if (!list) return;
        if (notifications.length === 0) {
            list.innerHTML = '<div class="notif-empty">No notifications</div>';
            return;
        }
        list.innerHTML = notifications.slice(0, 20).map(n => {
            const ago = timeAgo(n.time);
            return `<div class="notif-item notif-${n.type}">
                <span class="notif-msg">${escHtml(n.msg)}</span>
                <span class="notif-time">${ago}</span>
            </div>`;
        }).join('');
    }

    function timeAgo(ts) {
        const diff = Date.now() - ts;
        if (diff < 60000) return 'just now';
        if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
        if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
        return Math.floor(diff / 86400000) + 'd ago';
    }

    function escHtml(s) {
        if (s == null) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    // Breadcrumb rendering
    function getBreadcrumb() {
        const page = PAGES.find(p => p.id === currentPage);
        if (!page) return '';
        const idx = PAGES.indexOf(page);
        return `<nav class="breadcrumb">
            <span class="bc-home" onclick="AzureNav.navigate('panel-health')">Azure</span>
            ${currentPage !== 'panel-health' ? `<span class="bc-sep">/</span><span class="bc-current">${page.label}</span>` : ''}
        </nav>`;
    }

    // Skeleton loader
    function skeleton(type) {
        if (type === 'card') {
            return `<div class="skeleton skeleton-card"><div class="sk-title"></div><div class="sk-line wide"></div><div class="sk-line"></div></div>`;
        }
        if (type === 'table') {
            return `<div class="skeleton skeleton-table"><div class="sk-row"></div><div class="sk-row"></div><div class="sk-row"></div><div class="sk-row short"></div></div>`;
        }
        if (type === 'chart') {
            return `<div class="skeleton skeleton-chart"><div class="sk-bar" style="height:60%"></div><div class="sk-bar" style="height:80%"></div><div class="sk-bar" style="height:40%"></div><div class="sk-bar" style="height:90%"></div><div class="sk-bar" style="height:55%"></div></div>`;
        }
        return `<div class="skeleton"><div class="sk-line"></div><div class="sk-line wide"></div><div class="sk-line"></div></div>`;
    }

    // Keyboard shortcuts
    function initKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            // Ignore if typing in an input
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;

            // Ctrl+K = search
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                toggleSearch();
                return;
            }

            // Escape = close overlays
            if (e.key === 'Escape') {
                if (searchOpen) toggleSearch();
                if (notifOpen) toggleNotifications();
                return;
            }

            // 1-6 for page navigation
            const num = parseInt(e.key);
            if (num >= 1 && num <= 6 && !e.ctrlKey && !e.metaKey && !e.altKey) {
                const page = PAGES[num - 1];
                if (page) navigate(page.id);
            }
        });
    }

    function toggleSearch() {
        searchOpen = !searchOpen;
        const modal = document.getElementById('search-modal');
        if (modal) {
            modal.style.display = searchOpen ? 'flex' : 'none';
            if (searchOpen) {
                const input = document.getElementById('search-input');
                if (input) { input.value = ''; input.focus(); }
                renderSearchResults('');
            }
        }
    }

    function renderSearchResults(query) {
        const results = document.getElementById('search-results');
        if (!results) return;
        const q = query.toLowerCase().trim();
        const matches = PAGES.filter(p => !q || p.label.toLowerCase().includes(q));
        results.innerHTML = matches.map(p =>
            `<div class="search-result" onclick="AzureNav.navigate('${p.id}');AzureNav.toggleSearch();">
                <span class="sr-icon">${p.icon}</span>
                <span class="sr-label">${p.label}</span>
                <span class="sr-shortcut">${p.shortcut}</span>
            </div>`
        ).join('');
    }

    function navigate(panelId) {
        currentPage = panelId;
        // Dispatch a custom event so app.js can listen
        document.dispatchEvent(new CustomEvent('azure:navigate', { detail: { panelId } }));
        renderSidebar();
        renderBreadcrumb();
        closeMobile();
    }

    function toggleMobile() {
        const sidebar = document.getElementById('azure-sidebar');
        const overlay = document.getElementById('mobile-overlay');
        if (sidebar) sidebar.classList.toggle('mobile-open');
        if (overlay) overlay.style.display = sidebar.classList.contains('mobile-open') ? 'block' : 'none';
    }

    function closeMobile() {
        const sidebar = document.getElementById('azure-sidebar');
        const overlay = document.getElementById('mobile-overlay');
        if (sidebar) sidebar.classList.remove('mobile-open');
        if (overlay) overlay.style.display = 'none';
    }

    function renderSidebar() {
        const nav = document.getElementById('sidebar-nav');
        if (!nav) return;
        nav.innerHTML = PAGES.map(p =>
            `<a href="#" class="nav-item ${p.id === currentPage ? 'active' : ''}" data-target="${p.id}" onclick="AzureNav.navigate('${p.id}');return false;">
                <span class="icon">${p.icon}</span>
                <span class="nav-label">${p.label}</span>
                <span class="nav-shortcut">${p.shortcut}</span>
            </a>`
        ).join('');
    }

    function renderBreadcrumb() {
        const el = document.getElementById('breadcrumb-area');
        if (el) el.innerHTML = getBreadcrumb();
    }

    function renderFooter() {
        const el = document.getElementById('azure-footer');
        if (!el) return;
        el.innerHTML = `<span class="footer-version">Azure v1.0.0</span>
            <span class="footer-sep">|</span>
            <span class="footer-uptime" id="footer-uptime">Uptime: --</span>
            <span class="footer-sep">|</span>
            <span class="footer-restart" id="footer-restart">Last restart: --</span>`;
    }

    function updateFooter(uptimeSec, lastRestart) {
        const uptimeEl = document.getElementById('footer-uptime');
        const restartEl = document.getElementById('footer-restart');
        if (uptimeEl && uptimeSec != null) {
            const h = Math.floor(uptimeSec / 3600);
            const m = Math.floor((uptimeSec % 3600) / 60);
            const s = uptimeSec % 60;
            uptimeEl.textContent = `Uptime: ${h}h ${m}m ${s}s`;
        }
        if (restartEl && lastRestart) {
            restartEl.textContent = `Last restart: ${new Date(lastRestart).toLocaleString()}`;
        }
    }

    // Public API
    return {
        PAGES,
        init() {
            setTheme(getTheme());
            loadNotifications();
            renderSidebar();
            renderBreadcrumb();
            renderFooter();
            renderThemeBtn();
            renderNotifBadge();
            initKeyboardShortcuts();

            // Close dropdowns on outside click
            document.addEventListener('click', (e) => {
                if (notifOpen && !e.target.closest('#notif-bell') && !e.target.closest('#notif-dropdown')) {
                    notifOpen = false;
                    const dd = document.getElementById('notif-dropdown');
                    if (dd) dd.style.display = 'none';
                }
            });
        },
        navigate,
        toggleTheme,
        toggleNotifications,
        toggleMobile,
        toggleSearch,
        renderSearchResults,
        addNotification,
        updateFooter,
        skeleton,
        getCurrentPage() { return currentPage; },
    };
})();
