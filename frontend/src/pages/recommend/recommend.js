const API_BASE_URL = '/api';

function goToDeploy(ymlFileName) {
    window.location.href = `/src/pages/deploy/deploy.html?search=${encodeURIComponent(ymlFileName)}`;
}

function openTutorial(url) {
    window.open(url, '_blank');
}

function checkLogin() {
    const username = localStorage.getItem('username');
    if (!username) {
        window.location.href = '/src/pages/login/login.html';
        return false;
    }
    return true;
}

function loadUserInfo() {
    const username = localStorage.getItem('username');
    const isAdmin = localStorage.getItem('is_admin') === 'true';
    
    if (username) {
        document.getElementById('currentUsername').textContent = username;
    }
}

async function loadSidebar() {
    const response = await fetch('/src/components/sidebar/sidebar.html');
    const sidebarHtml = await response.text();
    const appContainer = document.getElementById('appContainer');
    appContainer.insertAdjacentHTML('afterbegin', sidebarHtml);
    
    const script = document.createElement('script');
    script.src = '/src/components/sidebar/sidebar.js';
    script.type = 'module';
    script.onload = function() {
        import('/src/components/sidebar/sidebar.js').then(({ initSidebar }) => {
            initSidebar('recommend');
        });
    };
    document.head.appendChild(script);
}

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    checkLogin();
    loadUserInfo();
});