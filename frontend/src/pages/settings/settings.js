const { API_BASE_URL, apiFetch } = window.AppPage;

async function loadVersion() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/system/version`);
        const result = await response.json();
        if (result.current_version) {
            document.getElementById('currentVersion').textContent = result.current_version;
        }
        if (result.build_date) {
            document.getElementById('buildDate').textContent = result.build_date;
        }
    } catch (error) {
        console.error('Failed to load version:', error);
    }
}

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    
    const addUserBtn = document.getElementById('addUserBtn');
    const addUserModal = document.getElementById('addUserModal');
    const addUserForm = document.getElementById('addUserForm');
    const editPasswordForm = document.getElementById('editPasswordForm');
    
    if (!checkLogin()) return;
    loadUserInfo();
    loadVersion();
    loadProxyConfig();
    loadGlobalDomain();
    loadDockerMirrors();
    
    addUserBtn.addEventListener('click', function() {
        addUserModal.classList.add('active');
    });
    
    addUserForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        const username = document.getElementById('newUsername').value;
        const password = document.getElementById('newPassword').value;
        
        if (!username || !password) {
            showMessage('请填写用户名和密码', 'error');
            return;
        }
        
        const submitBtn = addUserForm.querySelector('.btn-submit');
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 添加中...';
        
        try {
            const response = await apiFetch(`${API_BASE_URL}/users`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ username, password })
            });
            
            if (response.ok) {
                showMessage('用户添加成功', 'success');
                closeAddUserModal();
                loadUsers();
            } else {
                const data = await response.json();
                showMessage(getErrorMessage(data, '添加失败'), 'error');
            }
        } catch (error) {
            showMessage('网络错误，请稍后重试', 'error');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '添加';
        }
    });
    
    editPasswordForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        const username = document.getElementById('editUsername').value;
        const password = document.getElementById('editPassword').value;
        
        if (!password) {
            showMessage('请输入新密码', 'error');
            return;
        }
        
        const submitBtn = editPasswordForm.querySelector('.btn-submit');
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 保存中...';
        
        try {
            const response = await apiFetch(`${API_BASE_URL}/users/${username}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ password })
            });
            
            if (response.ok) {
                showMessage('密码修改成功', 'success');
                closeEditPasswordModal();
            } else {
                const data = await response.json();
                showMessage(data.detail || '修改失败', 'error');
            }
        } catch (error) {
            showMessage(error.message || '网络错误，请稍后重试', 'error');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '保存';
        }
    });
    
    loadUsers();
});

async function loadSidebar() {
    return window.AppPage.loadSidebar('settings');
}

async function loadUsers() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/users`);
        if (response.ok) {
            const users = await response.json();
            renderUsers(users);
        } else {
            renderUsers([]);
        }
    } catch (error) {
        console.error('Failed to load users:', error);
        renderUsers([]);
    }
}

function renderUsers(users) {
    const tbody = document.getElementById('userTableBody');
    
    if (users.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="3" class="empty-row">暂无用户</td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = users.map(user => `
        <tr>
            <td>
                <div style="display: flex; align-items: center; gap: 10px;">
                    <div style="width: 32px; height: 32px; border-radius: 50%; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); display: flex; align-items: center; justify-content: center; color: white; font-size: 12px;">
                        ${user.username.charAt(0).toUpperCase()}
                    </div>
                    <span>${user.username}</span>
                </div>
            </td>
            <td>${formatDate(user.created_at)}</td>
            <td>
                <button class="action-btn edit" onclick="openEditPasswordModal('${user.username}')">
                    <i class="fas fa-key"></i>
                    <span>修改密码</span>
                </button>
                ${user.username !== 'admin' ? `
                    <button class="action-btn delete" onclick="deleteUser('${user.username}')">
                        <i class="fas fa-trash"></i>
                        <span>删除</span>
                    </button>
                ` : ''}
            </td>
        </tr>
    `).join('');
}

function openEditPasswordModal(username) {
    document.getElementById('editUsername').value = username;
    document.getElementById('editPassword').value = '';
    document.getElementById('editPasswordModal').classList.add('active');
}

function closeEditPasswordModal() {
    document.getElementById('editPasswordModal').classList.remove('active');
    document.getElementById('editPasswordForm').reset();
}

function closeAddUserModal() {
    document.getElementById('addUserModal').classList.remove('active');
    document.getElementById('addUserForm').reset();
}

async function deleteUser(username) {
    if (!confirm(`确定要删除用户 "${username}" 吗？`)) {
        return;
    }
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/users/${username}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            showMessage('用户删除成功', 'success');
            loadUsers();
        } else {
            const data = await response.json();
            showMessage(data.detail || '删除失败', 'error');
        }
    } catch (error) {
        showMessage('网络错误，请稍后重试', 'error');
    }
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function showMessage(message, type = 'info') {
    const toast = document.getElementById('messageToast');
    toast.textContent = message;
    toast.className = `message-toast ${type} show`;
    
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

async function checkForUpdates() {
    const btn = document.getElementById('checkUpdateBtn');
    const statusEl = document.getElementById('updateStatus');
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 检查中...';
    statusEl.innerHTML = '';
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/system/check-update`);
        const result = await response.json();
        
        if (result.success) {
            if (result.update_available) {
                statusEl.innerHTML = `<i class="fas fa-arrow-up"></i> 有新版本可用: ${result.latest_version}<br><span style="font-size:12px;color:#666;">更新脚本已生成，请前往终端运行: <code style="color:#22c55e;">bash /app/scripts/update_app.sh</code></span>`;
                statusEl.className = 'update-status available';
            } else {
                statusEl.innerHTML = `<i class="fas fa-check"></i> 当前已是最新版本`;
                statusEl.className = 'update-status latest';
            }
        } else {
            statusEl.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${result.message}`;
            statusEl.className = 'update-status error';
        }
    } catch (error) {
        statusEl.innerHTML = `<i class="fas fa-exclamation-circle"></i> 网络错误，无法检查更新`;
        statusEl.className = 'update-status error';
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-refresh"></i> 检查更新';
    }
}

function checkLogin() {
    return window.AppPage.requireLogin();
}

function loadUserInfo() {
    window.AppPage.populateUsername();
}

async function loadProxyConfig() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/proxy`);
        if (response.ok) {
            const result = await response.json();
            if (result.success && result.data) {
                document.getElementById('httpProxy').value = result.data.http_proxy || '';
                document.getElementById('httpsProxy').value = result.data.https_proxy || '';
            }
        }
    } catch (error) {
        console.error('Failed to load proxy config:', error);
    }
}

async function loadGlobalDomain() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/global-domain`);
        if (response.ok) {
            const result = await response.json();
            if (result.success && result.data) {
                document.getElementById('globalDomain').value = result.data.global_domain || '';
            }
        }
    } catch (error) {
        console.error('Failed to load global domain:', error);
    }
}

async function saveProxyConfig() {
    const httpProxy = document.getElementById('httpProxy').value.trim();
    const httpsProxy = document.getElementById('httpsProxy').value.trim();
    
    const btn = document.getElementById('saveProxyBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 保存中...';
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/proxy`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ http_proxy: httpProxy, https_proxy: httpsProxy })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showMessage('代理配置保存成功', 'success');
        } else {
            showMessage(result.message || '保存失败', 'error');
        }
    } catch (error) {
        showMessage('网络错误，请稍后重试', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-save"></i> 保存配置';
    }
}

async function saveGlobalDomain() {
    const globalDomain = document.getElementById('globalDomain').value.trim();
    
    const btn = document.getElementById('saveDomainBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 保存中...';
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/global-domain`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ global_domain: globalDomain })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showMessage('全局域名/IP配置保存成功', 'success');
        } else {
            showMessage(result.message || '保存失败', 'error');
        }
    } catch (error) {
        showMessage('网络错误，请稍后重试', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-save"></i> 保存配置';
    }
}

// Docker 加速源管理
let dockerMirrors = [];

async function loadDockerMirrors() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/docker-mirrors`);
        if (response.ok) {
            const result = await response.json();
            dockerMirrors = result.mirrors || [];
            renderMirrors();
        } else {
            renderMirrors();
        }
    } catch (error) {
        console.error('Failed to load docker mirrors:', error);
        renderMirrors();
    }
}

function renderMirrors() {
    const listEl = document.getElementById('mirrorList');
    
    if (!dockerMirrors || dockerMirrors.length === 0) {
        listEl.innerHTML = '<div class="mirror-empty">暂无加速源配置</div>';
        return;
    }
    
    listEl.innerHTML = dockerMirrors.map((mirror, index) => `
        <div class="mirror-item" draggable="true" data-index="${index}" 
             ondragstart="handleDragStart(event)" ondragend="handleDragEnd(event)">
            <span class="mirror-drag-handle"><i class="fas fa-grip-vertical"></i></span>
            <span class="mirror-url">${escapeHtml(mirror)}</span>
            <div class="mirror-actions">
                <button class="edit-btn" onclick="editMirror(${index})">
                    <i class="fas fa-edit"></i>
                </button>
                <button class="delete-btn" onclick="deleteMirror(${index})">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        </div>
    `).join('');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 拖拽排序功能
let draggedIndex = null;

function handleDragStart(event) {
    draggedIndex = parseInt(event.target.dataset.index);
    event.target.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
}

function handleDragEnd(event) {
    event.target.classList.remove('dragging');
    document.querySelectorAll('.mirror-item').forEach(item => {
        item.classList.remove('drag-over');
    });
}

function handleDragOver(event) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
}

function handleDrop(event) {
    event.preventDefault();
    
    const target = event.target.closest('.mirror-item');
    if (!target || draggedIndex === null) return;
    
    const targetIndex = parseInt(target.dataset.index);
    
    if (draggedIndex !== targetIndex) {
        const movedItem = dockerMirrors.splice(draggedIndex, 1)[0];
        dockerMirrors.splice(targetIndex, 0, movedItem);
        renderMirrors();
        showMessage('顺序已调整，请点击保存配置', 'success');
    }
    
    draggedIndex = null;
}

function addMirror() {
    const input = document.getElementById('newMirror');
    const url = input.value.trim();
    
    if (!url) {
        showMessage('请输入加速源地址', 'error');
        return;
    }
    
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
        showMessage('加速源地址必须以 http:// 或 https:// 开头', 'error');
        return;
    }
    
    if (dockerMirrors.includes(url)) {
        showMessage('该加速源已存在', 'error');
        return;
    }
    
    dockerMirrors.push(url);
    renderMirrors();
    input.value = '';
    showMessage('加速源已添加，请点击保存配置', 'success');
}

function addPresetMirror(url) {
    if (dockerMirrors.includes(url)) {
        showMessage('该加速源已存在', 'error');
        return;
    }
    
    dockerMirrors.push(url);
    renderMirrors();
    showMessage('加速源已添加，请点击保存配置', 'success');
}

function editMirror(index) {
    const currentUrl = dockerMirrors[index];
    const newUrl = prompt('编辑加速源地址:', currentUrl);
    
    if (newUrl === null) return;
    
    const trimmedUrl = newUrl.trim();
    if (!trimmedUrl) {
        showMessage('加速源地址不能为空', 'error');
        return;
    }
    
    if (!trimmedUrl.startsWith('http://') && !trimmedUrl.startsWith('https://')) {
        showMessage('加速源地址必须以 http:// 或 https:// 开头', 'error');
        return;
    }
    
    if (trimmedUrl !== currentUrl && dockerMirrors.includes(trimmedUrl)) {
        showMessage('该加速源已存在', 'error');
        return;
    }
    
    dockerMirrors[index] = trimmedUrl;
    renderMirrors();
    showMessage('加速源已修改，请点击保存配置', 'success');
}

function deleteMirror(index) {
    if (!confirm('确定要删除该加速源吗？')) {
        return;
    }
    
    dockerMirrors.splice(index, 1);
    renderMirrors();
    showMessage('加速源已删除，请点击保存配置', 'success');
}

async function saveDockerMirrors() {
    if (!confirm('保存后将自动重启宿主机 Docker 服务。运行中的容器会短暂中断，本应用会在服务恢复后自动刷新。确定继续吗？')) {
        return;
    }

    const btn = document.getElementById('saveMirrorBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 保存并重启中...';
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/docker-mirrors`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ mirrors: dockerMirrors })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showMessage('配置已保存，正在确认 Docker 服务恢复状态…', 'success');
            await waitForDockerRestart();
        } else {
            showMessage(result.message || '保存或重启失败', result.saved ? 'info' : 'error');
        }
    } catch (error) {
        showMessage('网络错误，请稍后重试', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-save"></i> 保存并重启 Docker';
    }
}

function getErrorMessage(data, fallback) {
    if (typeof data?.detail === 'string') return data.detail;
    if (typeof data?.message === 'string') return data.message;
    if (Array.isArray(data?.detail)) return data.detail.map((item) => item.msg).join('；');
    return fallback;
}

async function waitForDockerRestart() {
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
        try {
            const response = await apiFetch(`${API_BASE_URL}/docker-mirrors/restart-status`);
            if (response.ok) {
                const status = await response.json();
                if (status.state === 'ready') {
                    showMessage('Docker 已恢复，正在刷新页面', 'success');
                    window.location.reload();
                    return;
                }
                if (status.state === 'failed') {
                    showMessage(status.message || 'Docker 恢复失败，配置已尝试回滚', 'error');
                    return;
                }
            }
        } catch (_error) {
            // Docker 重启期间连接中断属于预期状态，继续轮询即可。
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    showMessage('尚未确认 Docker 恢复，请稍后刷新页面查看状态', 'error');
}

async function clearApplicationCache() {
    const button = document.getElementById('clearCacheBtn');
    button.disabled = true;
    try {
        await window.AppCache.clearCacheAndReload();
    } catch (error) {
        showMessage('缓存清理失败，请手动刷新页面', 'error');
        button.disabled = false;
    }
}
