const API_BASE_URL = '/api';

document.addEventListener('DOMContentLoaded', function() {
    const loginForm = document.getElementById('loginForm');
    
    loginForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;
        const remember = document.getElementById('remember').checked;
        
        if (!username || !password) {
            showMessage('请输入用户名和密码', 'error');
            return;
        }
        
        showLoading(true);
        
        try {
            const response = await fetch(`${API_BASE_URL}/login`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    username: username,
                    password: password
                })
            });
            
            const result = await response.json();
            
            if (response.ok && result.success) {
                if (remember) {
                    localStorage.setItem('username', result.data.username);
                    localStorage.setItem('is_admin', result.data.is_admin ? 'true' : 'false');
                    localStorage.setItem('remember', 'true');
                } else {
                    localStorage.setItem('username', result.data.username);
                    localStorage.setItem('is_admin', result.data.is_admin ? 'true' : 'false');
                    localStorage.removeItem('remember');
                }
                
                showMessage('登录成功，正在跳转...', 'success');
                setTimeout(() => {
                    window.location.href = '../../pages/dashboard/dashboard.html';
                }, 1500);
            } else {
                showMessage(result.message || '登录失败', 'error');
                showLoading(false);
            }
        } catch (error) {
            showMessage('网络错误，请检查后端服务', 'error');
            showLoading(false);
        }
    });
    
    if (localStorage.getItem('remember') === 'true') {
        document.getElementById('username').value = localStorage.getItem('username') || '';
        document.getElementById('remember').checked = true;
    }
    
    // 链接使用默认行为跳转，不需要额外处理
});

function showMessage(message, type) {
    const messageBox = document.createElement('div');
    messageBox.className = `message message-${type}`;
    messageBox.textContent = message;
    
    document.querySelector('.login-box').appendChild(messageBox);
    
    setTimeout(() => {
        messageBox.classList.add('fade-out');
        setTimeout(() => {
            messageBox.remove();
        }, 300);
    }, 2000);
}

function showLoading(loading) {
    const btn = document.querySelector('.login-btn');
    if (loading) {
        btn.innerHTML = '<span class="loading"></span> 登录中...';
        btn.disabled = true;
    } else {
        btn.innerHTML = '登录';
        btn.disabled = false;
    }
}