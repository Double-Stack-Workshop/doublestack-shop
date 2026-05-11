const API_BASE_URL = '/api';

async function loadVersion() {
    try {
        const response = await fetch(`${API_BASE_URL}/system/version`);
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
            const response = await fetch(`${API_BASE_URL}/register`, {
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
                showMessage(data.detail || '添加失败', 'error');
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
            const response = await fetch(`${API_BASE_URL}/users/${username}`, {
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
    const response = await fetch('/src/components/sidebar/sidebar.html');
    const sidebarHtml = await response.text();
    const appContainer = document.getElementById('appContainer');
    appContainer.insertAdjacentHTML('afterbegin', sidebarHtml);
    
    const script = document.createElement('script');
    script.src = '/src/components/sidebar/sidebar.js';
    script.type = 'module';
    script.onload = function() {
        import('/src/components/sidebar/sidebar.js').then(({ initSidebar }) => {
            initSidebar('settings');
        });
    };
    document.head.appendChild(script);
}

async function loadUsers() {
    try {
        const response = await fetch(`${API_BASE_URL}/users`);
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
        const response = await fetch(`${API_BASE_URL}/users/${username}`, {
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
        const response = await fetch(`${API_BASE_URL}/system/check-update`);
        const result = await response.json();
        
        if (result.success) {
            if (result.update_available) {
                statusEl.innerHTML = `<i class="fas fa-arrow-up"></i> 有新版本可用: ${result.latest_version}`;
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
    const username = localStorage.getItem('username');
    const isAdmin = localStorage.getItem('is_admin') === 'true';
    
    if (!username) {
        window.location.href = '../login/login.html';
        return false;
    }
    
    if (!isAdmin) {
        alert('您没有权限访问此页面');
        window.location.href = '../dashboard/dashboard.html';
        return false;
    }
    
    return true;
}

function loadUserInfo() {
    const username = localStorage.getItem('username');
    
    if (username) {
        document.getElementById('currentUsername').textContent = username;
    }
}