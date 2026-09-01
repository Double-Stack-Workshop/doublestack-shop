(function () {
    const API_BASE_URL = '/api';
    const PASSWORD_CHANGE_PATH = '/src/pages/change-password/change-password.html';

    async function loadSidebar(currentPage, containerId = 'appContainer') {
        const user = await syncCurrentUser();
        if (!user || user.must_change_password) return;
        const response = await fetch('/src/components/sidebar/sidebar.html');
        if (!response.ok) throw new Error('侧边栏加载失败');
        const container = document.getElementById(containerId);
        if (!container) throw new Error(`未找到页面容器: ${containerId}`);
        container.insertAdjacentHTML('afterbegin', await response.text());
        const { initSidebar } = await import('/src/components/sidebar/sidebar.js');
        initSidebar(currentPage);
        if (localStorage.getItem('is_admin') === 'false') {
            document.querySelectorAll('.sidebar-nav li').forEach((item, index) => {
                if (index > 0) item.hidden = true;
            });
        } else if (!window.location.pathname.endsWith('/ai/ai.html')) {
            import('/src/components/ai-widget/ai-widget.js')
                .then(module => module.initAIWidget())
                .catch(error => console.error('AI 悬浮窗口加载失败:', error));
        }
    }

    function requireLogin({ admin = false, loginPath = '/src/pages/login/login.html', fallbackPath = '/src/pages/dashboard/dashboard.html' } = {}) {
        const username = localStorage.getItem('username');
        const isAdmin = localStorage.getItem('is_admin') === 'true';
        if (!username) {
            window.location.href = loginPath;
            return false;
        }
        if (admin && !isAdmin) {
            window.location.href = fallbackPath;
            return false;
        }
        return true;
    }

    function populateUsername(elementId = 'currentUsername') {
        const element = document.getElementById(elementId);
        const username = localStorage.getItem('username');
        if (element && username) element.textContent = username;
        document.querySelectorAll('.user-avatar img').forEach(image => {
            image.src = `${API_BASE_URL}/me/avatar?v=${Date.now()}`;
            image.onerror = () => { image.src = '/src/images/logo.png'; };
        });
    }

    async function syncCurrentUser({ allowPasswordChange = false } = {}) {
        const response = await fetch(`${API_BASE_URL}/me`);
        if (response.status === 401) {
            window.location.href = '/src/pages/login/login.html';
            return null;
        }
        if (!response.ok) return null;
        const result = await response.json();
        const user = result.data;
        if (user) {
            localStorage.setItem('username', user.username);
            localStorage.setItem('is_admin', user.is_admin ? 'true' : 'false');
            if (user.must_change_password && !allowPasswordChange) {
                window.location.replace(PASSWORD_CHANGE_PATH);
            }
        }
        return user;
    }

    async function apiFetch(path, options) {
        const response = await fetch(path.startsWith('/') ? path : `${API_BASE_URL}${path}`, options);
        if (response.status === 401) {
            localStorage.removeItem('username');
            localStorage.removeItem('is_admin');
            window.location.href = '/src/pages/login/login.html';
        }
        if (response.status === 403) {
            const result = await response.clone().json().catch(() => ({}));
            if (result.code === 'PASSWORD_CHANGE_REQUIRED') {
                window.location.replace(PASSWORD_CHANGE_PATH);
            }
        }
        return response;
    }

    async function copyText(text) {
        const value = String(text);
        if (window.isSecureContext !== false && navigator.clipboard?.writeText) {
            try {
                await navigator.clipboard.writeText(value);
                return;
            } catch {
                // 权限被拒绝时继续尝试兼容 HTTP/IP 访问的传统复制方式。
            }
        }
        const helper = document.createElement('textarea');
        helper.value = value;
        helper.setAttribute('readonly', '');
        helper.style.position = 'fixed';
        helper.style.left = '-9999px';
        helper.style.opacity = '0';
        document.body.append(helper);
        helper.select();
        helper.setSelectionRange(0, value.length);
        let copied = false;
        try { copied = document.execCommand('copy'); } finally { helper.remove(); }
        if (!copied) throw new Error('浏览器拒绝了复制操作');
    }

    window.AppPage = { API_BASE_URL, apiFetch, copyText, loadSidebar, requireLogin, populateUsername, syncCurrentUser };
}());
