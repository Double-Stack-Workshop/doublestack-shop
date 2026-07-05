const API_BASE_URL = '/api';

const RECOMMEND_CONFIG = {
    'qbittorrent': {
        title: 'qBittorrent',
        subtitle: '轻量级 BitTorrent 客户端',
        description: '功能强大的开源 BitTorrent 客户端，支持远程管理、RSS订阅、Web UI 等功能。',
        tags: ['下载工具', 'BT'],
        tutorial: 'https://blog.yutumay.cn:16666/archives/docker-rong-qi-qbittorrent-bu-shu-jiao-cheng'
    },
    'transmission': {
        title: 'Transmission',
        subtitle: '快速轻量级 BT 客户端',
        description: '开源的 BitTorrent 客户端，以简洁高效著称，支持 Web 界面远程管理。',
        tags: ['下载工具', 'BT'],
        tutorial: 'https://blog.yutumay.cn:16666/archives/docker-rong-qi-transmission-bu-shu-jiao-cheng'
    },
    'emby': {
        title: 'Emby',
        subtitle: '个人媒体服务器',
        description: '强大的媒体服务器，支持自动刮削元数据、多设备播放、实时转码、直播电视等功能。',
        tags: ['媒体', '影音'],
        tutorial: 'https://blog.yutumay.cn:16666/archives/docker-rong-qi-emby-bu-shu-jiao-cheng'
    },
    'moviepilot': {
        title: 'MoviePilot',
        subtitle: '智能媒体库管理工具',
        description: 'NAS 媒体库自动化管理工具，支持自动订阅、刮削、整理、通知等功能。',
        tags: ['媒体', '自动化'],
        tutorial: 'https://blog.yutumay.cn:16666/archives/docker-rong-qi-moviepilot-bu-shu-jiao-cheng'
    },
    'navidrome': {
        title: 'Navidrome',
        subtitle: '现代音乐流媒体服务器',
        description: '开源的音乐流媒体服务器，支持 Subsonic API，可在线播放和管理个人音乐库。',
        tags: ['音乐', '流媒体'],
        tutorial: 'https://blog.yutumay.cn:16666/archives/docker-rong-qi-navidrome-bu-shu-jiao-cheng'
    },
    'openlist': {
        title: 'OpenList',
        subtitle: '网盘文件列表程序',
        description: '支持多种网盘的文件列表程序，可统一管理阿里云盘、百度网盘、天翼云盘等。',
        tags: ['网盘', '文件管理'],
        tutorial: 'https://blog.yutumay.cn:16666/archives/docker-rong-qi-openlist-bu-shu-jiao-cheng'
    }
};

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
        const [currentRepoResponse, reposResponse] = await Promise.all([
            fetch(`${API_BASE_URL}/current-repo`),
            fetch(`${API_BASE_URL}/repos`)
        ]);
        
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
                    <p>暂无仓库，请先添加仓库</p>
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