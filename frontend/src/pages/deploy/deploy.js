const { API_BASE_URL, apiFetch } = window.AppPage;

let originalContent = '';
let fileLoadSequence = 0;
let isDeploying = false;
const deploymentQueue = [];
let activeDeployment = null;

function setSaveButtonsDisabled(disabled) {
    document.querySelectorAll('.deploy-save-btn').forEach(btn => {
        btn.disabled = disabled;
    });
}

function setDeployButtonsDisabled(disabled) {
    document.querySelectorAll('.deploy-deploy-btn').forEach(btn => {
        btn.disabled = disabled;
    });
}

document.addEventListener('DOMContentLoaded', async function() {
    try {
        await loadSidebar();
    } catch (error) {
        console.error('Error loading sidebar:', error);
    }
    
    checkLogin();
    loadUserInfo();
    
    try {
        await loadRepositories();
    } catch (error) {
        console.error('Error loading repositories:', error);
    }
    
    setupEventListeners();
});

async function loadSidebar() {
    return window.AppPage.loadSidebar('deploy');
}

function checkLogin() {
    return window.AppPage.requireLogin();
}

function loadUserInfo() {
    window.AppPage.populateUsername();
}

async function loadRepositories() {
    const select = document.getElementById('repoSelect');
    if (!select) {
        console.error('repoSelect element not found');
        return;
    }
    select.innerHTML = '<option value="">请选择仓库</option>';
    
    const urlParams = new URLSearchParams(window.location.search);
    const searchQuery = urlParams.get('search');
    
    try {
        const [reposResponse, currentRepoResponse] = await Promise.all([
            apiFetch(`${API_BASE_URL}/repos`),
            apiFetch(`${API_BASE_URL}/current-repo`)
        ]);
        
        if (!reposResponse.ok) {
            throw new Error(`HTTP error! status: ${reposResponse.status}`);
        }
        const repos = await reposResponse.json();
        const deployableRepos = repos.filter((repo) => (
            repo.repo_type !== 'script' && repo.status === 'active' && repo.yml_count > 0
        ));

        deployableRepos.forEach(repo => {
            const option = document.createElement('option');
            option.value = repo.name;
            option.textContent = repo.name;
            select.appendChild(option);
        });

        if (deployableRepos.length === 0) {
            select.innerHTML = '<option value="">暂无已同步的可部署仓库</option>';
            addLog('warning', '暂无已同步且包含 YML 文件的仓库，请先在仓库管理中同步仓库');
            return;
        }
        
        let selectedRepo = '';
        if (currentRepoResponse.ok) {
            const currentRepoData = await currentRepoResponse.json();
            selectedRepo = currentRepoData.data?.repo_name || '';
        }
        
        if (!deployableRepos.some((repo) => repo.name === selectedRepo)) {
            selectedRepo = deployableRepos[0].name;
        }
        
        if (selectedRepo) {
            select.value = selectedRepo;
            await loadYmlFiles(selectedRepo, searchQuery || '');
        }
    } catch (error) {
        console.error('Error loading repositories:', error);
        addLog('error', '获取仓库列表失败: ' + error.message);
    }
}

let allYmlFiles = [];

async function loadYmlFiles(repoName, searchQuery = '') {
    const select = document.getElementById('fileSelect');
    const searchInput = document.getElementById('fileSearch');
    
    if (!select || !searchInput) {
        console.error('fileSelect or fileSearch element not found');
        return;
    }
    
    select.innerHTML = '<option value="">请选择YML文件</option>';
    searchInput.value = searchQuery;
    searchInput.disabled = true;
    searchInput.placeholder = '请先选择仓库';
    hideFileDropdown();
    allYmlFiles = [];
    
    if (!repoName) {
        return;
    }
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/repos/${repoName}/files`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const files = await response.json();
        
        allYmlFiles = files.filter(file => file.name.endsWith('.yml') || file.name.endsWith('.yaml'));
        
        allYmlFiles.forEach(file => {
            const option = document.createElement('option');
            option.value = file.name;
            option.textContent = file.name;
            select.appendChild(option);
        });
        
        searchInput.disabled = allYmlFiles.length === 0;
        searchInput.placeholder = allYmlFiles.length > 0 ? '选择或搜索 YML 文件...' : '该仓库没有 YML 文件';
        renderFileOptions(searchQuery);
        
        if (allYmlFiles.length === 0) {
            addLog('warning', '该仓库中没有找到YML文件');
        }
    } catch (error) {
        console.error('Error loading files:', error);
        addLog('error', '获取文件列表失败: ' + error.message);
        searchInput.disabled = true;
        searchInput.placeholder = '获取文件列表失败';
    }
}

function renderFileOptions(searchText = '') {
    const dropdown = document.getElementById('fileDropdown');
    const normalizedSearch = searchText.trim().toLowerCase();
    const filteredFiles = allYmlFiles.filter(file => file.name.toLowerCase().includes(normalizedSearch));

    dropdown.innerHTML = filteredFiles.length > 0
        ? filteredFiles.map(file => `<button type="button" class="file-option" role="option" data-file="${file.name}"><i class="fas fa-file-code"></i><span>${file.name}</span></button>`).join('')
        : '<div class="file-option-empty">未找到匹配的 YML 文件</div>';
}

function showFileDropdown() {
    const searchInput = document.getElementById('fileSearch');
    const dropdown = document.getElementById('fileDropdown');
    if (searchInput.disabled) return;
    renderFileOptions(searchInput.value);
    dropdown.classList.remove('is-hidden');
}

function hideFileDropdown() {
    const dropdown = document.getElementById('fileDropdown');
    if (dropdown) dropdown.classList.add('is-hidden');
}

function selectYmlFile(fileName) {
    const fileSelect = document.getElementById('fileSelect');
    document.getElementById('fileSearch').value = fileName;
    fileSelect.value = fileName;
    hideFileDropdown();
    loadFileContent(document.getElementById('repoSelect').value, fileName);
}

async function loadFileContent(repoName, fileName) {
    const editor = document.getElementById('ymlEditor');
    const loadId = ++fileLoadSequence;
    editor.dataset.loading = 'true';
    setSaveButtonsDisabled(true);
    setDeployButtonsDisabled(true);
    const modeSection = document.getElementById('modeSection');
    const paramsSection = document.getElementById('paramsSection');
    const editorSection = document.getElementById('editorSection');
    const outputSection = document.getElementById('outputSection');
    
    if (!repoName || !fileName) {
        editor.dataset.loading = 'false';
        editor.value = '';
        editor.placeholder = '选择一个YML文件查看内容...';
        originalContent = '';
        setSaveButtonsDisabled(true);
        setDeployButtonsDisabled(true);
        document.getElementById('fileName').textContent = '未选择文件';
        document.getElementById('fileSize').textContent = '0 KB';
        document.getElementById('lastModified').textContent = '未知';
        editor.style.height = '150px';
        modeSection.style.display = 'none';
        paramsSection.style.display = 'none';
        editorSection.style.display = 'none';
        const _ns1 = document.getElementById('networkSection');
        if (_ns1) _ns1.style.display = 'none';
        document.getElementById('deployQueuePanel').classList.add('is-hidden');
        outputSection.style.display = 'none';
        return;
    }
    
    try {
        const response = await apiFetch(`${API_BASE_URL}/repos/${repoName}/files/${encodeURIComponent(fileName)}`);
        
        if (!response.ok) {
            throw new Error('文件读取失败');
        }
        
        const data = await response.json();
        if (loadId !== fileLoadSequence || repoName !== document.getElementById('repoSelect').value
                || fileName !== document.getElementById('fileSelect').value) return;
        originalContent = data.content;
        editor.value = data.content;
        editor.placeholder = '';
        
        document.getElementById('fileName').textContent = fileName;
        document.getElementById('fileSize').textContent = `${(data.content.length / 1024).toFixed(2)} KB`;
        document.getElementById('lastModified').textContent = data.last_modified || '未知';
        
        setSaveButtonsDisabled(true);
        setDeployButtonsDisabled(false);
        
        adjustEditorHeight(editor);
        parseYmlAndShowParams(data.content);
        
        modeSection.style.display = 'block';
        // 重置三个 section 的可见性：默认显示简易模式，其他隐藏
        paramsSection.style.display = 'block';
        editorSection.style.display = 'none';
        const _networkSec = document.getElementById('networkSection');
        if (_networkSec) _networkSec.style.display = 'none';
        // 同步重置 tab 的 active 状态为 easy mode
        const _eBtn = document.getElementById('easyModeBtn');
        const _aBtn = document.getElementById('advancedModeBtn');
        const _nBtn = document.getElementById('networkModeBtn');
        if (_eBtn) _eBtn.classList.add('active');
        if (_aBtn) _aBtn.classList.remove('active');
        if (_nBtn) _nBtn.classList.remove('active');
        document.getElementById('deployQueuePanel').classList.remove('is-hidden');
        outputSection.style.display = 'block';
        
        addLog('info', `已加载文件: ${fileName}`);
    } catch (error) {
        if (loadId !== fileLoadSequence || repoName !== document.getElementById('repoSelect').value
                || fileName !== document.getElementById('fileSelect').value) return;
        addLog('error', '读取文件内容失败: ' + error.message);
        editor.value = '';
        editor.placeholder = '文件读取失败';
        editor.style.height = '150px';
        clearParamsTable();
        modeSection.style.display = 'none';
        const _ns2 = document.getElementById('networkSection');
        if (_ns2) _ns2.style.display = 'none';
        document.getElementById('deployQueuePanel').classList.add('is-hidden');
        outputSection.style.display = 'none';
    } finally {
        if (loadId === fileLoadSequence) editor.dataset.loading = 'false';
    }
}

function parseYmlAndShowParams(ymlContent) {
    const paramsContainer = document.getElementById('paramsTableContainer');
    
    try {
        const services = parseYamlServices(ymlContent);
        
        if (!services || Object.keys(services).length === 0) {
            paramsContainer.innerHTML = '<p class="empty-hint">未解析到服务配置</p>';
            return;
        }
        
        let html = '<table class="params-table">';
        html += '<thead><tr><th>服务名称</th><th>参数类型</th><th>参数值</th></tr></thead>';
        html += '<tbody>';
        
        for (const [serviceName, config] of Object.entries(services)) {
            const extractFields = ['image', 'container_name', 'ports', 'volumes', 'environment', 'restart', 'network_mode'];
            let hasParams = false;
            
            for (const field of extractFields) {
                if (config[field] !== undefined && config[field] !== null) {
                    hasParams = true;
                    let displayValue = '';
                    
                    if (Array.isArray(config[field])) {
                        if (field === 'ports') {
                            displayValue = config[field].map((p, idx) => {
                                const portMapping = p.replace(/^\s*-\s+/, '').trim();
                                const [hostPort, containerPort] = portMapping.includes(':') ? portMapping.split(':') : [portMapping, portMapping];
                                return `<div class="port-input-group">
                                    <label class="port-label">宿主主机端口</label>
                                    <input type="text" class="param-input port-host" data-service="${serviceName}" data-field="${field}" data-idx="${idx}" data-original="${portMapping}" data-part="host" value="${hostPort}">
                                    <span class="port-separator">:</span>
                                    <label class="port-label">容器端口</label>
                                    <input type="text" class="param-input port-container" data-service="${serviceName}" data-field="${field}" data-idx="${idx}" data-original="${portMapping}" data-part="container" value="${containerPort}">
                                </div>`;
                            }).join('<br>');
                        } else if (field === 'volumes') {
                            displayValue = config[field].map((v, idx) => {
                                const fullPath = v.replace(/^\s*-\s+/, '').trim();
                                const parts = fullPath.split(':');
                                const hostPath = parts[0];
                                const containerPath = parts.length >= 2 ? parts.slice(1).join(':') : '';
                                return `<div class="volume-input-group">
                                    <input type="text" class="param-input volume-host" data-service="${serviceName}" data-field="${field}" data-idx="${idx}" data-original="${fullPath}" value="${hostPath}" placeholder="宿主机路径">
                                    <span class="volume-arrow">→</span>
                                    <span class="volume-container">${containerPath || ''}</span>
                                </div>`;
                            }).join('<br>');
                        } else if (field === 'environment') {
                            displayValue = config[field].map((e, idx) => {
                                const env = e.replace(/^\s*-\s+/, '').trim();
                                let key = '';
                                let val = '';
                                if (env.includes('=')) {
                                    [key, val] = env.split('=', 2);
                                } else if (env.includes(':')) {
                                    [key, val] = env.split(':', 2);
                                } else {
                                    key = env;
                                }
                                return `<div class="env-input-group">
                                    <span class="env-key">${key}</span>
                                    <span class="env-separator">=</span>
                                    <input type="text" class="param-input env-value" data-service="${serviceName}" data-field="${field}" data-idx="${idx}" data-original="${env}" data-key="${key}" value="${val}" placeholder="变量值">
                                </div>`;
                            }).join('<br>');
                        } else {
                            displayValue = config[field].map((val, idx) => {
                                return `<input type="text" class="param-input" data-service="${serviceName}" data-field="${field}" data-idx="${idx}" data-original="${val}" value="${val}">`;
                            }).join('<br>');
                        }
                    } else {
                        displayValue = `<input type="text" class="param-input" data-service="${serviceName}" data-field="${field}" data-original="${config[field]}" value="${config[field]}">`;
                    }
                    
                    html += `
                        <tr>
                            <td>${field === 'image' ? `<span class="service-name">${serviceName}</span>` : ''}</td>
                            <td class="param-name">${getFieldDisplayName(field)}</td>
                            <td class="param-value">${displayValue}</td>
                        </tr>
                    `;
                }
            }
            
            if (!hasParams) {
                html += `
                    <tr>
                        <td><span class="service-name">${serviceName}</span></td>
                        <td colspan="2">未找到可提取的参数</td>
                    </tr>`;
            }
        }
        
        html += '</tbody></table>';
        paramsContainer.innerHTML = html;
    } catch (error) {
        paramsContainer.innerHTML = `<p class="empty-hint">解析YML失败: ${error.message}</p>`;
    }
}

function parseYamlServices(content) {
    return window.DeployYaml.parseServices(content);
}

function getFieldDisplayName(field) {
    const displayNames = {
        'image': '镜像名称',
        'container_name': '容器名称',
        'ports': '端口映射',
        'volumes': '卷挂载',
        'environment': '环境变量',
        'restart': '重启策略',
        'network_mode': '网络模式'
    };
    return displayNames[field] || field;
}

function clearParamsTable() {
    const paramsContainer = document.getElementById('paramsTableContainer');
    paramsContainer.innerHTML = '<p class="empty-hint">选择一个YML文件查看配置参数</p>';
}

function adjustEditorHeight(editor) {
    editor.style.height = 'auto';
    const lineCount = editor.value.split('\n').length;
    const lineHeight = 24;
    const padding = 40;
    const minHeight = 200;
    const maxHeight = 500;
    
    let newHeight = lineCount * lineHeight + padding;
    newHeight = Math.max(minHeight, Math.min(newHeight, maxHeight));
    
    editor.style.height = newHeight + 'px';
}

async function saveFileContent(repoName, fileName, content) {
    try {
        const response = await apiFetch(`${API_BASE_URL}/repos/${repoName}/files/${encodeURIComponent(fileName)}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ content })
        });
        
        if (!response.ok) {
            throw new Error('保存失败');
        }
        
        const result = await response.json();
        
        if (result.success) {
            originalContent = content;
            setSaveButtonsDisabled(true);
            addLog('success', '文件保存成功');
        } else {
            addLog('error', '保存失败: ' + result.message);
        }
    } catch (error) {
        addLog('error', '保存文件失败: ' + error.message);
    }
}

function formatDuration(sec) {
    if (sec == null || isNaN(sec)) return '—';
    sec = Number(sec);
    if (sec < 60) return `${sec.toFixed(1)}s`;
    const m = Math.floor(sec / 60);
    const s = sec - m * 60;
    return `${m}m${s.toFixed(0)}s`;
}

function formatShortTime(isoStr) {
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

function resetDeployProgressPanel() {
    const panel = document.getElementById('deployProgressPanel');
    if (panel) panel.style.display = 'block';
    ['pull', 'up'].forEach(stage => {
        const bar = document.getElementById(`${stage}Bar`);
        if (bar) bar.style.width = '0%';
        const pct = document.getElementById(`${stage}StagePct`);
        if (pct) pct.textContent = '0%';
        const tm = document.getElementById(`${stage}StageTime`);
        if (tm) tm.textContent = '—';
        const det = document.getElementById(`${stage}StageDetail`);
        if (det) det.textContent = stage === 'pull' ? '等待开始拉取...' : '等待启动...';
    });
    const totalEl = document.getElementById('totalElapsed');
    if (totalEl) totalEl.textContent = '0.0s';
    const st = document.getElementById('deployStatus');
    if (st) { st.textContent = '运行中'; st.style.color = '#60a5fa'; }
}

function updateStageProgress(ev) {
    const stage = ev.stage;
    const bar = document.getElementById(`${stage}Bar`);
    const pctEl = document.getElementById(`${stage}StagePct`);
    const tmEl = document.getElementById(`${stage}StageTime`);
    const detEl = document.getElementById(`${stage}StageDetail`);
    if (!bar || !pctEl) return;
    const pct = Math.max(0, Math.min(100, Number(ev.percent) || 0));
    bar.style.width = `${pct}%`;
    pctEl.textContent = `${pct.toFixed(1)}%`;
    if (tmEl) {
        const parts = [];
        if (ev.elapsed_sec != null) parts.push(`用时 ${formatDuration(ev.elapsed_sec)}`);
        if (ev.eta_sec != null && ev.eta_sec > 0 && pct < 99.9) parts.push(`预计剩余 ${formatDuration(ev.eta_sec)}`);
        tmEl.textContent = parts.join(' · ') || '—';
    }
    if (detEl && ev.detail) detEl.textContent = ev.detail;
}

function finishDeployFlow(finalResult) {
    const st = document.getElementById('deployStatus');
    if (st && finalResult) {
        if (finalResult.success) {
            st.textContent = '成功';
            st.style.color = '#34d399';
        } else {
            st.textContent = '失败';
            st.style.color = '#f87171';
        }
    }
    const totalEl = document.getElementById('totalElapsed');
    if (totalEl && finalResult && finalResult.elapsed_sec != null) {
        totalEl.textContent = formatDuration(finalResult.elapsed_sec);
    }
    // 如果某阶段进度条还没到 100，流结束了就按 100/按实际画一下
    ['pull', 'up'].forEach(stage => {
        const bar = document.getElementById(`${stage}Bar`);
        const pctEl = document.getElementById(`${stage}StagePct`);
        if (bar && pctEl && Number(bar.style.width || '0%') < 100) {
            // 保持现状，不强制 100；但把"预计剩余"去掉
            const tmEl = document.getElementById(`${stage}StageTime`);
            if (tmEl && /预计剩余/.test(tmEl.textContent || '')) {
                tmEl.textContent = (tmEl.textContent.match(/用时 [^·]+/) || ['—'])[0].replace('用时 ', '') === '用时' ? '—' : tmEl.textContent.replace(/ · 预计剩余.*/, '');
            }
        }
    });
}

function renderDeploymentQueue() {
    const panel = document.getElementById('deployQueuePanel');
    const summary = document.getElementById('deployQueueSummary');
    const items = document.getElementById('deployQueueItems');
    if (!panel || !summary || !items) return;

    const queuedLabel = deploymentQueue.length > 0 ? `等待 ${deploymentQueue.length} 项` : '队列为空';
    summary.textContent = isDeploying ? `正在部署，${queuedLabel}` : queuedLabel;
    const activeItem = activeDeployment
        ? `<div class="deploy-queue-item active"><i class="fas fa-spinner fa-spin"></i><span>${activeDeployment.fileName}</span><small>${activeDeployment.repoName}</small><b>部署中</b></div>`
        : '';
    const waitingItems = deploymentQueue.map((task, index) =>
        `<div class="deploy-queue-item"><span class="queue-index">${index + 1}</span><span>${task.fileName}</span><small>${task.repoName}</small><b>等待中</b></div>`
    ).join('');
    items.innerHTML = activeItem || waitingItems ? activeItem + waitingItems : '';
}

function deployApplication(repoName, fileName) {
    if (!repoName || !fileName) {
        addLog('warning', '请先选择仓库和 YML 文件');
        return;
    }

    const task = { repoName, fileName };
    deploymentQueue.push(task);
    const outputSection = document.getElementById('outputSection');
    if (outputSection) outputSection.style.display = 'block';

    if (isDeploying) {
        addLog('info', `已加入部署队列: ${fileName}`);
    }
    renderDeploymentQueue();
    void processDeploymentQueue();
}

async function processDeploymentQueue() {
    if (isDeploying) return;

    while (deploymentQueue.length > 0) {
        activeDeployment = deploymentQueue.shift();
        isDeploying = true;
        renderDeploymentQueue();
        await executeDeployment(activeDeployment.repoName, activeDeployment.fileName);
        activeDeployment = null;
        isDeploying = false;
        renderDeploymentQueue();
    }
}

async function executeDeployment(repoName, fileName) {
    const outputSection = document.getElementById('outputSection');
    if (outputSection) outputSection.style.display = 'block';
    resetDeployProgressPanel();

    try {
        addLog('info', `开始部署应用: ${fileName}`);

        const response = await apiFetch(`${API_BASE_URL}/deploy`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                repo_name: repoName,
                file_name: fileName
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        let finalResult = null;

        await window.DeployProgress.consume(response, (event) => {
            if (event.type === 'log') {
                    const level = event.level === 'success' ? 'success' :
                                  event.level === 'error' ? 'error' : 'info';
                    addLog(level, event.message, event.ts);
            } else if (event.type === 'progress') {
                    updateStageProgress(event);
                    // 汇总时间也同步刷新
                    const totalEl = document.getElementById('totalElapsed');
                    if (totalEl && event.elapsed_sec != null) {
                        totalEl.textContent = formatDuration(event.elapsed_sec);
                    }
            } else if (event.type === 'done') {
                    finalResult = event;
                    if (event.success) {
                        addLog('success', event.message, event.ts);
                        if (event.data && event.data.container_id) {
                            addLog('info', '容器ID: ' + event.data.container_id, event.ts);
                        }
                    } else {
                        addLog('error', event.message, event.ts);
                    }
            }
        });

        finishDeployFlow(finalResult);

        if (!finalResult) {
            addLog('warning', '部署流结束但未收到最终结果');
        }
    } catch (error) {
        addLog('error', '部署应用失败: ' + error.message);
        const st = document.getElementById('deployStatus');
        if (st) { st.textContent = '异常'; st.style.color = '#f87171'; }
    } finally {
        // 队列处理期间保持部署按钮可用，用户可继续添加下一项任务。
    }
}

function addLog(type, message, ts) {
    const logContainer = document.getElementById('logContainer');
    const logItem = document.createElement('div');
    logItem.className = `log-item ${type}`;

    const iconMap = {
        info: 'fas fa-info-circle',
        success: 'fas fa-check-circle',
        warning: 'fas fa-exclamation-triangle',
        error: 'fas fa-times-circle'
    };

    const tm = formatShortTime(ts);
    const timeHtml = tm ? `<span class="log-time">[${tm}]</span>` : '';

    const safeMsg = String(message).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
    logItem.innerHTML = `
        <i class="${iconMap[type]}"></i>
        ${timeHtml}
        <span>${safeMsg}</span>
    `;

    logContainer.appendChild(logItem);
    logContainer.scrollTop = logContainer.scrollHeight;
}

function syncParamsToEditor() {
    const editor = document.getElementById('ymlEditor');
    let content = editor.value;
    let hasChanges = false;
    
    const portInputs = document.querySelectorAll('input[data-field="ports"]');
    const portIdxMap = new Map();
    
    portInputs.forEach(input => {
        const idx = input.dataset.idx;
        const original = input.dataset.original;
        const part = input.dataset.part;
        const value = input.value.trim();
        
        if (!portIdxMap.has(idx)) {
            portIdxMap.set(idx, { original, host: '', container: '' });
        }
        
        if (part === 'host') {
            portIdxMap.get(idx).host = value;
        } else {
            portIdxMap.get(idx).container = value;
        }
    });
    
    portIdxMap.forEach((data, idx) => {
        const { original, host, container } = data;
        if (!host || !container) return;
        
        const newEntry = `${host}:${container}`;
        if (newEntry !== original) {
            hasChanges = true;
            content = content.replace(new RegExp(original.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), newEntry);
            
            const portInputs = document.querySelectorAll(`input[data-field="ports"][data-idx="${idx}"]`);
            portInputs.forEach(input => {
                input.dataset.original = newEntry;
            });
        }
    });
    
    const volumeInputs = document.querySelectorAll('input[data-field="volumes"]');
    volumeInputs.forEach(input => {
        const original = input.dataset.original;
        const newValue = input.value.trim();
        
        if (!newValue || newValue === original) return;
        
        const parts = original.split(':');
        let newEntry = '';
        if (parts.length >= 2) {
            const containerPath = parts.slice(1).join(':');
            newEntry = `${newValue}:${containerPath}`;
        } else {
            newEntry = newValue;
        }
        
        if (newEntry !== original) {
            hasChanges = true;
            content = content.replace(new RegExp(original.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), newEntry);
            input.dataset.original = newEntry;
        }
    });
    
    const envInputs = document.querySelectorAll('input[data-field="environment"]');
    envInputs.forEach(input => {
        const original = input.dataset.original;
        const newValue = input.value.trim();
        const key = input.dataset.key;
        
        if (!newValue) return;
        
        const newEntry = `${key || ''}=${newValue}`;
        if (newEntry !== original) {
            hasChanges = true;
            content = content.replace(new RegExp(original.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), newEntry);
            input.dataset.original = newEntry;
        }
    });
    
    const otherInputs = document.querySelectorAll('input[data-field]:not([data-field="ports"]):not([data-field="volumes"]):not([data-field="environment"])');
    otherInputs.forEach(input => {
        const original = input.dataset.original;
        const newValue = input.value.trim();
        
        if (!newValue || newValue === original) return;
        
        hasChanges = true;
        content = content.replace(new RegExp(original.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), newValue);
        input.dataset.original = newValue;
    });
    
    if (hasChanges) {
        editor.value = content;
        setSaveButtonsDisabled(false);
        adjustEditorHeight(editor);
    }
}

function setupEventListeners() {
    const repoSelect = document.getElementById('repoSelect');
    const fileSelect = document.getElementById('fileSelect');
    const editor = document.getElementById('ymlEditor');
    const clearLogBtn = document.getElementById('clearLogBtn');
    
    repoSelect.addEventListener('change', function() {
        fileSelect.value = '';
        editor.value = '';
        editor.placeholder = '选择一个YML文件查看内容...';
        originalContent = '';
        setSaveButtonsDisabled(true);
        setDeployButtonsDisabled(true);
        document.getElementById('deployQueuePanel').classList.add('is-hidden');
        const searchInput = document.getElementById('fileSearch');
        searchInput.value = '';
        loadYmlFiles(this.value, searchInput.value);
    });
    
    fileSelect.addEventListener('change', function() {
        loadFileContent(repoSelect.value, this.value);
    });
    
    editor.addEventListener('input', function() {
        const hasChanges = this.value !== originalContent;
        setSaveButtonsDisabled(!hasChanges);
        adjustEditorHeight(this);
    });
    
    document.addEventListener('input', function(e) {
        if (e.target.classList.contains('param-input')) {
            syncParamsToEditor();
        }
    });
    
    document.querySelectorAll('.deploy-save-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            saveFileContent(repoSelect.value, fileSelect.value, editor.value);
        });
    });
    
    document.querySelectorAll('.deploy-deploy-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            deployApplication(repoSelect.value, fileSelect.value);
        });
    });
    
    clearLogBtn.addEventListener('click', function() {
        const logContainer = document.getElementById('logContainer');
        logContainer.innerHTML = `
            <div class="log-item info">
                <i class="fas fa-info-circle"></i>
                <span>准备就绪，请选择YML文件进行部署</span>
            </div>
        `;
    });
    
    const fileSearch = document.getElementById('fileSearch');
    fileSearch.addEventListener('input', function() {
        renderFileOptions(this.value);
        showFileDropdown();
    });
    fileSearch.addEventListener('focus', showFileDropdown);
    fileSearch.addEventListener('click', showFileDropdown);

    document.getElementById('filePickerToggle').addEventListener('click', function() {
        const dropdown = document.getElementById('fileDropdown');
        if (dropdown.classList.contains('is-hidden')) {
            fileSearch.focus();
            showFileDropdown();
        } else {
            hideFileDropdown();
        }
    });

    document.getElementById('fileDropdown').addEventListener('mousedown', function(event) {
        const option = event.target.closest('.file-option');
        if (!option) return;
        event.preventDefault();
        selectYmlFile(option.dataset.file);
    });

    document.addEventListener('click', function(event) {
        if (!event.target.closest('.search-select-wrapper')) hideFileDropdown();
    });
    
    const easyModeBtn = document.getElementById('easyModeBtn');
    const advancedModeBtn = document.getElementById('advancedModeBtn');
    const networkModeBtn = document.getElementById('networkModeBtn');
    const paramsSection = document.getElementById('paramsSection');
    const editorSection = document.getElementById('editorSection');
    const networkSection = document.getElementById('networkSection');
    
    function _setMode(mode) {
        // 先把所有 tab 的 active 状态清空，所有 section 隐藏
        if (easyModeBtn)     easyModeBtn.classList.remove('active');
        if (advancedModeBtn) advancedModeBtn.classList.remove('active');
        if (networkModeBtn)  networkModeBtn.classList.remove('active');
        if (paramsSection)   paramsSection.style.display = 'none';
        if (editorSection)   editorSection.style.display = 'none';
        if (networkSection)  networkSection.style.display = 'none';

        if (mode === 'easy') {
            if (easyModeBtn) easyModeBtn.classList.add('active');
            if (paramsSection) paramsSection.style.display = 'block';
            try { parseYmlAndShowParams(editor ? editor.value : ''); } catch (e) {}
        } else if (mode === 'advanced') {
            if (advancedModeBtn) advancedModeBtn.classList.add('active');
            if (editorSection) editorSection.style.display = 'block';
            try { if (editor) adjustEditorHeight(editor); } catch (e) {}
        } else if (mode === 'network') {
            if (networkModeBtn) networkModeBtn.classList.add('active');
            if (networkSection) networkSection.style.display = 'block';
            // 切到网络管理时自动加载已有网络列表
            try { loadNetworks(); } catch (e) { console.error('loadNetworks failed:', e); }
        }
    }
    
    easyModeBtn && easyModeBtn.addEventListener('click', function() { _setMode('easy'); });
    advancedModeBtn && advancedModeBtn.addEventListener('click', function() { _setMode('advanced'); });
    if (networkModeBtn) {
        networkModeBtn.addEventListener('click', function() { _setMode('network'); });
    } else {
        console.warn('[deploy] networkModeBtn 元素未找到，网络管理 Tab 未绑定事件');
    }

    // 网络管理：创建网络按钮
    const createNetworkBtn = document.getElementById('createNetworkBtn');
    const refreshNetworksBtn = document.getElementById('refreshNetworksBtn');
    if (createNetworkBtn) createNetworkBtn.addEventListener('click', createNetwork);
    if (refreshNetworksBtn) refreshNetworksBtn.addEventListener('click', loadNetworks);
}

// ============ Docker 网络管理相关函数 ============

async function loadNetworks() {
    const tbody = document.getElementById('networkTableBody');
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="3" class="empty-cell">加载中...</td></tr>`;
    try {
        const response = await apiFetch(`${API_BASE_URL}/networks`);
        const res = await response.json();
        if (!response.ok || !res.success) {
            tbody.innerHTML = `<tr><td colspan="3" class="empty-cell">加载失败：${res.message || '未知错误'}</td></tr>`;
            return;
        }
        const networks = Array.isArray(res.data) ? res.data : [];
        tbody.innerHTML = window.DeployNetwork.render(networks, escapeHtml);
    } catch (error) {
        console.error('加载网络列表失败:', error);
        tbody.innerHTML = `<tr><td colspan="3" class="empty-cell">加载失败：${escapeHtml(error.message || '网络异常')}</td></tr>`;
    }
}

async function createNetwork() {
    const nameInput = document.getElementById('networkName');
    const driverSelect = document.getElementById('networkDriver');
    const createBtn = document.getElementById('createNetworkBtn');
    const msgEl = document.getElementById('networkMessage');
    if (!nameInput || !driverSelect || !createBtn || !msgEl) return;

    const name = nameInput.value.trim();
    const driver = driverSelect.value || 'bridge';

    if (!name) {
        showNetworkMessage('error', '请输入网络名称');
        nameInput.focus();
        return;
    }
    if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(name)) {
        showNetworkMessage('error', '名称不合法：以字母/数字开头，仅支持字母数字下划线中划线点号，最长128字符');
        nameInput.focus();
        return;
    }

    createBtn.disabled = true;
    const originalBtnHTML = createBtn.innerHTML;
    createBtn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> 创建中...`;
    msgEl.style.display = 'none';

    try {
        const response = await apiFetch(`${API_BASE_URL}/networks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, driver: driver })
        });
        const res = await response.json();
        if (res.success) {
            showNetworkMessage('success', res.message || '创建成功');
            nameInput.value = '';
            // 刷新列表
            await loadNetworks();
        } else {
            showNetworkMessage('error', res.message || '创建失败');
        }
    } catch (error) {
        console.error('创建网络失败:', error);
        showNetworkMessage('error', '创建失败：' + (error.message || '网络异常'));
    } finally {
        createBtn.disabled = false;
        createBtn.innerHTML = originalBtnHTML;
    }
}

function showNetworkMessage(type, text) {
    const msgEl = document.getElementById('networkMessage');
    if (!msgEl) return;
    msgEl.className = 'network-message ' + (type === 'success' ? 'success' : 'error');
    msgEl.textContent = text || '';
    msgEl.style.display = 'block';
    // 5秒后自动隐藏成功提示
    if (type === 'success') {
        setTimeout(function() { msgEl.style.display = 'none'; }, 5000);
    }
}

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
