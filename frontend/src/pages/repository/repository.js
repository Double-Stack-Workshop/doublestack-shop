const { API_BASE_URL, apiFetch } = window.AppPage;

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    
    const addRepoBtn = document.getElementById('addRepoBtn');
    const initDefaultReposBtn = document.getElementById('initDefaultReposBtn');
    const addRepoModal = document.getElementById('addRepoModal');
    const addRepoForm = document.getElementById('addRepoForm');
    const searchInput = document.getElementById('searchInput');
    const filterSelect = document.getElementById('filterSelect');
    const repoTypeInput = document.getElementById('repoType');
    const repoTypeOptions = document.querySelectorAll('.repo-type-option');
    const localPathInput = document.getElementById('localPath');

    function selectRepoType(repoType) {
        repoTypeInput.value = repoType;
        repoTypeOptions.forEach((option) => {
            const selected = option.dataset.repoType === repoType;
            option.classList.toggle('active', selected);
            option.setAttribute('aria-checked', String(selected));
        });
        localPathInput.placeholder = repoType === 'script'
            ? '留空则扫描整个仓库内的 .sh 脚本'
            : '留空则扫描整个仓库内的 YML 文件';
    }
    
    checkLogin();
    loadUserInfo();
    
    addRepoBtn.addEventListener('click', function() {
        addRepoForm.reset();
        selectRepoType('compose');
        addRepoModal.classList.add('active');
    });

    repoTypeOptions.forEach((option) => {
        option.addEventListener('click', function () {
            selectRepoType(this.dataset.repoType);
        });
    });
    
    if (initDefaultReposBtn) {
        initDefaultReposBtn.addEventListener('click', async function() {
            const btn = this;
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>获取中...</span>';
            
            try {
                const response = await apiFetch(`${API_BASE_URL}/repos/init-default`, {
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
        const localPath = document.getElementById('localPath').value.trim();
        const repoType = repoTypeInput.value;
        
        if (!repoUrl) {
            showMessage('请填写仓库地址', 'error');
            return;
        }
        
        const submitBtn = addRepoForm.querySelector('.btn-submit');
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 添加中...';
        
        try {
            const response = await apiFetch(`${API_BASE_URL}/repos`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    name: repoName || null,
                    repo_url: repoUrl,
                    branch: branch,
                    local_path: localPath,
                    repo_type: repoType
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
    
    searchInput.addEventListener('input', applyRepoFilters);
    filterSelect.addEventListener('change', applyRepoFilters);
    
    loadRepos();
});

async function loadSidebar() {
    return window.AppPage.loadSidebar('repository');
}

let currentRepoName = '';

function applyRepoFilters() {
    const searchTerm = document.getElementById('searchInput').value.trim().toLowerCase();
    const selectedStatus = document.getElementById('filterSelect').value;

    document.querySelectorAll('.repo-card').forEach(card => {
        const matchesSearch = card.dataset.search.includes(searchTerm);
        const matchesStatus = selectedStatus === 'all' || card.dataset.status === selectedStatus;
        card.style.display = matchesSearch && matchesStatus ? 'flex' : 'none';
    });
}

function updateRepoStats(repos) {
    const countByStatus = status => repos.filter(repo => repo.status === status).length;
    document.getElementById('totalRepoCount').textContent = repos.length;
    document.getElementById('syncedRepoCount').textContent = countByStatus('active');
    document.getElementById('pendingRepoCount').textContent = countByStatus('pending');
    document.getElementById('failedRepoCount').textContent = countByStatus('error');
}

async function loadRepos() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/repos`);
        const repos = await response.json();
        
        const currentRepo = repos.find(r => r.is_current);
        currentRepoName = currentRepo ? currentRepo.name : '';
        
        const repoList = document.getElementById('repoList');
        updateRepoStats(repos);
        
        if (repos.length === 0) {
            repoList.innerHTML = `
                <div class="empty-state">
                    <i class="fab fa-github"></i>
                    <p>暂无仓库，点击右上角获取初始仓库</p>
                </div>
            `;
            return;
        }
        
        repoList.innerHTML = repos.map(repo => `
            <div class="repo-card ${repo.is_current ? 'current-repo' : ''}" data-name="${repo.name}" data-status="${repo.status}" data-search="${`${repo.name} ${repo.url}`.toLowerCase()}">
                <div class="repo-icon"><i class="fab fa-github"></i></div>
                <div class="repo-info">
                    <h3>${repo.name}<span class="repo-type-badge ${repo.repo_type === 'script' ? 'script' : 'compose'}">${repo.repo_type === 'script' ? 'Scripts' : 'Compose'}</span>${repo.is_current ? ' <span class="current-badge">当前系统仓库</span>' : ''}</h3>
                    <p>${repo.url}</p>
                    <div class="repo-meta">
                    <div class="meta-item">
                        <i class="fas ${repo.repo_type === 'script' ? 'fa-terminal' : 'fa-file-code'}"></i>
                        <span>${repo.file_count ?? repo.yml_count} 个${repo.repo_type === 'script' ? '脚本' : ' YML 文件'}</span>
                    </div>
                    <div class="meta-item">
                        <i class="fas fa-sync-alt"></i>
                        <span>上次同步: ${repo.last_sync || '从未'}</span>
                    </div>
                    </div>
                </div>
                <div class="repo-status-wrap">${getRepoStatusBadgeHTML(repo.status)}</div>
                <div class="repo-actions">
                    <button class="action-btn view-btn" title="查看文件" onclick="viewRepo('${repo.name}')">
                        <i class="fas fa-eye"></i>
                    </button>
                    <button class="action-btn sync-btn ${repo.status === 'syncing' ? 'disabled' : ''}" 
                            title="${repo.status === 'syncing' ? '同步中' : '同步仓库'}"
                            onclick="syncRepo('${repo.name}', this)"
                            ${repo.status === 'syncing' ? 'disabled' : ''}>
                        <i class="fas fa-refresh ${repo.status === 'syncing' ? 'fa-spin' : ''}"></i>
                    </button>
                    ${repo.repo_type !== 'script' ? `<button class="action-btn set-current-btn ${repo.is_current ? 'active' : ''}"
                            title="${repo.is_current ? '已设为当前' : '设为当前'}"
                            onclick="setCurrentRepo('${repo.name}', this)">
                        <i class="fas fa-star ${repo.is_current ? 'fa-solid' : 'fa-regular'}"></i>
                    </button>` : ''}
                </div>
            </div>
        `).join('');
        applyRepoFilters();
    } catch (error) {
        console.error('加载仓库失败:', error);
        showMessage('加载仓库列表失败，请检查后端服务', 'error');
    }
}

async function setCurrentRepo(repoName, btn) {
    const card = btn.closest('.repo-card');
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/current-repo`, {
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
                b.classList.remove('active');
                icon.className = 'fas fa-star fa-regular';
                b.title = '设为当前';
            });
            
            btn.classList.add('active');
            btn.querySelector('i').className = 'fas fa-star fa-solid';
            btn.title = '已设为当前';
            
            document.querySelectorAll('.repo-info h3').forEach(h3 => {
                h3.innerHTML = h3.textContent.replace(' <span class="current-badge">当前系统仓库</span>', '');
            });
            card.querySelector('.repo-info h3').innerHTML += ' <span class="current-badge">当前系统仓库</span>';
            
            showMessage(result.message, 'success');
            await loadRepos();
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
        'pending': '未同步',
        'error': '同步失败'
    };
    return statusMap[status] || status;
}

// 状态徽章统一 HTML 结构（内层，不含外层 div）——所有地方共用同一份模板
function getRepoStatusInnerHTML(status, text) {
    const displayText = text || getStatusText(status);
    return `<span class="status-dot"></span><span>${displayText}</span>`;
}

// 完整的状态徽章 HTML（含外层 div），用于 renderRepos 这种一次性字符串渲染
function getRepoStatusBadgeHTML(status) {
    return `<div class="repo-status ${status}">${getRepoStatusInnerHTML(status)}</div>`;
}

// 安全切换仓库状态 class：先清掉所有 4 种状态类，再加新的，避免双 class 叠加导致 CSS 覆盖
function setRepoStatusClass(statusElement, newStatus, text) {
    statusElement.classList.remove('active', 'syncing', 'pending', 'error');
    statusElement.classList.add(newStatus);
    statusElement.innerHTML = getRepoStatusInnerHTML(newStatus, text);
}

async function syncRepo(repoName, btn) {
    if (btn.classList.contains('disabled')) return;
    
    const card = btn.closest('.repo-card');
    const statusElement = card.querySelector('.repo-status');
    
    setRepoStatusClass(statusElement, 'syncing');
    
    btn.classList.add('disabled');
    btn.title = '同步中';
    btn.innerHTML = '<i class="fas fa-refresh fa-spin"></i>';
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/repos/${repoName}/sync`, {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (response.ok && result.success) {
            setRepoStatusClass(statusElement, 'active');
            card.dataset.status = 'active';
            
            btn.classList.remove('disabled');
            btn.title = '同步仓库';
            btn.innerHTML = '<i class="fas fa-refresh"></i>';
            
            showMessage(result.message, 'success');
            
            const metaItems = card.querySelectorAll('.meta-item');
            metaItems[0].innerHTML = `<i class="fas fa-file-code"></i><span>${result.data.yml_count} 个 YML 文件</span>`;
            metaItems[1].innerHTML = `<i class="fas fa-sync-alt"></i><span>上次同步: 刚刚</span>`;
            await loadRepos();
        } else {
            setRepoStatusClass(statusElement, 'error');
            card.dataset.status = 'error';
            
            btn.classList.remove('disabled');
            btn.title = '重试同步';
            btn.innerHTML = '<i class="fas fa-refresh"></i>';
            
            showMessage(result.message || '同步失败', 'error');
            await loadRepos();
        }
    } catch (error) {
        setRepoStatusClass(statusElement, 'error');
        card.dataset.status = 'error';
        
        btn.classList.remove('disabled');
        btn.title = '重试同步';
        btn.innerHTML = '<i class="fas fa-refresh"></i>';
        
        showMessage('网络错误，请检查后端服务', 'error');
        applyRepoFilters();
    }
}

async function viewRepo(repoName) {
    try {
        const response = await apiFetch(`${API_BASE_URL}/repos/${repoName}`);
        const repo = await response.json();
        
        if (response.ok) {
            const isScriptRepo = repo.repo_type === 'script';
            const fileLabel = isScriptRepo ? '脚本' : 'YML 文件';
            let ymlList = '';
            if (repo.yml_files && repo.yml_files.length > 0) {
                ymlList = repo.yml_files.map(file => `
                    <div class="yml-item">
                        <i class="fas ${isScriptRepo ? 'fa-terminal' : 'fa-file-code'}"></i>
                        <span>${file.name}</span>
                        <span class="yml-path">${file.path}</span>
                    </div>
                `).join('');
            } else {
                ymlList = `<p class="no-yml">未找到 ${fileLabel}</p>`;
            }
            
            const modal = document.createElement('div');
            modal.className = 'modal-overlay active';
            modal.id = 'viewRepoModal';
            const fileCount = repo.yml_files ? repo.yml_files.length : 0;
            modal.innerHTML = `
                <div class="modal-content view-modal">
                    <div class="modal-header">
                        <h2>${repo.name} - ${fileLabel}列表 (${fileCount}个)</h2>
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
    return window.AppPage.requireLogin();
}

function loadUserInfo() {
    window.AppPage.populateUsername();
}

async function initDefaultRepos() {
    const btn = document.getElementById('initDefaultReposBtn');
    if (!btn) return;
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>获取中...</span>';
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/repos/init-default`, {
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
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        padding: 60px 20px;
        color: #64748b;
    }
    
    .empty-state i {
        width: 64px;
        height: 64px;
        border-radius: 50%;
        background: #e2e8f0;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 32px;
        margin-bottom: 16px;
        color: #94a3b8;
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
