const { API_BASE_URL, apiFetch } = window.AppPage;

// 宿主机实时指标的 SSE 句柄（全局，保证同一时间只有一条连接）
let _hostMetricsSource = null;
let _hostMetricsReconnectTimer = null;

document.addEventListener('DOMContentLoaded', async function() {
    await loadSidebar();
    if (!checkLogin()) return;
    const user = loadUserInfo();
    if (user.isAdmin) {
        loadRepoCount();
        loadDeployedAppsCount();
        loadContainerCount();
        loadSuccessRate();
        loadDeploymentHistory();
    } else {
        applyStandardUserDashboard();
        loadDashboardStats();
    }
    loadConnectivity();
    loadDockerInfo();
    loadHostInfo(); // 内部成功后会启动实时 SSE

    // 页面可见性：切到其他 Tab 时断开 SSE，避免空耗宿主机 1 秒一次的采样
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            stopHostMetricsStream(true);
        } else {
            // 回到该页时只有 DOM 已经渲染过 host-info 才重连（防止初始化前误连）
            if (document.getElementById('stat-cpu-value')) {
                startHostMetricsStream();
            }
        }
    });

    // 窗口关闭时显式断开，避免服务端长时间保留连接
    window.addEventListener('beforeunload', function () {
        stopHostMetricsStream(false);
    });

    setInterval(user.isAdmin ? loadContainerCount : loadDashboardStats, 10000);
});


function stopHostMetricsStream(autoReconnect) {
    if (_hostMetricsReconnectTimer) {
        clearTimeout(_hostMetricsReconnectTimer);
        _hostMetricsReconnectTimer = null;
    }
    if (_hostMetricsSource) {
        try {
            _hostMetricsSource.onerror = null;
            _hostMetricsSource.onmessage = null;
            _hostMetricsSource.onopen = null;
            _hostMetricsSource.close();
        } catch (e) { /* noop */ }
        _hostMetricsSource = null;
    }
    if (autoReconnect) {
        // 调用方会自己在合适时重新 start，所以这里什么都不做
    }
}


function startHostMetricsStream() {
    // 先关旧的，保证不会重复连接
    stopHostMetricsStream(false);

    const cpuEl       = document.getElementById('stat-cpu-value');
    const cpuBarEl    = document.getElementById('stat-cpu-progress');
    const memPctEl    = document.getElementById('stat-mem-percent');
    const memBarEl    = document.getElementById('stat-mem-progress');
    const memUsedEl   = document.getElementById('stat-mem-used-total');
    // 如果首次渲染还没完成（卡片 DOM 尚未插入），跳过 SSE 启动
    if (!cpuEl || !memPctEl || !memBarEl || !memUsedEl) {
        return;
    }

    try {
        const url = `${API_BASE_URL}/system/host-metrics/stream`;
        const source = new EventSource(url, { withCredentials: true });
        _hostMetricsSource = source;

        source.onmessage = function (ev) {
            let payload;
            try {
                payload = JSON.parse(ev.data);
            } catch (e) {
                return;
            }
            if (!payload || payload.type !== 'metrics') {
                return;
            }
            if (payload.cpu_usage) {
                const n = _parsePctNumber(payload.cpu_usage);
                cpuEl.textContent = payload.cpu_usage;
                if (cpuBarEl) {
                    cpuBarEl.style.width = n + '%';
                    _applyWarnClass(cpuBarEl, n);
                }
            }
            if (payload.memory_used && payload.memory_total) {
                memUsedEl.textContent = `${payload.memory_used} / ${payload.memory_total}`;
            }
            if (payload.memory_usage) {
                const n = _parsePctNumber(payload.memory_usage);
                memPctEl.textContent = payload.memory_usage;
                memBarEl.style.width = n + '%';
                _applyWarnClass(memBarEl, n);
            }
        };

        source.onerror = function () {
            // 异常关闭：先清理资源，退避 5 秒再重连
            stopHostMetricsStream(false);
            if (!_hostMetricsReconnectTimer) {
                _hostMetricsReconnectTimer = setTimeout(function () {
                    _hostMetricsReconnectTimer = null;
                    if (document.visibilityState !== 'hidden') {
                        startHostMetricsStream();
                    }
                }, 5000);
            }
        };
    } catch (e) {
        // 浏览器不支持 EventSource 等异常：忽略，继续用静态一次性数据
    }
}

async function loadSidebar() {
    return window.AppPage.loadSidebar('dashboard');
}

function checkLogin() {
    return window.AppPage.requireLogin();
}

function loadUserInfo() {
    window.AppPage.populateUsername();
    const username = localStorage.getItem('username') || '用户';
    const isAdmin = localStorage.getItem('is_admin') === 'true';
    const heading = document.querySelector('.header-left h1');
    if (heading) heading.textContent = `欢迎回来，${username}`;
    return { username, isAdmin };
}

function applyStandardUserDashboard() {
    document.querySelector('.quick-actions')?.remove();
    document.querySelector('.recent-activity')?.remove();
}

async function loadDashboardStats() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/dashboard/stats`);
        if (!response.ok) return;
        const data = await response.json();
        document.getElementById('repoCount').textContent = data.repo_count;
        document.getElementById('deployedAppsCount').textContent = data.deployed_apps_count;
        document.getElementById('containerCount').textContent = data.container_count;
        document.getElementById('successRate').textContent = `${data.success_rate}%`;
    } catch (error) {
        console.error('Failed to load dashboard stats:', error);
    }
}

async function loadRepoCount() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/repos`);
        if (response.ok) {
            const repos = await response.json();
            document.getElementById('repoCount').textContent = repos.length;
        }
    } catch (error) {
        console.error('Failed to load repo count:', error);
    }
}

async function loadDeployedAppsCount() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/deployments/count`);
        if (response.ok) {
            const data = await response.json();
            document.getElementById('deployedAppsCount').textContent = data.count;
        }
    } catch (error) {
        console.error('Failed to load deployed apps count:', error);
    }
}

async function loadSuccessRate() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/deployments/success-rate`);
        if (response.ok) {
            const data = await response.json();
            document.getElementById('successRate').textContent = data.rate + '%';
        }
    } catch (error) {
        console.error('Failed to load success rate:', error);
    }
}

async function loadContainerCount() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/containers/count`);
        if (response.ok) {
            const data = await response.json();
            document.getElementById('containerCount').textContent = data.count;
        }
    } catch (error) {
        console.error('Failed to load container count:', error);
    }
}

async function loadDeploymentHistory() {
    try {
        const response = await apiFetch(`${API_BASE_URL}/deployments?limit=10`);
        if (response.ok) {
            const deployments = await response.json();
            renderDeploymentHistory(deployments);
        }
    } catch (error) {
        console.error('Failed to load deployment history:', error);
    }
}

function renderDeploymentHistory(deployments) {
    const container = document.getElementById('deploymentList');
    
    if (!deployments || deployments.length === 0) {
        container.innerHTML = `
            <div class="activity-item empty">
                <div class="activity-icon info">
                    <i class="fas fa-info"></i>
                </div>
                <div class="activity-content">
                    <h4>暂无部署记录</h4>
                    <p>通过应用部署页面部署应用后，记录将显示在这里</p>
                </div>
            </div>
        `;
        return;
    }
    
    container.innerHTML = deployments.map(deployment => {
        const statusClass = deployment.status === 'deployed' ? 'success' : 
                           deployment.status === 'failed' ? 'warning' : 'info';
        const iconClass = deployment.status === 'deployed' ? 'fa-check' : 
                         deployment.status === 'failed' ? 'fa-exclamation' : 'fa-spinner';
        const message = deployment.status === 'deployed' 
            ? `已成功部署到容器 ${deployment.container_name || 'unknown'}`
            : deployment.message || '部署失败';
        const timeAgo = formatTimeAgo(deployment.created_at);
        
        return `
            <div class="activity-item">
                <div class="activity-icon ${statusClass}">
                    <i class="fas ${iconClass}"></i>
                </div>
                <div class="activity-content">
                    <h4>${deployment.file_name.replace('.yml', '').replace('.yaml', '')}</h4>
                    <p>${message}</p>
                </div>
                <div class="activity-time">${timeAgo}</div>
            </div>
        `;
    }).join('');
}

function formatTimeAgo(dateString) {
    if (!dateString) return '未知';
    
    let date;
    try {
        if (dateString.includes('T')) {
            if (dateString.includes('Z')) {
                date = new Date(dateString);
            } else {
                date = new Date(dateString + 'Z');
            }
        } else {
            date = new Date(dateString.replace(/-/g, '/'));
        }
    } catch (e) {
        console.error('Date parsing error:', e);
        return '未知';
    }
    
    if (isNaN(date.getTime())) {
        return '未知';
    }
    
    const now = new Date();
    const diff = now - date;
    
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    
    if (minutes < 1) return '刚刚';
    if (minutes < 60) return `${minutes}分钟前`;
    if (hours < 24) return `${hours}小时前`;
    if (days < 7) return `${days}天前`;
    
    return date.toLocaleDateString('zh-CN');
}

function navigateTo(page) {
    const pageMap = {
        'repository': '../repository/repository.html',
        'deploy': '../deploy/deploy.html',
        'recommend': '../recommend/recommend.html',
        'container': '../container/container.html',
        'backup': '../backup/backup.html',
        'image': '../image/image.html',
        'ai': '../ai/ai.html',
        'terminal': '../terminal/terminal.html',
        'logs': '../logs/logs.html',
        'users': '../users/users.html',
        'settings': '../settings/settings.html'
    };
    
    const url = pageMap[page];
    if (url) {
        window.location.href = url;
    }
}

async function loadConnectivity() {
    const container = document.getElementById('connectivityGrid');
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/connectivity`);
        if (response.ok) {
            const data = await response.json();
            renderConnectivity(data);
        } else {
            container.innerHTML = `
                <div class="connectivity-card error">
                    <div class="connectivity-icon">
                        <i class="fas fa-exclamation-circle"></i>
                    </div>
                    <div class="connectivity-content">
                        <h4>加载失败</h4>
                        <p>无法获取连接性测试结果</p>
                    </div>
                </div>
            `;
        }
    } catch (error) {
        console.error('Failed to load connectivity:', error);
        container.innerHTML = `
            <div class="connectivity-card error">
                <div class="connectivity-icon">
                    <i class="fas fa-exclamation-circle"></i>
                </div>
                <div class="connectivity-content">
                    <h4>连接失败</h4>
                    <p>无法连接到服务器</p>
                </div>
            </div>
        `;
    }
}

function renderConnectivity(data) {
    const container = document.getElementById('connectivityGrid');
    
    if (!data || !data.results || data.results.length === 0) {
        container.innerHTML = `
            <div class="connectivity-card error">
                <div class="connectivity-icon">
                    <i class="fas fa-info-circle"></i>
                </div>
                <div class="connectivity-content">
                    <h4>暂无测试结果</h4>
                    <p>点击刷新按钮进行连接性测试</p>
                </div>
            </div>
        `;
        return;
    }
    
    container.innerHTML = data.results.map(result => {
        const statusClass = result.success ? 'success' : 'failed';
        const iconClass = result.success ? 'fa-check-circle' : 'fa-times-circle';
        const latency = result.latency > 0 ? `${result.latency}ms` : '-';
        
        return `
            <div class="connectivity-card ${statusClass}">
                <div class="connectivity-icon">
                    <i class="fas ${iconClass}"></i>
                </div>
                <div class="connectivity-content">
                    <div class="connectivity-header">
                        <h4>${result.name}</h4>
                        ${result.success ? `<span class="latency">${latency}</span>` : ''}
                    </div>
                    <p>${result.message}</p>
                    <span class="connectivity-url">${result.url}</span>
                </div>
            </div>
        `;
    }).join('');
}

async function loadHostInfo() {
    const container = document.getElementById('hostInfoGrid');
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/system/host-info`);
        if (response.ok) {
            const data = await response.json();
            if (data.success && data.data) {
                container.innerHTML = renderHostInfo(data.data);
                // 一次性加载完成后，启动 SSE 实时刷新 CPU/内存两个卡片
                startHostMetricsStream();
            } else {
                // 加载失败就关闭任何可能残留的 SSE
                stopHostMetricsStream(false);
                container.innerHTML = `
                    <div class="host-info-card error">
                        <div class="host-info-icon">
                            <i class="fas fa-exclamation-circle"></i>
                        </div>
                        <div class="host-info-content">
                            <h4>加载失败</h4>
                            <p>无法获取宿主机信息</p>
                        </div>
                    </div>
                `;
            }
        } else {
            stopHostMetricsStream(false);
            container.innerHTML = `
                <div class="host-info-card error">
                    <div class="host-info-icon">
                        <i class="fas fa-exclamation-circle"></i>
                    </div>
                    <div class="host-info-content">
                        <h4>请求失败</h4>
                        <p>HTTP ${response.status}</p>
                    </div>
                </div>
            `;
        }
    } catch (error) {
        stopHostMetricsStream(false);
        container.innerHTML = `
            <div class="host-info-card error">
                <div class="host-info-icon">
                    <i class="fas fa-exclamation-circle"></i>
                </div>
                <div class="host-info-content">
                    <h4>加载失败</h4>
                    <p>网络错误或服务不可用</p>
                </div>
            </div>
        `;
    }
}

function _applyWarnClass(barEl, pctNum) {
    if (!barEl) return;
    if (pctNum >= 85) {
        barEl.classList.add('warn-high');
    } else {
        barEl.classList.remove('warn-high');
    }
}

function _parsePctNumber(pctStr) {
    if (!pctStr) return 0;
    const m = String(pctStr).match(/([0-9]+(?:\.[0-9]+)?)/);
    if (!m) return 0;
    let v = parseFloat(m[1]);
    if (isNaN(v)) return 0;
    if (v < 0) v = 0;
    if (v > 100) v = 100;
    return v;
}

function renderHostInfo(info) {
    // 只渲染"默认出口网卡"卡片（1张），不展示 docker0 / br-xxx 等全部虚拟网桥。
    // 筛选优先级：primary_interface 精确匹配 → is_default=true → 第一张有IP的网卡 → 空
    let defaultIface = null;
    const networks = (info && info.network_info) ? info.network_info : [];
    if (networks.length > 0) {
        const primary = (info && info.primary_interface) ? String(info.primary_interface).trim() : '';
        if (primary) {
            defaultIface = networks.find(function (n) {
                return (n && n.name && String(n.name).trim() === primary)
                    || (n && n.name_raw && String(n.name_raw).trim() === primary);
            });
        }
        if (!defaultIface) {
            defaultIface = networks.find(function (n) { return n && n.is_default === true; });
        }
        if (!defaultIface) {
            defaultIface = networks.find(function (n) { return n && n.name && n.ip; });
        }
    }
    const networkHtml = defaultIface ? `
            <div class="host-info-card">
                <div class="host-info-icon bg-network">
                    <i class="fas fa-network-wired"></i>
                </div>
                <div class="host-info-content">
                    <div class="host-info-head">
                        <h4>${defaultIface.name}</h4>
                    </div>
                    <p class="host-info-os-network-text">${defaultIface.ip || '—'}</p>
                </div>
            </div>
        ` : '';

    const cpuPct = _parsePctNumber(info && info.cpu_usage);
    const memPct = _parsePctNumber(info && info.memory_usage);
    const diskPct = _parsePctNumber(info && info.disk_usage);
    const cpuWarn  = cpuPct  >= 85 ? ' warn-high' : '';
    const memWarn  = memPct  >= 85 ? ' warn-high' : '';
    const diskWarn = diskPct >= 85 ? ' warn-high' : '';
    
    return `
        <div class="host-info-card" id="stat-card-cpu">
            <div class="host-info-icon bg-cpu">
                <i class="fas fa-microchip"></i>
            </div>
            <div class="host-info-content">
                <div class="host-info-head">
                    <h4>CPU 使用率</h4>
                    <span class="host-info-pct-value" id="stat-cpu-value">${info.cpu_usage}</span>
                </div>
                <div class="host-info-progress">
                    <div class="host-info-progress-bar bg-cpu-bar${cpuWarn}" id="stat-cpu-progress" style="width:${cpuPct}%"></div>
                </div>
                <div class="host-info-foot">
                    <span class="host-info-foot-label">实时</span>
                    <span>All Cores</span>
                </div>
            </div>
        </div>
        <div class="host-info-card" id="stat-card-memory">
            <div class="host-info-icon bg-memory">
                <i class="fas fa-memory"></i>
            </div>
            <div class="host-info-content">
                <div class="host-info-head">
                    <h4>内存使用</h4>
                    <span class="host-info-pct-value" id="stat-mem-percent">${info.memory_usage}</span>
                </div>
                <div class="host-info-progress">
                    <div class="host-info-progress-bar bg-memory-bar${memWarn}" id="stat-mem-progress" style="width:${memPct}%"></div>
                </div>
                <div class="host-info-foot">
                    <span class="host-info-foot-label">实时</span>
                    <span id="stat-mem-used-total">${info.memory_used} / ${info.memory_total}</span>
                </div>
            </div>
        </div>
        <div class="host-info-card" id="stat-card-disk">
            <div class="host-info-icon bg-disk">
                <i class="fas fa-hard-drive"></i>
            </div>
            <div class="host-info-content">
                <div class="host-info-head">
                    <h4>磁盘空间</h4>
                    <span class="host-info-pct-value" id="stat-disk-percent">${info.disk_usage}</span>
                </div>
                <div class="host-info-progress">
                    <div class="host-info-progress-bar bg-disk-bar${diskWarn}" id="stat-disk-progress" style="width:${diskPct}%"></div>
                </div>
                <div class="host-info-foot">
                    <span class="host-info-foot-label">根分区</span>
                    <span id="stat-disk-used-total">${info.disk_used} / ${info.disk_total}</span>
                </div>
            </div>
        </div>
        <div class="host-info-card">
            <div class="host-info-icon bg-os">
                <i class="fas fa-server"></i>
            </div>
            <div class="host-info-content">
                <div class="host-info-head">
                    <h4>系统版本</h4>
                </div>
                <p class="host-info-os-network-text">${info.os_version}</p>
            </div>
        </div>
        ${networkHtml}
    `;
}

async function loadDockerInfo() {
    const container = document.getElementById('dockerInfoGrid');
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/system/docker-info`);
        if (response.ok) {
            const data = await response.json();
            if (data.success && data.data) {
                renderDockerInfo(data.data);
            }
        } else {
            container.innerHTML = `
                <div class="docker-info-card error">
                    <div class="docker-info-icon">
                        <i class="fas fa-exclamation-circle"></i>
                    </div>
                    <div class="docker-info-content">
                        <h4>加载失败</h4>
                        <p>无法获取 Docker 版本信息</p>
                    </div>
                </div>
            `;
        }
    } catch (error) {
        console.error('Failed to load Docker info:', error);
        container.innerHTML = `
            <div class="docker-info-card error">
                <div class="docker-info-icon">
                    <i class="fas fa-exclamation-circle"></i>
                </div>
                <div class="docker-info-content">
                    <h4>连接失败</h4>
                    <p>无法连接到服务器</p>
                </div>
            </div>
        `;
    }
}

function renderDockerInfo(data) {
    const container = document.getElementById('dockerInfoGrid');
    
    container.innerHTML = `
        <div class="docker-info-card">
            <div class="docker-info-icon bg-blue">
                <i class="fab fa-docker"></i>
            </div>
            <div class="docker-info-content">
                <h4>Docker</h4>
                <p>${data.docker_version || '未知'}</p>
            </div>
        </div>
        <div class="docker-info-card">
            <div class="docker-info-icon bg-green">
                <i class="fab fa-docker"></i>
            </div>
            <div class="docker-info-content">
                <h4>Docker Compose</h4>
                <p>${data.docker_compose_version || '未知'}</p>
            </div>
        </div>
    `;
}
