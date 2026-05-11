const API_BASE_URL = '/api';

document.getElementById('registerForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const confirmPassword = document.getElementById('confirmPassword').value;
    const adminPassword = document.getElementById('adminPassword').value;
    const errorMessage = document.getElementById('errorMessage');
    
    errorMessage.classList.remove('show');
    
    if (!username || !password || !confirmPassword || !adminPassword) {
        errorMessage.textContent = '请填写所有字段';
        errorMessage.classList.add('show');
        return;
    }
    
    if (password !== confirmPassword) {
        errorMessage.textContent = '两次输入的密码不一致';
        errorMessage.classList.add('show');
        return;
    }
    
    if (password.length < 6) {
        errorMessage.textContent = '密码长度至少为6位';
        errorMessage.classList.add('show');
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE_URL}/register`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                username,
                password,
                admin_password: adminPassword
            })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            alert('注册成功！请登录');
            window.location.href = '/';
        } else {
            errorMessage.textContent = data.message || '注册失败';
            errorMessage.classList.add('show');
        }
    } catch (error) {
        errorMessage.textContent = '网络错误，请稍后重试';
        errorMessage.classList.add('show');
    }
});
