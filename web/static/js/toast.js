/**
 * Toast notification system for Azure Operating Platform.
 * Provides success/error/warning/info toasts with auto-dismiss, stacking, and slide-in animation.
 */
const AzureToast = (() => {
    let container = null;

    function ensureContainer() {
        if (!container || !document.body.contains(container)) {
            container = document.getElementById('toast-container');
            if (!container) {
                container = document.createElement('div');
                container.id = 'toast-container';
                container.className = 'azure-toast-container';
                document.body.appendChild(container);
            }
        }
        return container;
    }

    function show(message, type, duration) {
        type = type || 'info';
        duration = duration || 5000;
        const c = ensureContainer();

        const toast = document.createElement('div');
        toast.className = 'azure-toast azure-toast-' + type;

        const icons = { success: '\u2713', error: '\u2717', warning: '\u26A0', info: '\u2139' };
        const icon = document.createElement('span');
        icon.className = 'azure-toast-icon';
        icon.textContent = icons[type] || icons.info;

        const text = document.createElement('span');
        text.className = 'azure-toast-text';
        text.textContent = message;

        const closeBtn = document.createElement('button');
        closeBtn.className = 'azure-toast-close';
        closeBtn.innerHTML = '&times;';
        closeBtn.onclick = function () { dismiss(toast); };

        toast.appendChild(icon);
        toast.appendChild(text);
        toast.appendChild(closeBtn);
        c.appendChild(toast);

        // Trigger reflow for animation
        void toast.offsetHeight;
        toast.classList.add('azure-toast-visible');

        if (duration > 0) {
            setTimeout(function () { dismiss(toast); }, duration);
        }

        // Limit total visible toasts
        while (c.children.length > 5) {
            c.removeChild(c.firstChild);
        }

        return toast;
    }

    function dismiss(toast) {
        if (!toast || !toast.parentNode) return;
        toast.classList.remove('azure-toast-visible');
        toast.classList.add('azure-toast-exit');
        setTimeout(function () {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 300);
    }

    return {
        success: function (msg, dur) { return show(msg, 'success', dur); },
        error: function (msg, dur) { return show(msg, 'error', dur); },
        warning: function (msg, dur) { return show(msg, 'warning', dur); },
        info: function (msg, dur) { return show(msg, 'info', dur); },
        show: show,
        dismiss: dismiss,
    };
})();

if (typeof window !== 'undefined') {
    window.AzureToast = AzureToast;
}
