const API_BASE_URL = '/api';

document.getElementById('forgotForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const adminPassword = document.getElementById('adminPassword').value;
    const newPassword = document.getElementById('newPassword').value;
    const confirmPassword = document.getElementById('confirmPassword').value;
    const errorMessage = document.getElementById('errorMessage');
    const successMessage = document.getElementById('successMessage');
    
    errorMessage.classList.remove('show');
    successMessage.classList.remove('show');
    
    if (!adminPassword || !newPassword || !confirmPassword) {
        errorMessage.textContent = '请填写所有字段';
        errorMessage.classList.add('show');
        return;
    }
    
    if (newPassword !== confirmPassword) {
        errorMessage.textContent = '两次输入的密码不一致';
        errorMessage.classList.add('show');
        return;
    }
    
    if (newPassword.length < 6) {
        errorMessage.textContent = '密码长度至少为6位';
        errorMessage.classList.add('show');
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE_URL}/users/forgot-password`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 
                admin_password: adminPassword,
                new_password: newPassword
            })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            successMessage.textContent = data.message || '密码重置成功，请使用新密码登录';
            successMessage.classList.add('show');
            document.getElementById('adminPassword').value = '';
            document.getElementById('newPassword').value = '';
            document.getElementById('confirmPassword').value = '';
            
            setTimeout(() => {
                window.location.href = '/';
            }, 2000);
        } else {
            errorMessage.textContent = data.message || '操作失败';
            errorMessage.classList.add('show');
        }
    } catch (error) {
        errorMessage.textContent = '网络错误，请稍后重试';
        errorMessage.classList.add('show');
    }
});