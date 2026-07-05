const API_BASE_URL = '/api';

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    
    const addRepoBtn = document.getElementById('addRepoBtn');
    const initDefaultReposBtn = document.getElementById('initDefaultReposBtn');
    const addRepoModal = document.getElementById('addRepoModal');
    const addRepoForm = document.getElementById('addRepoForm');
    const searchInput = document.getElementById('searchInput');
    const filterSelect = document.getElementById('filterSelect');
    
    checkLogin();
    loadUserInfo();
    
    addRepoBtn.addEventListener('click', function() {
        addRepoModal.classList.add('active');
    });
    
    if (initDefaultReposBtn) {
        initDefaultReposBtn.addEventListener('click', async function() {
            const btn = this;
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>获取中...</span>';
            
            try {
                const response = await fetch(`${API_BASE_URL}/repos/init-default`, {
                    method: 'POST'
                });
                
                const result = await response.json();
                
                if (response.ok && result.success) {
                    showMessage(result.message, 'success');
                    await loadRepos();
                } else {
                    showMessage(result.message || '获取失败', 'error');
                }
            } catch (error) {
                showMessage('网络错误，请检查后端服务', 'error');
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-download"></i><span>获取初始仓库</span>';
            }
        });
    }
    
    addRepoForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        const repoName = document.getElementById('repoName').value.trim();
        const repoUrl = document.getElementById('repoUrl').value;
        const branch = document.getElementById('branch').value;
        const localPath = document.getElementById('localPath').value;
        
        if (!repoUrl || !localPath) {
            showMessage('请填写仓库地址和本地路径', 'error');
            return;
        }
        
        const submitBtn = addRepoForm.querySelector('.btn-submit');
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 添加中...';
        
        try {
            const response = await fetch(`${API_BASE_URL}/repos`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    name: repoName || null,
                    repo_url: repoUrl,
                    branch: branch,
                    local_path: localPath
                })
            });
            
            const result = await response.json();
            
            if (response.ok && result.success) {
                showMessage(result.message, 'success');
                closeModal();
                addRepoForm.reset();
                await loadRepos();
            } else {
                showMessage(result.message || '添加失败', 'error');
            }
        } catch (error) {
            showMessage('网络错误，请检查后端服务', 'error');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '添加';
        }
    });
    
    searchInput.addEventListener('input', function(e) {
        const searchTerm = e.target.value.toLowerCase();
        const repoCards = document.querySelectorAll('.repo-card');
        
        repoCards.forEach(card => {
            const repoName = card.querySelector('.repo-info h3').textContent.toLowerCase();
            const repoUrl = card.querySelector('.repo-info p').textContent.toLowerCase();
            
            if (repoName.includes(searchTerm) || repoUrl.includes(searchTerm)) {
                card.style.display = 'block';
            } else {
                card.style.display = 'none';
            }
        });
    });
    
    filterSelect.addEventListener('change', function(e) {
        const status = e.target.value;
        const repoCards = document.querySelectorAll('.repo-card');
        
        repoCards.forEach(card => {
            const statusElement = card.querySelector('.repo-status');
            
            if (status === 'all') {
                card.style.display = 'block';
            } else if (statusElement.classList.contains(status)) {
                card.style.display = 'block';
            } else {
                card.style.display = 'none';
            }
        });
    });
    
    loadRepos();
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
            initSidebar('repository');
        });
    };
    document.head.appendChild(script);
}

let currentRepoName = '';

async function loadRepos() {
    try {
        const response = await fetch(`${API_BASE_URL}/repos`);
        const repos = await response.json();
        
        const currentRepo = repos.find(r => r.is_current);
        currentRepoName = currentRepo ? currentRepo.name : '';
        
        const repoGrid = document.querySelector('.repo-grid');
        
        if (repos.length === 0) {
            repoGrid.innerHTML = `
                <div class="empty-state">
                    <i class="fab fa-github"></i>
                    <p>暂无仓库，点击右上角获取初始仓库</p>
                </div>
            `;
            return;
        }
        
        repoGrid.innerHTML = repos.map(repo => `
            <div class="repo-card ${repo.is_current ? 'current-repo' : ''}" data-name="${repo.name}">
                <div class="repo-header">
                    <div class="repo-icon">
                        <i class="fab fa-github"></i>
                    </div>
                    <div class="repo-info">
                        <h3>${repo.name}${repo.is_current ? ' <span class="current-badge">当前系统仓库</span>' : ''}</h3>
                        <p>${repo.url}</p>
                    </div>
                    <div class="repo-status ${repo.status}">
                        <span class="status-dot"></span>
                        <span>${getStatusText(repo.status)}</span>
                    </div>
                </div>
                <div class="repo-meta">
                    <div class="meta-item">
                        <i class="fas fa-file-code"></i>
                        <span>${repo.yml_count} 个 YML 文件</span>
                    </div>
                    <div class="meta-item">
                        <i class="fas fa-sync-alt"></i>
                        <span>上次同步: ${repo.last_sync || '从未'}</span>
                    </div>
                </div>
                <div class="repo-actions">
                    <button class="action-btn view-btn" onclick="viewRepo('${repo.name}')">
                        <i class="fas fa-eye"></i>
                        <span>查看文件</span>
                    </button>
                    <button class="action-btn sync-btn ${repo.status === 'syncing' ? 'disabled' : ''}" 
                            onclick="syncRepo('${repo.name}', this)"
                            ${repo.status === 'syncing' ? 'disabled' : ''}>
                        <i class="fas fa-refresh ${repo.status === 'syncing' ? 'fa-spin' : ''}"></i>
                        <span>${repo.status === 'syncing' ? '同步中' : '同步'}</span>
                    </button>
                    <button class="action-btn set-current-btn ${repo.is_current ? 'active' : ''}" 
                            onclick="setCurrentRepo('${repo.name}', this)">
                        <i class="fas fa-star ${repo.is_current ? 'fa-solid' : 'fa-regular'}"></i>
                        <span>${repo.is_current ? '已设为当前' : '设为当前'}</span>
                    </button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('加载仓库失败:', error);
        showMessage('加载仓库列表失败，请检查后端服务', 'error');
    }
}

async function setCurrentRepo(repoName, btn) {
    const card = btn.closest('.repo-card');
    
    try {
        const response = await fetch(`${API_BASE_URL}/current-repo`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ repo_name: repoName })
        });
        
        const result = await response.json();
        
        if (response.ok && result.success) {
            currentRepoName = repoName;
            
            document.querySelectorAll('.repo-card').forEach(c => {
                c.classList.remove('current-repo');
            });
            card.classList.add('current-repo');
            
            document.querySelectorAll('.set-current-btn').forEach(b => {
                const icon = b.querySelector('i');
                const span = b.querySelector('span');
                b.classList.remove('active');
                icon.className = 'fas fa-star fa-regular';
                span.textContent = '设为当前';
            });
            
            btn.classList.add('active');
            btn.querySelector('i').className = 'fas fa-star fa-solid';
            btn.querySelector('span').textContent = '已设为当前';
            
            document.querySelectorAll('.repo-info h3').forEach(h3 => {
                h3.innerHTML = h3.textContent.replace(' <span class="current-badge">当前系统仓库</span>', '');
            });
            card.querySelector('.repo-info h3').innerHTML += ' <span class="current-badge">当前系统仓库</span>';
            
            showMessage(result.message, 'success');
        } else {
            showMessage(result.message || '设置失败', 'error');
        }
    } catch (error) {
        showMessage('网络错误，请检查后端服务', 'error');
    }
}

function getStatusText(status) {
    const statusMap = {
        'active': '已同步',
        'syncing': '同步中',
        'error': '同步失败'
    };
    return statusMap[status] || status;
}

async function syncRepo(repoName, btn) {
    if (btn.classList.contains('disabled')) return;
    
    const card = btn.closest('.repo-card');
    const statusElement = card.querySelector('.repo-status');
    
    statusElement.classList.remove('active', 'error');
    statusElement.classList.add('syncing');
    statusElement.innerHTML = '<span class="status-dot"></span><span>同步中</span>';
    
    btn.classList.add('disabled');
    btn.innerHTML = '<i class="fas fa-refresh fa-spin"></i><span>同步中</span>';
    
    try {
        const response = await fetch(`${API_BASE_URL}/repos/${repoName}/sync`, {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (response.ok && result.success) {
            statusElement.classList.remove('syncing');
            statusElement.classList.add('active');
            statusElement.innerHTML = '<span class="status-dot"></span><span>已同步</span>';
            
            btn.classList.remove('disabled');
            btn.innerHTML = '<i class="fas fa-refresh"></i><span>同步</span>';
            
            showMessage(result.message, 'success');
            
            const metaItems = card.querySelectorAll('.meta-item');
            metaItems[0].innerHTML = `<i class="fas fa-file-code"></i><span>${result.data.yml_count} 个 YML 文件</span>`;
            metaItems[1].innerHTML = `<i class="fas fa-sync-alt"></i><span>上次同步: 刚刚</span>`;
        } else {
            statusElement.classList.remove('syncing');
            statusElement.classList.add('error');
            statusElement.innerHTML = '<span class="status-dot"></span><span>同步失败</span>';
            
            btn.classList.remove('disabled');
            btn.innerHTML = '<i class="fas fa-refresh"></i><span>重试</span>';
            
            showMessage(result.message || '同步失败', 'error');
        }
    } catch (error) {
        statusElement.classList.remove('syncing');
        statusElement.classList.add('error');
        statusElement.innerHTML = '<span class="status-dot"></span><span>同步失败</span>';
        
        btn.classList.remove('disabled');
        btn.innerHTML = '<i class="fas fa-refresh"></i><span>重试</span>';
        
        showMessage('网络错误，请检查后端服务', 'error');
    }
}

async function viewRepo(repoName) {
    try {
        const response = await fetch(`${API_BASE_URL}/repos/${repoName}`);
        const repo = await response.json();
        
        if (response.ok) {
            let ymlList = '';
            if (repo.yml_files && repo.yml_files.length > 0) {
                ymlList = repo.yml_files.map(file => `
                    <div class="yml-item">
                        <i class="fas fa-file-code"></i>
                        <span>${file.name}</span>
                        <span class="yml-path">${file.path}</span>
                    </div>
                `).join('');
            } else {
                ymlList = '<p class="no-yml">未找到 YML 文件</p>';
            }
            
            const modal = document.createElement('div');
            modal.className = 'modal-overlay active';
            modal.id = 'viewRepoModal';
            const fileCount = repo.yml_files ? repo.yml_files.length : 0;
            modal.innerHTML = `
                <div class="modal-content view-modal">
                    <div class="modal-header">
                        <h2>${repo.name} - YML 文件列表 (${fileCount}个)</h2>
                        <button class="modal-close" onclick="closeViewModal()">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="yml-list">
                        ${ymlList}
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        } else {
            showMessage('获取仓库信息失败', 'error');
        }
    } catch (error) {
        showMessage('网络错误，请检查后端服务', 'error');
    }
}

function closeModal() {
    const addRepoModal = document.getElementById('addRepoModal');
    addRepoModal.classList.remove('active');
}

function closeViewModal() {
    const modal = document.getElementById('viewRepoModal');
    if (modal) {
        modal.remove();
    }
}

function showMessage(message, type) {
    const messageBox = document.createElement('div');
    messageBox.className = `message message-${type}`;
    messageBox.textContent = message;
    
    document.querySelector('.main-content').appendChild(messageBox);
    
    setTimeout(() => {
        messageBox.classList.add('fade-out');
        setTimeout(() => {
            messageBox.remove();
        }, 300);
    }, 3000);
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

async function initDefaultRepos() {
    const btn = document.getElementById('initDefaultReposBtn');
    if (!btn) return;
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>获取中...</span>';
    
    try {
        const response = await fetch(`${API_BASE_URL}/repos/init-default`, {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (response.ok && result.success) {
            showMessage(result.message, 'success');
            await loadRepos();
        } else {
            showMessage(result.message || '获取失败', 'error');
        }
    } catch (error) {
        showMessage('网络错误，请检查后端服务', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-download"></i><span>获取初始仓库</span>';
    }
}

const style = document.createElement('style');
style.textContent = `
    .message {
        position: fixed;
        top: 100px;
        right: 30px;
        padding: 16px 24px;
        border-radius: 12px;
        color: white;
        font-size: 14px;
        font-weight: 500;
        z-index: 300;
        animation: slideInRight 0.3s ease;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
    }
    
    .message-success {
        background: linear-gradient(135deg, #10b981 0%, #059669 100%);
    }
    
    .message-error {
        background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
    }
    
    @keyframes slideInRight {
        from {
            opacity: 0;
            transform: translateX(50px);
        }
        to {
            opacity: 1;
            transform: translateX(0);
        }
    }
    
    .fade-out {
        opacity: 0;
        transform: translateX(50px);
        transition: all 0.3s ease;
    }
    
    .empty-state {
        grid-column: 1 / -1;
        text-align: center;
        padding: 60px 20px;
        color: #64748b;
    }
    
    .empty-state i {
        font-size: 48px;
        margin-bottom: 16px;
        color: #cbd5e1;
    }
    
    .view-modal {
        max-width: 600px;
    }
    
    .yml-list {
        max-height: 400px;
        overflow-y: auto;
        padding: 20px;
    }
    
    .yml-item {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 12px 16px;
        border-radius: 10px;
        background: #f8fafc;
        margin-bottom: 8px;
        transition: all 0.3s ease;
    }
    
    .yml-item:hover {
        background: #f1f5f9;
    }
    
    .yml-item i {
        color: #667eea;
    }
    
    .yml-path {
        color: #94a3b8;
        font-size: 12px;
        margin-left: auto;
    }
    
    .no-yml {
        text-align: center;
        color: #94a3b8;
        padding: 40px;
    }
`;
document.head.appendChild(style);