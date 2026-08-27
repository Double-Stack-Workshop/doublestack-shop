const { API_BASE_URL, apiFetch } = window.AppPage;
let allBackups = [];
let allContainers = [];
let currentBackupId = null;

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    checkLogin();
    loadUserInfo();
    await loadContainers();
    await refreshBackups();
    
    setInterval(refreshBackups, 30000);
});

async function loadSidebar() {
    return window.AppPage.loadSidebar('backup');
}

function checkLogin() {
    return window.AppPage.requireLogin();
}

function loadUserInfo() {
    window.AppPage.populateUsername();
}

async function loadContainers() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/containers`);
        if (response.ok) {
            allContainers = await response.json();
        } else {
            allContainers = [];
        }
    } catch (error) {
        console.error('Failed to load containers:', error);
        allContainers = [];
    }
    updateStats();
    renderContainers(allContainers);
}

function updateStats() {
    const total = allContainers.length;
    const running = allContainers.filter(c => c.state === 'running').length;
    
    const backedContainerNames = new Set(allBackups.map(b => b.container_name));
    const backed = backedContainerNames.size;
    const unbacked = total - backed;
    const success = allBackups.filter(b => b.status === 'success' || !b.status).length;
    
    document.getElementById('totalCount').textContent = total;
    document.getElementById('runningCount').textContent = running;
    document.getElementById('backedCount').textContent = backed;
    document.getElementById('unbackedCount').textContent = unbacked;
    document.getElementById('successCount').textContent = success;
}

async function refreshBackups() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/backups`);
        if (response.ok) {
            allBackups = await response.json();
        } else {
            allBackups = [];
        }
    } catch (error) {
        console.error('Failed to load backups:', error);
        allBackups = [];
    }
    updateStats();
    renderContainers(allContainers);
}

function formatStorage(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function filterContainers() {
    const searchQuery = document.getElementById('searchInput').value.toLowerCase();
    
    let filtered = allContainers;
    
    if (searchQuery) {
        filtered = filtered.filter(c => 
            c.name.toLowerCase().includes(searchQuery)
        );
    }
    
    renderContainers(filtered);
}

function renderContainers(containers) {
    const containersList = document.getElementById('containersList');
    
    if (!containers || containers.length === 0) {
        showEmptyState();
        return;
    }
    
    const backedContainerNames = new Set(allBackups.map(b => b.container_name));
    
    containersList.innerHTML = containers.map(container => {
        const hasBackup = backedContainerNames.has(container.name);
        const lastBackup = allBackups.filter(b => b.container_name === container.name).sort((a, b) => new Date(b.created_at) - new Date(a.created_at))[0];
        
        return `
        <div class="container-card ${container.state === 'running' ? 'running' : 'exited'}">
            <div class="container-info">
                <div class="container-icon">
                    <i class="fab fa-docker"></i>
                </div>
                <div class="container-details">
                    <h3>${container.name}</h3>
                    <p>
                        <span><i class="fas fa-hashtag"></i> ${container.id ? container.id.slice(0, 12) : ''}</span>
                        <span><i class="fab fa-docker"></i> ${container.image || ''}</span>
                    </p>
                </div>
            </div>
            <div class="container-status">
                <span class="status-badge ${container.state === 'running' ? 'running' : 'exited'}">
                    <i class="fas fa-circle"></i>
                    ${container.state === 'running' ? '运行中' : '已停止'}
                </span>
                <span style="font-size: 13px; color: #6b7280;">
                    ${hasBackup ? `<i class="fas fa-check-circle" style="color: #22c55e;"></i> 已备份` : `<i class="fas fa-times-circle" style="color: #f59e0b;"></i> 未备份`}
                </span>
            </div>
            <div class="container-actions">
                <button class="action-btn backup" title="创建备份" onclick="showCreateBackupModal('${container.id}')">
                    <i class="fas fa-download"></i>
                </button>
                ${hasBackup ? `
                <button class="action-btn view-backups" title="查看备份" onclick="viewContainerBackups('${container.name}')">
                    <i class="fas fa-file-text"></i>
                </button>
                ` : ''}
            </div>
        </div>
    `}).join('');
}

function showEmptyState() {
    const containersList = document.getElementById('containersList');
    containersList.innerHTML = `
        <div class="empty-state">
            <i class="fab fa-docker"></i>
            <p>暂无容器数据</p>
        </div>
    `;
}

let currentBackupContainerId = null;

function showCreateBackupModal(containerId) {
    currentBackupContainerId = containerId;
    
    if (containerId) {
        const container = allContainers.find(c => c.id === containerId);
        if (container) {
            document.getElementById('containerNameDisplay').value = container.name;
            document.getElementById('backupName').value = `${container.name}-backup`;
        }
    }
    
    document.getElementById('createBackupModalOverlay').style.display = 'flex';
}

function closeCreateBackupModal() {
    document.getElementById('createBackupModalOverlay').style.display = 'none';
    document.getElementById('containerNameDisplay').value = '';
    document.getElementById('backupName').value = '';
    currentBackupContainerId = null;
}

async function createBackup() {
    const containerId = currentBackupContainerId;
    
    if (!containerId) {
        alert('请选择要备份的容器');
        return;
    }
    
    const modal = document.getElementById('createBackupModalOverlay');
    const submitBtn = modal.querySelector('.btn-primary');
    const originalText = submitBtn.textContent;
    submitBtn.textContent = '备份中...';
    submitBtn.disabled = true;
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/containers/${containerId}/backup`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        const result = await response.json();
        
        if (result.success) {
            closeCreateBackupModal();
            await refreshBackups();
            alert('备份创建成功\n\n备份步骤:\n' + result.data.steps.join('\n'));
        } else {
            alert(result.message || '创建备份失败');
        }
    } catch (error) {
        console.error('Failed to create backup:', error);
        alert('创建备份失败: ' + error.message);
    } finally {
        submitBtn.textContent = originalText;
        submitBtn.disabled = false;
    }
}

async function showBackupDetail(backupId) {
    currentBackupId = backupId;
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/backups/${backupId}`);
        if (response.ok) {
            const backup = await response.json();
            document.getElementById('detailModalTitle').textContent = `备份详情 - ${backup.name}`;
            document.getElementById('detailModalBody').innerHTML = `
                <div class="detail-row">
                    <span class="detail-label">备份名称</span>
                    <span class="detail-value">${backup.name}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">备份ID</span>
                    <span class="detail-value">${backup.id || '-'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">所属容器</span>
                    <span class="detail-value">${backup.container_name || '-'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">备份大小</span>
                    <span class="detail-value">${backup.size ? formatStorage(backup.size) : '-'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">创建时间</span>
                    <span class="detail-value">${backup.created_at || '-'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">描述</span>
                    <span class="detail-value">${backup.description || '-'}</span>
                </div>
            `;
            document.getElementById('backupDetailModalOverlay').style.display = 'flex';
        }
    } catch (error) {
        console.error('Failed to get backup detail:', error);
    }
}

function closeBackupDetailModal() {
    document.getElementById('backupDetailModalOverlay').style.display = 'none';
    currentBackupId = null;
    document.getElementById('deleteBackupBtn').style.display = 'inline-block';
}

async function deleteBackup(backupId) {
    const id = backupId || currentBackupId;
    if (!id) return;
    
    if (!confirm('确定要删除此备份吗？此操作不可撤销！')) return;
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/backups/${id}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            closeBackupDetailModal();
            await refreshBackups();
            alert('备份删除成功');
        } else {
            const result = await response.json();
            alert(result.message || '删除失败');
        }
    } catch (error) {
        console.error('Failed to delete backup:', error);
        alert('删除备份失败');
    }
}

async function restoreBackup(backupId) {
    if (!confirm('确定要恢复此备份吗？这将覆盖现有容器数据！')) return;
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/backups/${backupId}/restore`, {
            method: 'POST'
        });
        
        if (response.ok) {
            await refreshBackups();
            alert('备份恢复成功');
        } else {
            const result = await response.json();
            alert(result.message || '恢复失败');
        }
    } catch (error) {
        console.error('Failed to restore backup:', error);
        alert('恢复备份失败');
    }
}

async function downloadBackup(backupId) {
    try {
        const response = await apiFetch(`${API_BASE_URL}/backups/${backupId}/download`);
        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            const contentDisposition = response.headers.get('Content-Disposition');
            const match = contentDisposition ? contentDisposition.match(/filename="?([^"]+)"?/) : null;
            const filename = match ? match[1] : `backup-${backupId}.tar`;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        } else {
            const result = await response.json();
            alert(result.message || '下载失败');
        }
    } catch (error) {
        console.error('Failed to download backup:', error);
        alert('下载备份失败');
    }
}

let selectedRestoreFile = null;

function handleRestoreFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    if (!file.name.endsWith('.tar')) {
        alert('请选择 .tar 格式的备份文件');
        event.target.value = '';
        return;
    }
    
    selectedRestoreFile = file;
    
    document.getElementById('restoreFileName').textContent = file.name;
    document.getElementById('restoreFileSize').textContent = formatStorage(file.size);
    
    document.getElementById('fileUploadArea').style.display = 'none';
    document.getElementById('restoreInfo').style.display = 'block';
    document.getElementById('restoreBtn').disabled = false;
}

async function performRestore() {
    if (!selectedRestoreFile) return;
    
    const btn = document.getElementById('restoreBtn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 恢复中...';
    btn.disabled = true;
    
    try {
        const formData = new FormData();
        formData.append('file', selectedRestoreFile);
        
        const response = await apiFetch(`${API_BASE_URL}/backups/restore-file`, {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            try {
                const errorData = await response.json();
                alert(`恢复失败 [${response.status}]: ${errorData.detail || errorData.message || '未知错误'}`);
            } catch (e) {
                alert(`恢复失败 [${response.status}]: ${response.statusText}`);
            }
            return;
        }
        
        const result = await response.json();
        
        if (result.success) {
            alert('备份恢复成功');
            resetRestoreArea();
            await loadContainers();
        } else {
            alert('恢复失败: ' + (result.message || result.detail || '未知错误'));
        }
    } catch (error) {
        console.error('Failed to restore backup:', error);
        alert('恢复备份失败: ' + error.message);
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

function resetRestoreArea() {
    selectedRestoreFile = null;
    document.getElementById('restoreFile').value = '';
    document.getElementById('fileUploadArea').style.display = 'block';
    document.getElementById('restoreInfo').style.display = 'none';
    document.getElementById('restoreBtn').disabled = true;
}

async function cleanOldBackups() {
    if (!confirm('确定要清理旧备份吗？这将删除所有超过30天的备份！')) return;
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/backups/clean`, {
            method: 'POST'
        });
        
        if (response.ok) {
            await refreshBackups();
            alert('旧备份清理成功');
        } else {
            const result = await response.json();
            alert(result.message || '清理失败');
        }
    } catch (error) {
        console.error('Failed to clean backups:', error);
        alert('清理备份失败');
    }
}

async function exportBackup() {
    alert('导出功能开发中...');
}

function formatTimeAgo(dateString) {
    if (!dateString) return '未知';
    
    let date;
    try {
        date = new Date(dateString);
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
    
    return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

document.getElementById('createBackupModalOverlay').addEventListener('click', function(e) {
    if (e.target === this) {
        closeCreateBackupModal();
    }
});

document.getElementById('backupDetailModalOverlay').addEventListener('click', function(e) {
    if (e.target === this) {
        closeBackupDetailModal();
    }
});

function viewContainerBackups(containerName) {
    const containerBackups = allBackups.filter(b => b.container_name === containerName);
    
    if (containerBackups.length === 0) {
        alert('该容器暂无备份');
        return;
    }
    
    let backupListHtml = `<div style="max-height: 500px; overflow-y: auto;">`;
    containerBackups.forEach(backup => {
        const statusColor = backup.status === 'success' ? '#22c55e' : backup.status === 'failed' ? '#ef4444' : '#6b7280';
        const statusText = backup.status === 'success' ? '成功' : backup.status === 'failed' ? '失败' : '未知';
        
        backupListHtml += `
            <div style="padding: 20px; border-bottom: 1px solid #f3f4f6;">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
                    <div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <strong style="font-size: 16px; color: #1a1a2e;">${backup.name}</strong>
                            <span style="padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 500; background-color: ${statusColor}20; color: ${statusColor};">
                                ${statusText}
                            </span>
                        </div>
                        <div style="font-size: 13px; color: #6b7280; margin-top: 6px;">
                            <span>${formatTimeAgo(backup.created_at)}</span>
                            <span style="margin: 0 8px;">·</span>
                            <span>${backup.size ? formatStorage(backup.size) : '-'}</span>
                        </div>
                    </div>
                    <div style="display: flex; gap: 8px;">
                        <button class="action-btn backup" title="下载备份" onclick="downloadBackup('${backup.id}')">
                            <i class="fas fa-download"></i>
                        </button>
                        <button class="action-btn delete" title="删除备份" onclick="deleteBackup('${backup.id}'); closeBackupDetailModal();">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </div>
                <div style="background-color: #f8fafc; padding: 12px; border-radius: 8px;">
                    <div style="font-size: 12px; color: #64748b; font-weight: 500; margin-bottom: 6px;">备份内容</div>
                    <div style="font-size: 13px; color: #475569; display: flex; gap: 16px;">
                        <span><i class="fas fa-box" style="margin-right: 4px; color: #6366f1;"></i>镜像文件</span>
                        <span><i class="fas fa-file-code" style="margin-right: 4px; color: #06b6d4;"></i>容器配置</span>
                        <span><i class="fas fa-database" style="margin-right: 4px; color: #22c55e;"></i>数据卷</span>
                    </div>
                </div>
            </div>
        `;
    });
    backupListHtml += `</div>`;
    
    document.getElementById('detailModalTitle').textContent = `${containerName} 的备份列表`;
    document.getElementById('detailModalBody').innerHTML = backupListHtml;
    document.getElementById('deleteBackupBtn').style.display = 'none';
    document.getElementById('backupDetailModalOverlay').style.display = 'flex';
}