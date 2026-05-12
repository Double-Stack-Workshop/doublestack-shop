const API_BASE_URL = '/api';
let allContainers = [];

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    checkLogin();
    loadUserInfo();
    await refreshContainers();
    
    setInterval(refreshContainers, 15000);
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
            initSidebar('container');
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
    }
    
    if (!isAdmin) {
        const menuItems = document.querySelectorAll('.sidebar-nav li');
        menuItems.forEach((item, index) => {
            if (index > 0) {
                item.style.display = 'none';
            }
        });
    }
}

async function refreshContainers() {
    try {
        const response = await fetch(`${API_BASE_URL}/containers`);
        if (response.ok) {
            allContainers = await response.json();
            renderContainers(allContainers);
            updateStats();
        } else {
            showEmptyState();
        }
    } catch (error) {
        console.error('Failed to load containers:', error);
        showEmptyState();
    }
}

function updateStats() {
    const running = allContainers.filter(c => c.state === 'running').length;
    const stopped = allContainers.filter(c => c.state === 'exited').length;
    const total = allContainers.length;
    
    document.getElementById('runningCount').textContent = running;
    document.getElementById('stoppedCount').textContent = stopped;
    document.getElementById('totalCount').textContent = total;
}

function filterContainers() {
    const filter = document.getElementById('statusFilter').value;
    const searchQuery = document.getElementById('searchInput').value.toLowerCase();
    
    let filtered = allContainers;
    
    if (filter !== 'all') {
        filtered = filtered.filter(c => c.state === filter);
    }
    
    if (searchQuery) {
        filtered = filtered.filter(c => 
            c.name.toLowerCase().includes(searchQuery) ||
            c.id.toLowerCase().includes(searchQuery)
        );
    }
    
    renderContainers(filtered);
}

function renderContainers(containers) {
    const containerList = document.getElementById('containersList');
    
    if (!containers || containers.length === 0) {
        showEmptyState();
        return;
    }
    
    containerList.innerHTML = containers.map(container => `
        <div class="container-card ${container.state}">
            <div class="container-info">
                <div class="container-icon">
                    <i class="fab fa-docker"></i>
                </div>
                <div class="container-details">
                    <h3>${container.name}</h3>
                    <p>
                        <span><i class="fas fa-hashtag"></i> ${container.id.slice(0, 12)}</span>
                        <span><i class="fas fa-image"></i> ${container.image.split('/').pop().split(':')[0]}</span>
                    </p>
                </div>
            </div>
            <div class="container-status">
                <span class="status-badge ${container.state}">
                    <i class="fas fa-circle"></i>
                    ${container.state === 'running' ? '运行中' : '已停止'}
                </span>
            </div>
            <div class="container-actions">
                <button class="action-btn view" title="查看详情" onclick="showContainerDetail('${container.id}')">
                    <i class="fas fa-eye"></i>
                </button>
                <button class="action-btn logs" title="查看日志" onclick="showContainerLogs('${container.id}', '${container.name}')">
                    <i class="fas fa-file-text"></i>
                </button>
                ${container.state === 'running' ? `
                    <button class="action-btn stop" title="停止容器" onclick="stopContainer('${container.id}')">
                        <i class="fas fa-stop"></i>
                    </button>
                    <button class="action-btn restart" title="重启容器" onclick="restartContainer('${container.id}')">
                        <i class="fas fa-redo"></i>
                    </button>
                ` : `
                    <button class="action-btn start" title="启动容器" onclick="startContainer('${container.id}')">
                        <i class="fas fa-play"></i>
                    </button>
                `}
                <button class="action-btn remove" title="删除容器" onclick="removeContainer('${container.id}', ${container.state === 'running'})">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        </div>
    `).join('');
}

function showEmptyState() {
    const containerList = document.getElementById('containersList');
    containerList.innerHTML = `
        <div class="empty-state">
            <i class="fas fa-inbox"></i>
            <p>暂无容器数据</p>
            <button class="btn btn-primary" onclick="refreshContainers()">
                <i class="fas fa-refresh"></i>
                刷新数据
            </button>
        </div>
    `;
}

async function showContainerDetail(containerId) {
    try {
        const response = await fetch(`${API_BASE_URL}/containers/${containerId}`);
        if (response.ok) {
            const container = await response.json();
            document.getElementById('modalTitle').textContent = `容器详情 - ${container.name}`;
            document.getElementById('modalBody').innerHTML = `
                <div class="detail-row">
                    <span class="detail-label">容器名称</span>
                    <span class="detail-value">${container.name}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">容器ID</span>
                    <span class="detail-value">${container.id}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">镜像</span>
                    <span class="detail-value">${container.image}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">状态</span>
                    <span class="detail-value">${container.state === 'running' ? '<span style="color: #22c55e;">运行中</span>' : '<span style="color: #ef4444;">已停止</span>'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">运行时间</span>
                    <span class="detail-value">${container.uptime || 'N/A'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">端口映射</span>
                    <span class="detail-value">${container.ports && container.ports.length > 0 ? container.ports.join('<br>') : '无'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">创建时间</span>
                    <span class="detail-value">${container.created_at || 'N/A'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">命令</span>
                    <span class="detail-value">${container.command || 'N/A'}</span>
                </div>
            `;
            document.getElementById('modalOverlay').style.display = 'flex';
        }
    } catch (error) {
        console.error('Failed to get container detail:', error);
        alert('获取容器详情失败');
    }
}

function closeModal() {
    document.getElementById('modalOverlay').style.display = 'none';
}

async function startContainer(containerId) {
    if (!confirm('确定要启动此容器吗？')) return;
    
    try {
        const response = await fetch(`${API_BASE_URL}/containers/${containerId}/start`, {
            method: 'POST'
        });
        if (response.ok) {
            await refreshContainers();
            alert('容器启动成功');
        } else {
            const result = await response.json();
            alert(result.message || '启动失败');
        }
    } catch (error) {
        console.error('Failed to start container:', error);
        alert('启动容器失败');
    }
}

async function stopContainer(containerId) {
    if (!confirm('确定要停止此容器吗？')) return;
    
    try {
        const response = await fetch(`${API_BASE_URL}/containers/${containerId}/stop`, {
            method: 'POST'
        });
        if (response.ok) {
            await refreshContainers();
            alert('容器停止成功');
        } else {
            const result = await response.json();
            alert(result.message || '停止失败');
        }
    } catch (error) {
        console.error('Failed to stop container:', error);
        alert('停止容器失败');
    }
}

async function restartContainer(containerId) {
    if (!confirm('确定要重启此容器吗？')) return;
    
    try {
        const response = await fetch(`${API_BASE_URL}/containers/${containerId}/restart`, {
            method: 'POST'
        });
        if (response.ok) {
            await refreshContainers();
            alert('容器重启成功');
        } else {
            const result = await response.json();
            alert(result.message || '重启失败');
        }
    } catch (error) {
        console.error('Failed to restart container:', error);
        alert('重启容器失败');
    }
}

async function removeContainer(containerId, isRunning) {
    const confirmMsg = isRunning 
        ? '此容器正在运行，确定要强制删除吗？此操作不可撤销！'
        : '确定要删除此容器吗？此操作不可撤销！';
    
    if (!confirm(confirmMsg)) return;
    
    try {
        const url = isRunning 
            ? `${API_BASE_URL}/containers/${containerId}?force=true`
            : `${API_BASE_URL}/containers/${containerId}`;
        
        const response = await fetch(url, {
            method: 'DELETE'
        });
        if (response.ok) {
            await refreshContainers();
            alert('容器删除成功');
        } else {
            const result = await response.json();
            alert(result.message || '删除失败');
        }
    } catch (error) {
        console.error('Failed to remove container:', error);
        alert('删除容器失败');
    }
}

let currentLogsContainerId = null;

async function showContainerLogs(containerId, containerName) {
    const titleEl = document.getElementById('logsModalTitle');
    const overlayEl = document.getElementById('logsModalOverlay');
    
    if (!titleEl || !overlayEl) {
        console.error('日志弹窗元素未找到');
        return;
    }
    
    currentLogsContainerId = containerId;
    titleEl.textContent = `容器日志 - ${containerName}`;
    overlayEl.style.display = 'flex';
    await loadContainerLogs(containerId);
}

async function loadContainerLogs(containerId) {
    try {
        const response = await fetch(`${API_BASE_URL}/containers/${containerId}/logs?tail=200`);
        if (response.ok) {
            const data = await response.json();
            document.getElementById('logsContent').textContent = data.logs || '暂无日志';
        } else {
            document.getElementById('logsContent').textContent = '获取日志失败';
        }
    } catch (error) {
        console.error('Failed to load container logs:', error);
        document.getElementById('logsContent').textContent = '获取日志失败: ' + error.message;
    }
}

async function refreshLogs() {
    if (currentLogsContainerId) {
        await loadContainerLogs(currentLogsContainerId);
    }
}

function clearLogs() {
    document.getElementById('logsContent').textContent = '';
}

function closeLogsModal() {
    document.getElementById('logsModalOverlay').style.display = 'none';
    currentLogsContainerId = null;
}

function viewAllContainers() {
    document.getElementById('statusFilter').value = 'all';
    document.getElementById('searchInput').value = '';
    filterContainers();
}

document.getElementById('modalOverlay').addEventListener('click', function(e) {
    if (e.target === this) {
        closeModal();
    }
});

document.getElementById('logsModalOverlay').addEventListener('click', function(e) {
    if (e.target === this) {
        closeLogsModal();
    }
});