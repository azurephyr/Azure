/**
 * Lightweight nav injection for standalone pages.
 * Replaces any existing sidebar with the shared Azure sidebar,
 * adds hamburger/overlay for mobile, footer, breadcrumbs, and search modal.
 */
(function() {
    const PAGES = [
        { id: 'panel-health', label: 'Dashboard', icon: '&#9632;', href: '/' },
        { id: 'panel-analytics', label: 'Analytics', icon: '&#9650;', href: '/analytics' },
        { id: 'panel-mod', label: 'Moderation', icon: '&#9650;', href: '/moderation' },
        { id: 'panel-config', label: 'Settings', icon: '&#9881;', href: '/settings' },
        { id: 'panel-live', label: 'Logs', icon: '&#9617;', href: '/logs' },
        { id: 'panel-users', label: 'Users', icon: '&#9787;', href: '/users' },
    ];

    const currentPage = (function() {
        const path = window.location.pathname.toLowerCase();
        if (path.includes('analytics')) return 'panel-analytics';
        if (path.includes('moderation') || path.includes('mod')) return 'panel-mod';
        if (path.includes('settings') || path.includes('config')) return 'panel-config';
        if (path.includes('logs')) return 'panel-live';
        if (path.includes('users')) return 'panel-users';
        return 'panel-health';
    })();

    function esc(s) {
        if (s == null) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    // Theme
    const LS_THEME = 'azure_theme';
    function getTheme() { return localStorage.getItem(LS_THEME) || 'dark'; }
    function setTheme(t) { localStorage.setItem(LS_THEME, t); document.documentElement.setAttribute('data-theme', t); }
    setTheme(getTheme());

    // Remove existing sidebars that are NOT the shared one
    document.querySelectorAll('.sidebar, .logs-sidebar, .settings-sidebar, .logs-layout > aside:first-child').forEach(el => {
        if (!el.id || el.id !== 'azure-sidebar') {
            el.remove();
        }
    });

    // Build shared sidebar HTML
    const sidebarHtml = `
    <aside class="sidebar glass-panel" id="azure-sidebar">
        <div class="sidebar-header">
            <div class="sidebar-logo"></div>
            <h2>Azure Core</h2>
        </div>
        <nav id="sidebar-nav">
            ${PAGES.map(p => `
                <a href="${p.href}" class="nav-item ${p.id === currentPage ? 'active' : ''}">
                    <span class="icon">${p.icon}</span>
                    <span class="nav-label">${p.label}</span>
                </a>
            `).join('')}
        </nav>
        <div class="sidebar-footer">
            <div class="ws-status offline" id="ws-indicator">Offline</div>
            <div class="sidebar-actions">
                <a href="/" class="btn secondary small">Dashboard</a>
            </div>
        </div>
    </aside>`;

    // Build footer
    const footerHtml = `<div id="azure-footer">
        <span class="footer-version">Azure v1.0.0</span>
        <span class="footer-sep">|</span>
        <span class="footer-uptime" id="footer-uptime">Uptime: --</span>
    </div>`;

    // Build search modal
    const searchHtml = `
    <div id="search-modal" style="display:none;position:fixed;inset:0;z-index:5000;background:rgba(0,0,0,0.6);backdrop-filter:blur(4px);align-items:flex-start;justify-content:center;padding-top:15vh;">
        <div class="search-box" style="width:520px;max-width:90vw;background:var(--bg-dark);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);box-shadow:0 25px 60px rgba(0,0,0,0.7);overflow:hidden;">
            <div class="search-input-wrap" style="display:flex;align-items:center;padding:16px 20px;border-bottom:1px solid var(--border-subtle);gap:12px;">
                <span class="search-icon" style="color:var(--text-tertiary);font-size:1.1rem;">&#128269;</span>
                <input type="text" id="search-input" placeholder="Go to page..." style="flex:1;background:transparent;border:none;color:var(--text-primary);font-size:1rem;font-family:var(--font-sans);outline:none;">
            </div>
            <div id="search-results" style="max-height:320px;overflow-y:auto;padding:8px;">
                ${PAGES.map(p => `
                    <a href="${p.href}" class="search-result" style="display:flex;align-items:center;gap:12px;padding:10px 14px;border-radius:8px;cursor:pointer;text-decoration:none;color:var(--text-primary);transition:background 0.15s;">
                        <span class="sr-icon" style="width:28px;height:28px;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,0.05);border-radius:6px;font-size:0.9rem;">${p.icon}</span>
                        <span class="sr-label" style="flex:1;font-size:0.9rem;font-weight:500;">${p.label}</span>
                    </a>
                `).join('')}
            </div>
        </div>
    </div>`;

    // Hamburger button
    const hamburgerHtml = `<button class="hamburger" id="hamburger-btn" aria-label="Toggle menu" style="display:none;position:fixed;top:14px;left:14px;z-index:200;width:40px;height:40px;border-radius:10px;background:var(--bg-surface);border:1px solid var(--border-subtle);backdrop-filter:blur(12px);cursor:pointer;align-items:center;justify-content:center;color:var(--text-primary);font-size:1.2rem;">&#9776;</button>
    <div class="mobile-overlay" id="mobile-overlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:90;"></div>`;

    // Inject into DOM
    const body = document.body;
    const firstChild = body.firstElementChild;

    // Insert hamburger + overlay at start of body
    body.insertAdjacentHTML('afterbegin', hamburgerHtml);

    // Insert sidebar - find the main content container or insert before main content
    const mainContent = firstChild.querySelector('.main-content, .logs-main, .settings-main, [class*="main"]');
    if (mainContent) {
        mainContent.insertAdjacentHTML('beforebegin', sidebarHtml);
    } else if (firstChild) {
        firstChild.insertAdjacentHTML('afterbegin', sidebarHtml);
    } else {
        body.insertAdjacentHTML('afterbegin', sidebarHtml);
    }

    // Insert footer and search modal at end of body
    body.insertAdjacentHTML('beforeend', footerHtml + searchHtml);

    // Apply data-theme to html
    document.documentElement.setAttribute('data-theme', getTheme());

    // Mobile hamburger
    const hamburger = document.getElementById('hamburger-btn');
    const overlay = document.getElementById('mobile-overlay');
    const sidebar = document.getElementById('azure-sidebar');

    if (hamburger && sidebar) {
        hamburger.addEventListener('click', () => {
            sidebar.classList.toggle('mobile-open');
            overlay.style.display = sidebar.classList.contains('mobile-open') ? 'block' : 'none';
        });
    }
    if (overlay) {
        overlay.addEventListener('click', () => {
            if (sidebar) sidebar.classList.remove('mobile-open');
            overlay.style.display = 'none';
        });
    }

    // Search modal
    const searchModal = document.getElementById('search-modal');
    const searchInput = document.getElementById('search-input');

    function toggleSearch() {
        if (!searchModal) return;
        const isOpen = searchModal.style.display === 'flex';
        searchModal.style.display = isOpen ? 'none' : 'flex';
        if (!isOpen && searchInput) searchInput.focus();
    }

    if (searchModal) {
        searchModal.addEventListener('click', (e) => {
            if (e.target === searchModal) toggleSearch();
        });
    }

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); toggleSearch(); }
        if (e.key === 'Escape' && searchModal && searchModal.style.display === 'flex') { searchModal.style.display = 'none'; }
    });

    // Show hamburger on mobile
    function checkMobile() {
        if (hamburger) {
            hamburger.style.display = window.innerWidth <= 768 ? 'flex' : 'none';
        }
    }
    checkMobile();
    window.addEventListener('resize', checkMobile);
})();
