const API_BASE_URL = '/api';

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    checkLogin();
    loadUserInfo();
    loadRepoCount();
    loadDeployedAppsCount();
    loadContainerCount();
    loadSuccessRate();
    loadDeploymentHistory();
    
    setInterval(loadContainerCount, 10000);
});

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
            initSidebar('dashboard');
        });
    };
    document.head.appendChild(script);
}

function checkLogin() {
    const username = localStorage.getItem('username');
    if (!username) {
        window.location.href = '../login/login.html';
        return false;
    }
    return true;
}

function loadUserInfo() {
    const username = localStorage.getItem('username');
    const isAdmin = localStorage.getItem('is_admin') === 'true';
    
    if (username) {
        document.getElementById('currentUsername').textContent = username;
        document.querySelector('.header-left h1').textContent = `欢迎回来，${username}`;
    }
    
    if (!isAdmin) {
        const menuItems = document.querySelectorAll('.sidebar-nav li');
        menuItems.forEach((item, index) => {
            if (index > 0) {
                item.style.display = 'none';
            }
        });
        
        const quickActions = document.querySelector('.quick-actions');
        if (quickActions) {
            quickActions.style.display = 'none';
        }
    }
}

async function loadRepoCount() {
    try {
        const response = await fetch(`${API_BASE_URL}/repos`);
        if (response.ok) {
            const repos = await response.json();
            document.getElementById('repoCount').textContent = repos.length;
        }
    } catch (error) {
        console.error('Failed to load repo count:', error);
    }
}

async function loadDeployedAppsCount() {
    try {
        const response = await fetch(`${API_BASE_URL}/deployments/count`);
        if (response.ok) {
            const data = await response.json();
            document.getElementById('deployedAppsCount').textContent = data.count;
        }
    } catch (error) {
        console.error('Failed to load deployed apps count:', error);
    }
}

async function loadSuccessRate() {
    try {
        const response = await fetch(`${API_BASE_URL}/deployments/success-rate`);
        if (response.ok) {
            const data = await response.json();
            document.getElementById('successRate').textContent = data.rate + '%';
        }
    } catch (error) {
        console.error('Failed to load success rate:', error);
    }
}

async function loadContainerCount() {
    try {
        const response = await fetch(`${API_BASE_URL}/containers/count`);
        if (response.ok) {
            const data = await response.json();
            document.getElementById('containerCount').textContent = data.count;
        }
    } catch (error) {
        console.error('Failed to load container count:', error);
    }
}

async function loadDeploymentHistory() {
    try {
        const response = await fetch(`${API_BASE_URL}/deployments?limit=10`);
        if (response.ok) {
            const deployments = await response.json();
            renderDeploymentHistory(deployments);
        }
    } catch (error) {
        console.error('Failed to load deployment history:', error);
    }
}

function renderDeploymentHistory(deployments) {
    const container = document.getElementById('deploymentList');
    
    if (!deployments || deployments.length === 0) {
        container.innerHTML = `
            <div class="activity-item empty">
                <div class="activity-icon info">
                    <i class="fas fa-info"></i>
                </div>
                <div class="activity-content">
                    <h4>暂无部署记录</h4>
                    <p>通过应用部署页面部署应用后，记录将显示在这里</p>
                </div>
            </div>
        `;
        return;
    }
    
    container.innerHTML = deployments.map(deployment => {
        const statusClass = deployment.status === 'deployed' ? 'success' : 
                           deployment.status === 'failed' ? 'warning' : 'info';
        const iconClass = deployment.status === 'deployed' ? 'fa-check' : 
                         deployment.status === 'failed' ? 'fa-exclamation' : 'fa-spinner';
        const message = deployment.status === 'deployed' 
            ? `已成功部署到容器 ${deployment.container_name || 'unknown'}`
            : deployment.message || '部署失败';
        const timeAgo = formatTimeAgo(deployment.created_at);
        
        return `
            <div class="activity-item">
                <div class="activity-icon ${statusClass}">
                    <i class="fas ${iconClass}"></i>
                </div>
                <div class="activity-content">
                    <h4>${deployment.file_name.replace('.yml', '').replace('.yaml', '')}</h4>
                    <p>${message}</p>
                </div>
                <div class="activity-time">${timeAgo}</div>
            </div>
        `;
    }).join('');
}

function formatTimeAgo(dateString) {
    if (!dateString) return '未知';
    
    let date;
    try {
        if (dateString.includes('T')) {
            if (dateString.includes('Z')) {
                date = new Date(dateString);
            } else {
                date = new Date(dateString + 'Z');
            }
        } else {
            date = new Date(dateString.replace(/-/g, '/'));
        }
    } catch (e) {
        console.error('Date parsing error:', e);
        return '未知';
    }
    
    if (isNaN(date.getTime())) {
        return '未知';
    }
    
    const now = new Date();
    const diff = now - date;
    
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    
    if (minutes < 1) return '刚刚';
    if (minutes < 60) return `${minutes}分钟前`;
    if (hours < 24) return `${hours}小时前`;
    if (days < 7) return `${days}天前`;
    
    return date.toLocaleDateString('zh-CN');
}

function navigateTo(page) {
    const pageMap = {
        'repository': '../repository/repository.html',
        'deploy': '../deploy/deploy.html',
        'container': '../container/container.html',
        'settings': '../settings/settings.html'
    };
    
    const url = pageMap[page];
    if (url) {
        window.location.href = url;
    }
}