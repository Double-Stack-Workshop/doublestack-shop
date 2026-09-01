const { API_BASE_URL, apiFetch, syncCurrentUser } = window.AppPage;
const form = document.getElementById('changePasswordForm');
const submitButton = document.getElementById('submitPassword');
const message = document.getElementById('passwordMessage');
const logoutButton = document.getElementById('logoutBtn');

async function initialize() {
    try {
        const user = await syncCurrentUser({ allowPasswordChange: true });
        if (!user) {
            message.textContent = '无法确认登录状态，请退出后重新登录';
            return;
        }
        if (!user.must_change_password) {
            window.location.replace('/src/pages/dashboard/dashboard.html');
            return;
        }
        submitButton.disabled = false;
        document.getElementById('newPassword').focus();
    } catch (error) {
        message.textContent = '无法确认登录状态，请刷新页面重试';
    }
}

function clearUser() {
    localStorage.removeItem('username');
    localStorage.removeItem('is_admin');
    localStorage.removeItem('remember');
}

form.addEventListener('submit', async (event) => {
    event.preventDefault();
    message.classList.remove('success');
    message.textContent = '';
    const newPassword = document.getElementById('newPassword').value;
    const confirmPassword = document.getElementById('confirmPassword').value;
    if (newPassword !== confirmPassword) {
        message.textContent = '两次输入的新密码不一致';
        return;
    }
    if (Array.from(newPassword).length < 8 || new TextEncoder().encode(newPassword).length > 72) {
        message.textContent = '新密码至少为 8 位，且 UTF-8 编码不能超过 72 字节';
        return;
    }
    submitButton.disabled = true;
    try {
        const response = await apiFetch(`${API_BASE_URL}/change-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_password: newPassword }),
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            message.textContent = typeof result.detail === 'string' ? result.detail : (result.message || '修改失败，请检查输入');
            submitButton.disabled = false;
            return;
        }
        form.reset();
        clearUser();
        message.classList.add('success');
        message.textContent = '密码修改成功，请使用新密码重新登录';
        setTimeout(() => window.location.replace('/src/pages/login/login.html'), 1500);
    } catch (error) {
        message.textContent = '网络错误，请稍后重试';
        submitButton.disabled = false;
    }
});

logoutButton.addEventListener('click', async () => {
    logoutButton.disabled = true;
    try {
        const response = await fetch(`${API_BASE_URL}/logout`, { method: 'POST' });
        if (!response.ok && response.status !== 401) throw new Error('退出失败');
        clearUser();
        window.location.replace('/src/pages/login/login.html');
    } catch (error) {
        message.textContent = '退出失败，请重试';
        logoutButton.disabled = false;
    }
});

initialize();
