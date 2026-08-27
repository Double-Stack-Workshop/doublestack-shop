(function () {
    const API_BASE_URL = '/api';

    async function loadSidebar(currentPage, containerId = 'appContainer') {
        await syncCurrentUser();
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
    }

    async function syncCurrentUser() {
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
        return response;
    }

    window.AppPage = { API_BASE_URL, apiFetch, loadSidebar, requireLogin, populateUsername, syncCurrentUser };
}());
