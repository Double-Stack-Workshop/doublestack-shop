export function initSidebar(currentPage) {
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.querySelector('.sidebar');
    const logoutBtn = document.getElementById('logoutBtn');
    const navItems = document.querySelectorAll('.sidebar-nav li');
    
    sidebarToggle.addEventListener('click', function() {
        sidebar.classList.toggle('collapsed');
        const icon = sidebarToggle.querySelector('i');
        if (sidebar.classList.contains('collapsed')) {
            icon.classList.remove('fa-chevron-left');
            icon.classList.add('fa-chevron-right');
        } else {
            icon.classList.remove('fa-chevron-right');
            icon.classList.add('fa-chevron-left');
        }
    });
    
    logoutBtn.addEventListener('click', async function() {
        if (confirm('确定要退出登录吗？')) {
            try {
                await fetch('/api/logout', { method: 'POST' });
            } catch (error) {
                // 即使请求失败也清除界面状态，避免用户误以为仍处于登录状态。
            }
            localStorage.removeItem('username');
            localStorage.removeItem('is_admin');
            localStorage.removeItem('remember');
            window.location.href = '../login/login.html';
        }
    });
    
    navItems.forEach(item => {
        const link = item.querySelector('a');
        if (link && link.href.includes(currentPage)) {
            item.classList.add('active');
        }
    });
}
