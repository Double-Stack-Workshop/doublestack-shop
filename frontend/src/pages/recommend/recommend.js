const API_BASE_URL = '/api';

function goToDeploy(ymlFileName) {
    window.location.href = `/src/pages/deploy/deploy.html?search=${encodeURIComponent(ymlFileName)}`;
}

function openTutorial(url) {
    if (url) {
        window.open(url, '_blank');
    }
}

function checkLogin() {
    const username = localStorage.getItem('username');
    if (!username) {
        window.location.href = '/src/pages/login/login.html';
        return false;
    }
    return true;
}

function loadUserInfo() {
    const username = localStorage.getItem('username');
    const isAdmin = localStorage.getItem('is_admin') === 'true';
    
    if (username) {
        document.getElementById('currentUsername').textContent = username;
    }
}

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
            initSidebar('recommend');
        });
    };
    document.head.appendChild(script);
}

async function loadRecommendations() {
    const grid = document.getElementById('recommendGrid');
    
    try {
        const [currentRepoResponse, reposResponse, recommendCfgResponse] = await Promise.all([
            fetch(`${API_BASE_URL}/current-repo`),
            fetch(`${API_BASE_URL}/repos`),
            fetch(`${API_BASE_URL}/recommend-config`)
        ]);
        
        // 从后端加载推荐配置（读取自 data/recommend.json）
        let RECOMMEND_CONFIG = {};
        if (recommendCfgResponse.ok) {
            const cfgData = await recommendCfgResponse.json();
            if (cfgData && cfgData.success && cfgData.data) {
                RECOMMEND_CONFIG = cfgData.data;
            }
        }
        
        let currentRepoName = '';
        if (currentRepoResponse.ok) {
            const currentRepoData = await currentRepoResponse.json();
            currentRepoName = currentRepoData.data?.repo_name || '';
        }
        
        if (!currentRepoName) {
            const repos = await reposResponse.json();
            if (repos.length > 0) {
                currentRepoName = repos[0].name;
            }
        }
        
        if (!currentRepoName) {
            grid.innerHTML = `
                <div class="empty-state">
                    <i class="fab fa-docker"></i>
                    <p>暂无仓库，请先前往仓库管理添加仓库</p>
                </div>
            `;
            return;
        }
        
        const filesResponse = await fetch(`${API_BASE_URL}/repos/${encodeURIComponent(currentRepoName)}/files`);
        if (!filesResponse.ok) {
            throw new Error('获取文件列表失败');
        }
        
        const files = await filesResponse.json();
        
        const recommendCards = [];
        const availableFiles = {};
        
        files.forEach(file => {
            const baseName = file.name.replace('.yml', '').replace('.yaml', '').toLowerCase();
            availableFiles[baseName] = file.name;
        });
        
        Object.keys(RECOMMEND_CONFIG).forEach(key => {
            if (availableFiles[key]) {
                recommendCards.push({
                    ...RECOMMEND_CONFIG[key],
                    fileName: availableFiles[key]
                });
            }
        });
        
        if (recommendCards.length === 0) {
            grid.innerHTML = `
                <div class="empty-state">
                    <i class="fab fa-docker"></i>
                    <p>当前仓库暂无推荐的容器配置文件</p>
                </div>
            `;
            return;
        }
        
        grid.innerHTML = recommendCards.map(card => `
            <div class="recommend-card">
                <div class="card-header">
                    <div class="card-icon">
                        <i class="fab fa-docker"></i>
                    </div>
                    <div class="card-title">
                        <h3>${card.title}</h3>
                        <p>${card.subtitle}</p>
                    </div>
                </div>
                <div class="card-description">
                    <p>${card.description}</p>
                </div>
                <div class="card-tags">
                    ${card.tags.map(tag => `<span class="tag">${tag}</span>`).join('')}
                </div>
                <div class="card-actions">
                    <button class="btn btn-primary" onclick="goToDeploy('${card.fileName}')">
                        <i class="fas fa-download"></i>
                        <span>获取容器</span>
                    </button>
                    ${card.tutorial ? `
                    <button class="btn btn-secondary" onclick="openTutorial('${card.tutorial}')">
                        <i class="fas fa-book-open"></i>
                        <span>使用教程</span>
                    </button>
                    ` : ''}
                </div>
            </div>
        `).join('');
        
    } catch (error) {
        console.error('加载推荐容器失败:', error);
        grid.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-exclamation-circle"></i>
                <p>加载推荐容器失败，请检查后端服务</p>
            </div>
        `;
    }
}

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    checkLogin();
    loadUserInfo();
    loadRecommendations();
});