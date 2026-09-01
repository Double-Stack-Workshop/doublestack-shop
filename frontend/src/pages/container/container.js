const { API_BASE_URL, apiFetch } = window.AppPage;
let allContainers = [];
let globalDomain = '';
let isRefreshingContainers = false;

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    checkLogin();
    loadUserInfo();
    await loadGlobalDomain();
    await refreshContainers();
    
    setInterval(refreshContainers, 15000);
});

async function loadGlobalDomain() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/global-domain`);
        if (response.ok) {
            const result = await response.json();
            if (result.success && result.data) {
                globalDomain = result.data.global_domain || '';
            }
        }
    } catch (error) {
        console.error('Failed to load global domain:', error);
    }
}

function getPortMappings(container) {
    const mappings = [];
    const seen = new Set();
    for (const rawPort of container.ports || []) {
        const value = String(rawPort).trim().replace(/^(?:0\.0\.0\.0|\[::\]|::):/, '');
        if (!value || seen.has(value)) continue;
        seen.add(value);
        const match = value.match(/^(?:(.+):)?(\d+)->(\d+)(?:\/([a-z0-9]+))?$/i);
        mappings.push({
            value,
            hostPort: match?.[2] || '',
            containerPort: match?.[3] || '',
            protocol: (match?.[4] || '').toLowerCase(),
        });
    }
    return mappings;
}

function buildAccessUrl(hostPort) {
    const configuredAddress = globalDomain.trim();
    if (!configuredAddress || !hostPort) return null;
    try {
        let address = configuredAddress;
        if (!/^https?:\/\//i.test(address)) {
            if (address.includes(':') && !address.includes('[') && address.split(':').length > 2) {
                address = `[${address}]`;
            }
            address = `http://${address}`;
        }
        const url = new URL(address);
        url.port = hostPort;
        const result = url.toString();
        return url.pathname === '/' && !url.search && !url.hash ? result.replace(/\/$/, '') : result;
    } catch (_error) {
        return null;
    }
}

function getAccessLinks(container) {
    return getPortMappings(container)
        .filter(mapping => mapping.hostPort && (!mapping.protocol || mapping.protocol === 'tcp'))
        .map(mapping => ({ ...mapping, url: buildAccessUrl(mapping.hostPort) }))
        .filter(mapping => mapping.url);
}

function renderPortMappings(container) {
    const mappings = getPortMappings(container);
    if (mappings.length === 0) return '无';
    return `<div class="detail-port-list">${mappings.map(mapping => {
        const url = (!mapping.protocol || mapping.protocol === 'tcp') ? buildAccessUrl(mapping.hostPort) : null;
        const link = url
            ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" title="打开 ${escapeHtml(url)}"><i class="fas fa-external-link-alt"></i></a>`
            : '';
        return `<div class="detail-port-item"><span>${escapeHtml(mapping.value)}</span>${link}</div>`;
    }).join('')}</div>`;
}

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[character]));
}

async function loadSidebar() {
    return window.AppPage.loadSidebar('container');
}

function checkLogin() {
    return window.AppPage.requireLogin();
}

function loadUserInfo() {
    window.AppPage.populateUsername();
}

async function refreshContainers() {
    if (isRefreshingContainers) return;
    isRefreshingContainers = true;
    try {
        const response = await apiFetch(`${API_BASE_URL}/containers`);
        if (response.ok) {
            allContainers = await response.json();
            updateStats();
            filterContainers();
        } else {
            showEmptyState();
        }
    } catch (error) {
        console.error('Failed to load containers:', error);
        showEmptyState();
    } finally {
        isRefreshingContainers = false;
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
    
    const html = containers.map(container => {
        const accessLinks = getAccessLinks(container);
        const accessLinksHtml = accessLinks.length > 0 ? `
            <div class="container-port-links">
                ${accessLinks.map(mapping => `<a href="${escapeHtml(mapping.url)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(mapping.url)}"><i class="fas fa-link"></i>${mapping.hostPort}</a>`).join('')}
            </div>` : '';
        
        const runningActions = `
            <button class="action-btn stop" title="停止容器" onclick="stopContainer('${container.id}')">
                <i class="fas fa-stop"></i>
            </button>
            <button class="action-btn restart" title="重启容器" onclick="restartContainer('${container.id}')">
                <i class="fas fa-redo"></i>
            </button>
        `;
        
        const stoppedActions = `
            <button class="action-btn start" title="启动容器" onclick="startContainer('${container.id}')">
                <i class="fas fa-play"></i>
            </button>
        `;
        
        return `
        <div class="container-card ${container.state}">
            <div class="container-info">
                <div class="container-icon">
                    <i class="fab fa-docker"></i>
                </div>
                <div class="container-details">
                    <h3><span>${escapeHtml(container.name)}</span></h3>
                    <p>
                        <span><i class="fas fa-hashtag"></i> ${container.id.slice(0, 12)}</span>
                        <span><i class="fab fa-docker"></i> ${container.image.split('/').pop().split(':')[0]}</span>
                    </p>
                    ${accessLinksHtml}
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
                ${container.state === 'running' ? runningActions : stoppedActions}
                <button class="action-btn remove" title="删除容器" onclick="removeContainer('${container.id}', ${container.state === 'running'})">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        </div>
        `;
    }).join('');
    
    containerList.innerHTML = html;
}

function showEmptyState() {
    const containerList = document.getElementById('containersList');
    containerList.innerHTML = `
        <div class="empty-state">
            <i class="fas fa-inbox"></i>
            <p>暂无容器数据</p>
        </div>
    `;
}

async function showContainerDetail(containerId) {
    try {
        const response = await apiFetch(`${API_BASE_URL}/containers/${containerId}`);
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
                    <span class="detail-value">${renderPortMappings(container)}</span>
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
        const response = await apiFetch(`${API_BASE_URL}/containers/${containerId}/start`, {
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
        const response = await apiFetch(`${API_BASE_URL}/containers/${containerId}/stop`, {
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
        const response = await apiFetch(`${API_BASE_URL}/containers/${containerId}/restart`, {
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
        const response = await apiFetch(`${API_BASE_URL}/containers/${containerId}/logs?tail=200`);
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
