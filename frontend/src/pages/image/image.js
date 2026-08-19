const API_BASE_URL = '/api';

function convertToUTC8(dateStr) {
    if (!dateStr) return '未知';
    try {
        const date = new Date(dateStr);
        if (isNaN(date.getTime())) return dateStr;
        return date.toLocaleString('zh-CN', {
            timeZone: 'Asia/Shanghai',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        }).replace(/\//g, '-');
    } catch {
        return dateStr;
    }
}

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    
    const pullImageBtn = document.getElementById('pullImageBtn');
    const searchDockerHubBtn = document.getElementById('searchDockerHubBtn');
    const pullImageModal = document.getElementById('pullImageModal');
    const dockerHubModal = document.getElementById('dockerHubModal');
    const pullImageForm = document.getElementById('pullImageForm');
    const searchInput = document.getElementById('searchInput');
    const dockerHubSearch = document.getElementById('dockerHubSearch');
    const searchBtn = document.getElementById('searchBtn');
    
    checkLogin();
    loadUserInfo();
    
    pullImageBtn.addEventListener('click', function() {
        pullImageModal.classList.add('active');
    });
    
    searchDockerHubBtn.addEventListener('click', function() {
        dockerHubModal.classList.add('active');
    });
    
    function formatDuration2(sec) {
        if (sec == null || isNaN(sec)) return '—';
        sec = Number(sec);
        if (sec < 60) return `${sec.toFixed(1)}s`;
        const m = Math.floor(sec / 60);
        const s = sec - m * 60;
        return `${m}m${s.toFixed(0)}s`;
    }

    function formatShortTime2(isoStr) {
        if (!isoStr) return '';
        try {
            const d = new Date(isoStr);
            const hh = String(d.getHours()).padStart(2, '0');
            const mm = String(d.getMinutes()).padStart(2, '0');
            const ss = String(d.getSeconds()).padStart(2, '0');
            return `${hh}:${mm}:${ss}`;
        } catch {
            return '';
        }
    }

    function resetPullProgress() {
        const pg = document.getElementById('pullProgressGroup');
        if (pg) pg.style.display = 'block';
        const bar = document.getElementById('pullProgressBar');
        const pct = document.getElementById('pullProgressPct');
        const tm = document.getElementById('pullProgressTime');
        const det = document.getElementById('pullProgressDetail');
        if (bar) bar.style.width = '0%';
        if (pct) pct.textContent = '0%';
        if (tm) tm.textContent = '用时 0.0s';
        if (det) det.textContent = '准备中...';
    }

    pullImageForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        const imageName = document.getElementById('imageName').value.trim();
        const tag = document.getElementById('tag').value.trim() || 'latest';

        if (!imageName) {
            showMessage('请输入镜像名称', 'error');
            return;
        }

        const fullImageName = imageName.includes(':') ? imageName : `${imageName}:${tag}`;

        const submitBtn = pullImageForm.querySelector('.btn-submit');
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 拉取中...';

        resetPullProgress();

        const pullLogGroup = document.getElementById('pullLogGroup');
        const pullLogContainer = document.getElementById('pullLogContainer');
        pullLogGroup.style.display = 'block';
        pullLogContainer.innerHTML = '';

        const addPullLog = (type, msg, ts) => {
            const item = document.createElement('div');
            item.className = `pull-log-item ${type}`;
            const tm = formatShortTime2(ts);
            const timeHtml = tm ? `<span class="pull-log-time">[${tm}]</span>` : '';
            const safeMsg = String(msg).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
            item.innerHTML = `${timeHtml}<span class="pull-log-msg">${safeMsg}</span>`;
            pullLogContainer.appendChild(item);
            pullLogContainer.scrollTop = pullLogContainer.scrollHeight;
        };

        const updatePullProgress = (ev) => {
            const bar = document.getElementById('pullProgressBar');
            const pctEl = document.getElementById('pullProgressPct');
            const tmEl = document.getElementById('pullProgressTime');
            const detEl = document.getElementById('pullProgressDetail');
            if (!bar || !pctEl) return;
            const pct = Math.max(0, Math.min(100, Number(ev.percent) || 0));
            bar.style.width = `${pct}%`;
            pctEl.textContent = `${pct.toFixed(1)}%`;
            if (tmEl) {
                let tm = `用时 ${formatDuration2(ev.elapsed_sec)}`;
                if (ev.eta_sec != null && ev.eta_sec > 0 && pct < 99.9) tm += ` · 剩余 ${formatDuration2(ev.eta_sec)}`;
                tmEl.textContent = tm;
            }
            if (detEl && ev.detail) detEl.textContent = ev.detail;
        };

        let success = false;
        let finalMsg = '';
        let finalElapsed = 0;

        try {
            const response = await fetch(`${API_BASE_URL}/images/pull`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    image_name: fullImageName
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                let idx;
                while ((idx = buffer.indexOf('\n\n')) >= 0) {
                    const raw = buffer.slice(0, idx);
                    buffer = buffer.slice(idx + 2);
                    if (!raw.startsWith('data:')) continue;
                    const jsonStr = raw.slice(5).trim();
                    if (!jsonStr) continue;
                    let event;
                    try {
                        event = JSON.parse(jsonStr);
                    } catch {
                        continue;
                    }
                    if (event.type === 'log') {
                        addPullLog('info', event.message, event.ts);
                    } else if (event.type === 'progress') {
                        updatePullProgress(event);
                    } else if (event.type === 'done') {
                        success = event.success;
                        finalMsg = event.message;
                        finalElapsed = Number(event.elapsed_sec) || 0;
                        // 拉取完成，强制补 100% 显示
                        updatePullProgress({
                            percent: 100,
                            elapsed_sec: finalElapsed,
                            eta_sec: 0,
                            detail: event.success ? '拉取完成' : '拉取失败',
                        });
                        addPullLog(event.success ? 'success' : 'error',
                                   event.message + (finalElapsed ? `，耗时 ${formatDuration2(finalElapsed)}` : ''),
                                   event.ts);
                    }
                }
            }

            if (success) {
                showMessage(finalMsg, 'success');
                setTimeout(() => {
                    closeModal('pullImageModal');
                    pullImageForm.reset();
                    pullLogGroup.style.display = 'none';
                    const pg = document.getElementById('pullProgressGroup');
                    if (pg) pg.style.display = 'none';
                }, 800);
                await loadImages();
            } else {
                showMessage(finalMsg || '拉取失败', 'error');
            }
        } catch (error) {
            showMessage('网络错误，请检查后端服务', 'error');
            addPullLog('error', '网络错误: ' + error.message);
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '拉取';
        }
    });
    
    searchBtn.addEventListener('click', async function() {
        await searchDockerHub();
    });
    
    dockerHubSearch.addEventListener('keyup', function(e) {
        if (e.key === 'Enter') {
            searchDockerHub();
        }
    });
    
    searchInput.addEventListener('input', function(e) {
        const searchTerm = e.target.value.toLowerCase();
        const imageCards = document.querySelectorAll('.image-card');
        
        imageCards.forEach(card => {
            const imageName = card.querySelector('.image-info h3').textContent.toLowerCase();
            
            if (imageName.includes(searchTerm)) {
                card.style.display = 'block';
            } else {
                card.style.display = 'none';
            }
        });
    });
    
    loadImages();
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
            initSidebar('image');
        });
    };
    document.head.appendChild(script);
}

async function loadImages() {
    try {
        const response = await fetch(`${API_BASE_URL}/images`);
        const images = await response.json();
        
        const imageGrid = document.getElementById('imageGrid');
        const imageCount = document.getElementById('imageCount');
        const totalSize = document.getElementById('totalSize');
        
        if (!Array.isArray(images) || images.length === 0) {
            imageGrid.innerHTML = `
                <div class="empty-state">
                    <i class="fab fa-docker"></i>
                    <p>暂无镜像，点击右上角拉取镜像</p>
                </div>
            `;
            imageCount.textContent = '0';
            totalSize.textContent = '0 B';
            return;
        }
        
        imageCount.textContent = images.length;
        
        const totalBytes = images.reduce((sum, img) => sum + (img.size || 0), 0);
        totalSize.textContent = formatSize(totalBytes);
        
        imageGrid.innerHTML = images.map(image => `
            <div class="image-card" data-id="${image.id}">
                <div class="image-header">
                    <div class="image-icon">
                        <i class="fab fa-docker"></i>
                    </div>
                    <div class="image-info">
                        <h3>${image.name || image.repo_tags?.[0] || 'unknown'}</h3>
                        <p>${image.id?.substring(0, 12)}...</p>
                    </div>
                </div>
                <div class="image-tags">
                    ${(image.repo_tags || []).slice(0, 3).map(tag => `<span class="tag-item">${tag}</span>`).join('')}
                    ${(image.repo_tags?.length || 0) > 3 ? `<span class="tag-item">+${(image.repo_tags.length - 3)} more</span>` : ''}
                </div>
                <div class="image-meta">
                    <div class="meta-item">
                        <i class="fas fa-hdd"></i>
                        <span>${formatSize(image.size)}</span>
                    </div>
                    <div class="meta-item">
                        <i class="fas fa-calendar"></i>
                        <span>${convertToUTC8(image.created_at || image.created_since) || '未知'}</span>
                    </div>
                </div>
                <div class="image-actions">
                    <button class="action-btn delete-btn" onclick="deleteImage('${image.id}', this)">
                        <i class="fas fa-trash"></i>
                        <span>删除</span>
                    </button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('加载镜像失败:', error);
        showMessage('加载镜像列表失败，请检查后端服务', 'error');
    }
}

async function deleteImage(imageId, btn) {
    if (!confirm('确定要删除此镜像吗？此操作不可撤销。')) {
        return;
    }
    
    const card = btn.closest('.image-card');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>删除中</span>';
    
    try {
        const response = await fetch(`${API_BASE_URL}/images/${imageId}`, {
            method: 'DELETE'
        });
        
        const result = await response.json();
        
        if (response.ok && result.success) {
            showMessage(result.message, 'success');
            card.remove();
            
            const imageGrid = document.getElementById('imageGrid');
            if (imageGrid.querySelectorAll('.image-card').length === 0) {
                imageGrid.innerHTML = `
                    <div class="empty-state">
                        <i class="fas fa-image"></i>
                        <p>暂无镜像，点击右上角拉取镜像</p>
                    </div>
                `;
            }
            
            await loadImages();
        } else {
            showMessage(result.message || '删除失败', 'error');
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-trash"></i><span>删除</span>';
        }
    } catch (error) {
        showMessage('网络错误，请检查后端服务', 'error');
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-trash"></i><span>删除</span>';
    }
}

async function searchDockerHub() {
    const query = dockerHubSearch.value.trim();
    
    if (!query) {
        showMessage('请输入搜索关键词', 'error');
        return;
    }
    
    const searchResults = document.getElementById('searchResults');
    searchResults.innerHTML = `
        <div class="empty-search">
            <i class="fas fa-spinner fa-spin"></i>
            <p>搜索中...</p>
        </div>
    `;
    
    try {
        const response = await fetch(`${API_BASE_URL}/images/search?query=${encodeURIComponent(query)}`);
        const results = await response.json();
        
        if (!Array.isArray(results) || results.length === 0) {
            searchResults.innerHTML = `
                <div class="empty-search">
                    <i class="fas fa-search"></i>
                    <p>未找到匹配的镜像</p>
                </div>
            `;
            return;
        }
        
        searchResults.innerHTML = results.map(result => `
            <div class="search-result-item" onclick="selectDockerHubImage('${result.name}')">
                <h4>${result.name}</h4>
                <p>${result.description || '暂无描述'}</p>
                <div class="tags">
                    ${(result.tags || ['latest']).slice(0, 5).map(tag => `<span class="tag">${tag}</span>`).join('')}
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('搜索 DockerHub 失败:', error);
        searchResults.innerHTML = `
            <div class="empty-search">
                <i class="fas fa-exclamation-circle"></i>
                <p>搜索失败，请重试</p>
            </div>
        `;
    }
}

function selectDockerHubImage(imageName) {
    closeModal('dockerHubModal');
    document.getElementById('imageName').value = imageName;
    pullImageModal.classList.add('active');
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('active');
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

function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatDate(timestamp) {
    if (!timestamp) return '未知';
    const date = new Date(timestamp * 1000);
    return date.toLocaleString('zh-CN');
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
`;
document.head.appendChild(style);
