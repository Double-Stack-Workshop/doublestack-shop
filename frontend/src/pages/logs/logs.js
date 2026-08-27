const { API_BASE_URL, apiFetch } = window.AppPage;
let allLogs = [];

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    checkLogin();
    loadUserInfo();
    await loadLogs();
    
    // 监听筛选变化
    document.getElementById('levelFilter').addEventListener('change', filterLogs);
    document.getElementById('typeFilter').addEventListener('change', filterLogs);
});

async function loadSidebar() {
    return window.AppPage.loadSidebar('logs');
}

function checkLogin() {
    return window.AppPage.requireLogin();
}

function loadUserInfo() {
    window.AppPage.populateUsername();
}

async function loadLogs() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/logs`);
        if (response.ok) {
            const data = await response.json();
            allLogs = data.logs || [];
            renderLogs(allLogs);
        } else {
            renderEmptyState();
        }
    } catch (error) {
        console.error('Failed to load logs:', error);
        renderEmptyState();
    }
}

function refreshLogs() {
    loadLogs();
}

function filterLogs() {
    const level = document.getElementById('levelFilter').value;
    const type = document.getElementById('typeFilter').value;
    
    let filteredLogs = allLogs;
    
    if (level) {
        filteredLogs = filteredLogs.filter(log => log.level === level);
    }
    
    if (type) {
        filteredLogs = filteredLogs.filter(log => log.type === type);
    }
    
    renderLogs(filteredLogs);
}

function renderLogs(logs) {
    const logsBody = document.getElementById('logsBody');
    
    if (!logs || logs.length === 0) {
        renderEmptyState();
        return;
    }
    
    logsBody.innerHTML = logs.map((log, index) => {
        const levelClass = log.level.toLowerCase();
        const levelText = getLevelText(log.level);
        const typeText = getTypeText(log.type);
        const time = formatTime(log.timestamp);
        const hasDetails = log.details && log.details.length > 0;
        const detailBtn = hasDetails ? `
            <button class="btn-detail" onclick="showDetailModal(${index})">
                <i class="fas fa-eye"></i>
                查看详情
            </button>
        ` : '';
        
        return `
            <div class="log-entry">
                <div class="col-level">
                    <span class="level-badge ${levelClass}">
                        <i class="fas ${getLevelIcon(log.level)}"></i>
                        ${levelText}
                    </span>
                </div>
                <div class="col-time">${time}</div>
                <div class="col-type">${typeText}</div>
                <div class="col-message">${escapeHtml(log.message)}</div>
                <div class="col-action">${detailBtn}</div>
            </div>
        `;
    }).join('');
}

function showDetailModal(logIndex) {
    const log = allLogs[logIndex];
    if (!log || !log.details || log.details.length === 0) {
        return;
    }
    
    const modal = document.getElementById('detailModal');
    const modalBody = document.getElementById('modalBody');
    
    const detailHtml = log.details.map((detail, idx) => {
        let logClass = 'info';
        if (detail.includes('[部署成功]') || detail.includes('[成功]')) {
            logClass = 'success';
        } else if (detail.includes('[错误]') || detail.includes('[失败]')) {
            logClass = 'error';
        } else if (detail.includes('[警告]')) {
            logClass = 'warning';
        }
        return `<div class="detail-log-item ${logClass}">${escapeHtml(detail)}</div>`;
    }).join('');
    
    modalBody.innerHTML = `<div class="detail-logs">${detailHtml}</div>`;
    modal.style.display = 'flex';
}

function closeDetailModal() {
    const modal = document.getElementById('detailModal');
    modal.style.display = 'none';
}

function renderEmptyState() {
    const logsBody = document.getElementById('logsBody');
    logsBody.innerHTML = `
        <div class="logs-empty">
            <i class="fas fa-history"></i>
            <p>暂无日志记录</p>
        </div>
    `;
}

async function clearLogs() {
    if (!confirm('确定要清空所有日志吗？')) {
        return;
    }
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/logs`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            allLogs = [];
            renderEmptyState();
            alert('日志已清空');
        } else {
            alert('清空日志失败');
        }
    } catch (error) {
        console.error('Failed to clear logs:', error);
        alert('清空日志失败');
    }
}

function getLevelText(level) {
    const levels = {
        'INFO': '信息',
        'WARNING': '警告',
        'ERROR': '错误',
        'SUCCESS': '成功'
    };
    return levels[level] || level;
}

function getLevelIcon(level) {
    const icons = {
        'INFO': 'fa-info-circle',
        'WARNING': 'fa-exclamation-triangle',
        'ERROR': 'fa-times-circle',
        'SUCCESS': 'fa-check-circle'
    };
    return icons[level] || 'fa-info-circle';
}

function getTypeText(type) {
    const types = {
        'auth': '认证',
        'file': '文件',
        'system': '系统',
        'query': '查询',
        'deploy': '部署',
        'container': '容器',
        'image': '镜像',
        'backup': '备份'
    };
    return types[type] || type;
}

function formatTime(timestamp) {
    if (!timestamp) return '-';
    const date = new Date(timestamp);
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
