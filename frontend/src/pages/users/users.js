const { API_BASE_URL, apiFetch } = window.AppPage;

document.addEventListener('DOMContentLoaded', async function() {
    if (!window.AppPage.requireLogin()) return;
    await window.AppPage.loadSidebar('users');
    if (!window.AppPage.requireLogin({ admin: true })) return;
    window.AppPage.populateUsername();
    bindUserEvents();
    await loadUsers();
});

function bindUserEvents() {
    document.getElementById('addUserBtn').addEventListener('click', () => openModal('addUserModal'));
    document.getElementById('addUserForm').addEventListener('submit', addUser);
    document.getElementById('editUserForm').addEventListener('submit', updateUser);
    document.getElementById('confirmDeleteBtn').addEventListener('click', confirmDeleteUser);
    document.getElementById('userTableBody').addEventListener('click', handleUserAction);
    document.querySelectorAll('[data-close-modal]').forEach(button => {
        button.addEventListener('click', () => closeModal(button.dataset.closeModal));
    });
    document.querySelectorAll('.modal-overlay').forEach(modal => {
        modal.addEventListener('mousedown', event => {
            if (event.target === modal) closeModal(modal.id);
        });
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') document.querySelectorAll('.modal-overlay.active').forEach(modal => closeModal(modal.id));
    });
}

async function loadUsers() {
    const tbody = document.getElementById('userTableBody');
    tbody.replaceChildren(createStatusRow('加载中...'));
    try {
        const response = await apiFetch(`${API_BASE_URL}/users`);
        if (!response.ok) throw new Error(await readError(response, '获取用户列表失败'));
        renderUsers(await response.json());
    } catch (error) {
        tbody.replaceChildren(createStatusRow(error.message || '获取用户列表失败'));
    }
}

function renderUsers(users) {
    const tbody = document.getElementById('userTableBody');
    tbody.replaceChildren();
    if (!Array.isArray(users) || users.length === 0) {
        tbody.appendChild(createStatusRow('暂无用户'));
        return;
    }
    users.forEach(user => tbody.appendChild(createUserRow(user)));
}

function createStatusRow(message) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 4;
    cell.className = 'empty-row';
    cell.textContent = message;
    row.appendChild(cell);
    return row;
}

function createUserRow(user) {
    const username = String(user.username || '');
    const row = document.createElement('tr');
    const userCell = document.createElement('td');
    const identity = document.createElement('div');
    identity.className = 'user-identity';
    const avatar = document.createElement('img');
    avatar.className = 'user-list-avatar';
    avatar.src = `${API_BASE_URL}/users/${encodeURIComponent(username)}/avatar?v=${encodeURIComponent(user.avatar_filename || 'default')}`;
    avatar.alt = `${username} 的头像`;
    avatar.onerror = () => { avatar.src = '/src/images/logo.png'; };
    const name = document.createElement('span');
    name.textContent = username;
    identity.append(avatar, name);
    userCell.appendChild(identity);

    const createdCell = document.createElement('td');
    createdCell.textContent = formatDate(user.created_at);
    const lastLoginCell = document.createElement('td');
    lastLoginCell.textContent = formatDate(user.last_login_at);
    const actionCell = document.createElement('td');
    actionCell.className = 'user-actions';
    actionCell.appendChild(createActionButton('edit', username, 'fa-pen', '修改'));
    if (!user.is_admin) actionCell.appendChild(createActionButton('delete', username, 'fa-trash', '删除'));
    row.append(userCell, createdCell, lastLoginCell, actionCell);
    return row;
}

function createActionButton(action, username, iconName, label) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `app-button app-button-compact ${action === 'delete' ? 'app-button-danger' : 'app-button-secondary'}`;
    button.dataset.action = action;
    button.dataset.username = username;
    const icon = document.createElement('i');
    icon.className = `fas ${iconName}`;
    const text = document.createElement('span');
    text.textContent = label;
    button.append(icon, text);
    return button;
}

function handleUserAction(event) {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    if (button.dataset.action === 'edit') openEditUser(button.dataset.username);
    if (button.dataset.action === 'delete') openDeleteUser(button.dataset.username);
}

async function addUser(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const username = document.getElementById('newUsername').value.trim();
    const password = document.getElementById('newPassword').value;
    const avatar = document.getElementById('newAvatar').files[0];
    await submitUserForm(form, '添加中...', async () => {
        const response = await apiFetch(`${API_BASE_URL}/users`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        if (!response.ok) throw new Error(await readError(response, '添加失败'));
        let avatarError = null;
        if (avatar) {
            try { await uploadAvatar(username, avatar); }
            catch (error) { avatarError = error; }
        }
        closeModal('addUserModal');
        showMessage(avatarError ? `用户已创建，${avatarError.message}` : '用户添加成功', avatarError ? 'error' : 'success');
        await loadUsers();
    });
}

async function updateUser(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const originalUsername = document.getElementById('originalUsername').value;
    const username = document.getElementById('editUsername').value.trim();
    const password = document.getElementById('editPassword').value;
    await submitUserForm(form, '保存中...', async () => {
        const body = { username };
        if (password) body.password = password;
        const response = await apiFetch(`${API_BASE_URL}/users/${encodeURIComponent(originalUsername)}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
        });
        if (!response.ok) throw new Error(await readError(response, '修改失败'));
        closeModal('editUserModal');
        await window.AppPage.syncCurrentUser();
        window.AppPage.populateUsername();
        showMessage('用户信息修改成功', 'success');
        await loadUsers();
    });
}

async function submitUserForm(form, busyLabel, operation) {
    const button = form.querySelector('.submit-btn');
    const original = button.textContent;
    button.disabled = true;
    button.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${busyLabel}`;
    try { await operation(); } catch (error) { showMessage(error.message || '网络错误，请稍后重试', 'error'); }
    finally { button.disabled = false; button.textContent = original; }
}

function openEditUser(username) {
    document.getElementById('originalUsername').value = username;
    document.getElementById('editUsername').value = username;
    document.getElementById('editPassword').value = '';
    openModal('editUserModal');
}

function openDeleteUser(username) {
    document.getElementById('deleteUsernameLabel').textContent = username;
    document.getElementById('confirmDeleteBtn').dataset.username = username;
    openModal('deleteUserModal');
}

async function confirmDeleteUser() {
    const button = document.getElementById('confirmDeleteBtn');
    const username = button.dataset.username;
    const original = button.textContent;
    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 删除中...';
    try {
        const response = await apiFetch(`${API_BASE_URL}/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(await readError(response, '删除失败'));
        closeModal('deleteUserModal');
        showMessage('用户删除成功', 'success');
        await loadUsers();
    } catch (error) { showMessage(error.message || '网络错误，请稍后重试', 'error'); }
    finally { button.disabled = false; button.textContent = original; }
}

async function uploadAvatar(username, file) {
    const data = new FormData();
    data.append('file', file);
    const response = await apiFetch(`${API_BASE_URL}/users/${encodeURIComponent(username)}/avatar`, {
        method: 'POST', body: data
    });
    if (!response.ok) throw new Error(await readError(response, '用户已创建，但头像上传失败'));
}

function openModal(id) {
    const modal = document.getElementById(id);
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => modal.querySelector('input:not([type="hidden"])')?.focus());
}

function closeModal(id) {
    const modal = document.getElementById(id);
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    modal.querySelector('form')?.reset();
}

async function readError(response, fallback) {
    const data = await response.json().catch(() => ({}));
    if (Array.isArray(data.detail)) return data.detail.map(item => item.msg).filter(Boolean).join('；') || fallback;
    return data.detail || data.message || fallback;
}

function formatDate(value) {
    if (!value) return '-';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    });
}

function showMessage(message, type = 'info') {
    const toast = document.getElementById('messageToast');
    toast.textContent = message;
    toast.className = `message-toast ${type} show`;
    clearTimeout(showMessage.timer);
    showMessage.timer = setTimeout(() => toast.classList.remove('show'), 3000);
}
