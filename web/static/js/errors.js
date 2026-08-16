/**
 * Global error handler for Azure Operating Platform.
 * - window.onerror / unhandledrejection handlers that show toasts
 * - Network error detector (shows "Connection lost" banner)
 * - Retry logic with exponential backoff for fetch calls
 */
(function () {
    'use strict';

    var _recoveryBanner = null;
    var _isOnline = navigator.onLine !== false;

    // ── Network status banner ──────────────────────────────────────────
    function showOfflineBanner() {
        if (_recoveryBanner) return;
        _recoveryBanner = document.createElement('div');
        _recoveryBanner.className = 'azure-offline-banner';
        _recoveryBanner.innerHTML =
            '<span class="azure-offline-icon">\u26A0</span>' +
            '<span class="azure-offline-text">Connection lost. Retrying...</span>';
        document.body.appendChild(_recoveryBanner);
        requestAnimationFrame(function () {
            _recoveryBanner.classList.add('azure-offline-visible');
        });
    }

    function hideOfflineBanner() {
        if (!_recoveryBanner) return;
        _recoveryBanner.classList.remove('azure-offline-visible');
        var el = _recoveryBanner;
        setTimeout(function () {
            if (el.parentNode) el.parentNode.removeChild(el);
        }, 400);
        _recoveryBanner = null;
    }

    window.addEventListener('online', function () {
        _isOnline = true;
        hideOfflineBanner();
        if (window.AzureToast) {
            window.AzureToast.success('Connection restored', 3000);
        }
    });

    window.addEventListener('offline', function () {
        _isOnline = false;
        showOfflineBanner();
        if (window.AzureToast) {
            window.AzureToast.error('Connection lost', 0);
        }
    });

    // ── Global error handlers ──────────────────────────────────────────
    window.onerror = function (msg, url, line, col, err) {
        if (window.AzureToast) {
            var detail = typeof msg === 'string' ? msg : 'Unknown error';
            if (detail.length > 120) detail = detail.substring(0, 117) + '...';
            window.AzureToast.error('Client error: ' + detail, 6000);
        }
        return false;
    };

    window.addEventListener('unhandledrejection', function (e) {
        if (window.AzureToast) {
            var reason = e && e.reason;
            var detail = 'Unhandled promise rejection';
            if (reason) {
                if (typeof reason === 'string') detail = reason;
                else if (reason.message) detail = reason.message;
            }
            if (detail.length > 120) detail = detail.substring(0, 117) + '...';
            window.AzureToast.warning(detail, 5000);
        }
    });

    // ── Retry-capable fetch wrapper ────────────────────────────────────
    /**
     * AzureFetch(url, options)
     * - Checks response.ok, parses JSON
     * - Retries up to 3 times with exponential backoff on network/5xx errors
     * - Redirects to / on 401
     * - Shows user-friendly toast on errors
     * - Returns parsed JSON or null on failure
     */
    window.AzureFetch = function (url, options) {
        options = options || {};
        var maxRetries = options._retries != null ? options._retries : 3;
        var attempt = options._attempt || 1;

        return fetch(url, {
            method: options.method || 'GET',
            headers: options.headers || {},
            body: options.body || undefined,
        })
            .then(function (res) {
                // 401 → redirect to login
                if (res.status === 401) {
                    if (window.AzureToast) {
                        window.AzureToast.warning('Session expired. Redirecting to login...', 3000);
                    }
                    setTimeout(function () {
                        window.location.href = '/';
                    }, 1500);
                    return null;
                }

                // 500+ → retry or show error
                if (res.status >= 500) {
                    if (attempt < maxRetries) {
                        var delay = Math.pow(2, attempt) * 500;
                        return new Promise(function (resolve) {
                            setTimeout(function () {
                                options._attempt = attempt + 1;
                                resolve(window.AzureFetch(url, options));
                            }, delay);
                        });
                    }
                    if (window.AzureToast) {
                        window.AzureToast.error('Server error. Please try again later.', 5000);
                    }
                    return null;
                }

                if (!res.ok) {
                    return res.json().catch(function () { return {}; }).then(function (body) {
                        var msg = (body && body.detail) ? body.detail : 'Request failed (' + res.status + ')';
                        if (window.AzureToast) {
                            window.AzureToast.error(msg, 5000);
                        }
                        return null;
                    });
                }

                return res.json();
            })
            .catch(function (err) {
                // Network error → retry or show banner
                if (attempt < maxRetries) {
                    var delay = Math.pow(2, attempt) * 500;
                    return new Promise(function (resolve) {
                        setTimeout(function () {
                            options._attempt = attempt + 1;
                            resolve(window.AzureFetch(url, options));
                        }, delay);
                    });
                }

                showOfflineBanner();
                if (window.AzureToast) {
                    var errMsg = (err && err.message) ? err.message : 'Network error';
                    window.AzureToast.error(errMsg + '. Check your connection.', 5000);
                }
                return null;
            });
    };

    // ── API helper with auth header ────────────────────────────────────
    window.AzureApiGet = function (url) {
        var token = localStorage.getItem('azure_token') || localStorage.getItem('azure_auth_token') || '';
        return window.AzureFetch(url, {
            headers: { 'Authorization': 'Bearer ' + token }
        });
    };

    window.AzureApiPost = function (url, data) {
        var token = localStorage.getItem('azure_token') || localStorage.getItem('azure_auth_token') || '';
        return window.AzureFetch(url, {
            method: 'POST',
            headers: {
                'Authorization': 'Bearer ' + token,
                'Content-Type': 'application/json'
            },
            body: data != null ? JSON.stringify(data) : '{}'
        });
    };

    // ── Error boundary helper ──────────────────────────────────────────
    /**
     * AzureTryCatch(fn, sectionName)
     * Wraps fn() in try/catch so one broken section doesn't break the page.
     */
    window.AzureTryCatch = function (fn, sectionName) {
        try {
            var result = fn();
            if (result && typeof result.catch === 'function') {
                return result.catch(function (err) {
                    console.error('[AzureError] ' + (sectionName || 'section') + ':', err);
                    if (window.AzureToast) {
                        window.AzureToast.warning(
                            (sectionName || 'Section') + ' failed to load', 4000
                        );
                    }
                    return null;
                });
            }
            return result;
        } catch (err) {
            console.error('[AzureError] ' + (sectionName || 'section') + ':', err);
            if (window.AzureToast) {
                window.AzureToast.warning(
                    (sectionName || 'Section') + ' failed to load', 4000
                );
            }
            return null;
        }
    };
})();
