const API_BASE_URL = '/api';

let originalContent = '';

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
    const response = await fetch('/src/components/sidebar/sidebar.html');
    const sidebarHtml = await response.text();
    const appContainer = document.getElementById('appContainer');
    appContainer.insertAdjacentHTML('afterbegin', sidebarHtml);
    
    const script = document.createElement('script');
    script.src = '/src/components/sidebar/sidebar.js';
    script.type = 'module';
    script.onload = function() {
        import('/src/components/sidebar/sidebar.js').then(({ initSidebar }) => {
            initSidebar('deploy');
        });
    };
    document.head.appendChild(script);
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
        const response = await fetch(`${API_BASE_URL}/repos`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const repos = await response.json();
        
        repos.forEach(repo => {
            const option = document.createElement('option');
            option.value = repo.name;
            option.textContent = repo.name;
            select.appendChild(option);
        });
        
        if (searchQuery && repos.length > 0) {
            select.value = repos[0].name;
            await loadYmlFiles(repos[0].name, searchQuery);
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
    allYmlFiles = [];
    
    if (!repoName) {
        searchInput.style.display = 'none';
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE_URL}/repos/${repoName}/files`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const files = await response.json();
        
        allYmlFiles = files.filter(file => file.name.endsWith('.yml') || file.name.endsWith('.yaml'));
        
        let filteredFiles = allYmlFiles;
        if (searchQuery) {
            filteredFiles = allYmlFiles.filter(file => 
                file.name.toLowerCase().includes(searchQuery.toLowerCase())
            );
        }
        
        filteredFiles.forEach(file => {
            const option = document.createElement('option');
            option.value = file.name;
            option.textContent = file.name;
            select.appendChild(option);
        });
        
        searchInput.style.display = allYmlFiles.length > 0 ? 'block' : 'none';
        
        if (allYmlFiles.length === 0) {
            addLog('warning', '该仓库中没有找到YML文件');
        } else if (searchQuery && filteredFiles.length === 0) {
            addLog('warning', `未找到匹配 "${searchQuery}" 的文件`);
        }
    } catch (error) {
        console.error('Error loading files:', error);
        addLog('error', '获取文件列表失败: ' + error.message);
        searchInput.style.display = 'none';
    }
}

function filterFiles(searchText) {
    const select = document.getElementById('fileSelect');
    select.innerHTML = '<option value="">请选择YML文件</option>';
    
    const filteredFiles = allYmlFiles.filter(file => 
        file.name.toLowerCase().includes(searchText.toLowerCase())
    );
    
    filteredFiles.forEach(file => {
        const option = document.createElement('option');
        option.value = file.name;
        option.textContent = file.name;
        select.appendChild(option);
    });
}

async function loadFileContent(repoName, fileName) {
    const editor = document.getElementById('ymlEditor');
    const saveBtn = document.getElementById('saveBtn');
    const deployBtn = document.getElementById('deployBtn');
    const modeSection = document.getElementById('modeSection');
    const paramsSection = document.getElementById('paramsSection');
    const editorSection = document.getElementById('editorSection');
    const outputSection = document.getElementById('outputSection');
    
    if (!repoName || !fileName) {
        editor.value = '';
        editor.placeholder = '选择一个YML文件查看内容...';
        originalContent = '';
        saveBtn.disabled = true;
        deployBtn.disabled = true;
        document.getElementById('fileName').textContent = '未选择文件';
        document.getElementById('fileSize').textContent = '0 KB';
        document.getElementById('lastModified').textContent = '未知';
        editor.style.height = '150px';
        modeSection.style.display = 'none';
        paramsSection.style.display = 'none';
        editorSection.style.display = 'none';
        outputSection.style.display = 'none';
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE_URL}/repos/${repoName}/files/${encodeURIComponent(fileName)}`);
        
        if (!response.ok) {
            throw new Error('文件读取失败');
        }
        
        const data = await response.json();
        originalContent = data.content;
        editor.value = data.content;
        editor.placeholder = '';
        
        document.getElementById('fileName').textContent = fileName;
        document.getElementById('fileSize').textContent = `${(data.content.length / 1024).toFixed(2)} KB`;
        document.getElementById('lastModified').textContent = data.last_modified || '未知';
        
        saveBtn.disabled = true;
        deployBtn.disabled = false;
        
        adjustEditorHeight(editor);
        parseYmlAndShowParams(data.content);
        
        modeSection.style.display = 'block';
        paramsSection.style.display = 'block';
        editorSection.style.display = 'none';
        outputSection.style.display = 'block';
        
        addLog('info', `已加载文件: ${fileName}`);
    } catch (error) {
        addLog('error', '读取文件内容失败: ' + error.message);
        editor.value = '';
        editor.placeholder = '文件读取失败';
        editor.style.height = '150px';
        clearParamsTable();
        modeSection.style.display = 'none';
        outputSection.style.display = 'none';
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
                    let editType = 'single';
                    let originalValues = [];
                    
                    if (Array.isArray(config[field])) {
                        editType = 'multi';
                        if (field === 'ports') {
                            originalValues = config[field].map(p => p.split('-').pop().trim());
                            displayValue = config[field].map((p, idx) => {
                                const portMapping = p.split('-').pop().trim();
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
                            originalValues = config[field].map(v => v.split('-').pop().trim());
                            displayValue = config[field].map((v, idx) => {
                                const fullPath = v.split('-').pop().trim();
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
                            originalValues = config[field].map(e => e.split('-').pop().trim());
                            displayValue = config[field].map((e, idx) => {
                                const env = e.split('-').pop().trim();
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
                            originalValues = config[field];
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
    const lines = content.split('\n');
    const services = {};
    let currentService = null;
    let currentConfig = {};
    let inServices = false;
    let currentArrayKey = null;
    
    for (let i = 0; i < lines.length; i++) {
        const originalLine = lines[i];
        let line = originalLine.trim();
        
        if (line === '' || line.startsWith('#')) continue;
        
        if (line.startsWith('services:')) {
            inServices = true;
            continue;
        }
        
        if (!inServices) continue;
        
        if (line.startsWith('- ')) {
            if (currentArrayKey && currentService) {
                const item = line.substring(1).trim();
                if (!currentConfig[currentArrayKey]) {
                    currentConfig[currentArrayKey] = [];
                }
                currentConfig[currentArrayKey].push(item);
            }
            continue;
        }
        
        const colonIndex = line.indexOf(':');
        if (colonIndex === -1) {
            continue;
        }
        
        const key = line.substring(0, colonIndex).trim();
        const value = line.substring(colonIndex + 1).trim();
        
        const leadingSpaces = originalLine.length - originalLine.trimStart().length;
        
        if (leadingSpaces <= 2 && key !== 'services') {
            currentService = key;
            currentConfig = {};
            services[currentService] = currentConfig;
            currentArrayKey = null;
        } else if (currentService) {
            if (value === '') {
                currentConfig[key] = [];
                currentArrayKey = key;
            } else {
                currentConfig[key] = value;
                currentArrayKey = null;
            }
        }
    }
    
    return services;
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
        const response = await fetch(`${API_BASE_URL}/repos/${repoName}/files/${encodeURIComponent(fileName)}`, {
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
            document.getElementById('saveBtn').disabled = true;
            addLog('success', '文件保存成功');
        } else {
            addLog('error', '保存失败: ' + result.message);
        }
    } catch (error) {
        addLog('error', '保存文件失败: ' + error.message);
    }
}

async function deployApplication(repoName, fileName) {
    const deployBtn = document.getElementById('deployBtn');
    
    try {
        deployBtn.disabled = true;
        addLog('info', `开始部署应用: ${fileName}`);
        
        const response = await fetch(`${API_BASE_URL}/deploy`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                repo_name: repoName,
                file_name: fileName
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            if (result.data && result.data.detailed_logs) {
                result.data.detailed_logs.forEach(log => {
                    if (log.includes('[镜像拉取]')) {
                        addLog('info', log);
                    } else if (log.includes('[启动日志]')) {
                        addLog('info', log);
                    } else if (log.includes('[部署成功]')) {
                        addLog('success', log);
                    } else {
                        addLog('info', log);
                    }
                });
            } else if (result.data && result.data.output) {
                addLog('info', '部署日志: ' + result.data.output);
            }
            addLog('success', '应用部署成功');
            if (result.data && result.data.container_id) {
                addLog('info', '容器ID: ' + result.data.container_id);
            }
        } else {
            addLog('error', '部署失败: ' + result.message);
        }
    } catch (error) {
        addLog('error', '部署应用失败: ' + error.message);
    } finally {
        deployBtn.disabled = false;
    }
}

function addLog(type, message) {
    const logContainer = document.getElementById('logContainer');
    const logItem = document.createElement('div');
    logItem.className = `log-item ${type}`;
    
    const iconMap = {
        info: 'fas fa-info-circle',
        success: 'fas fa-check-circle',
        warning: 'fas fa-exclamation-triangle',
        error: 'fas fa-times-circle'
    };
    
    logItem.innerHTML = `
        <i class="${iconMap[type]}"></i>
        <span>${message}</span>
    `;
    
    logContainer.appendChild(logItem);
    logContainer.scrollTop = logContainer.scrollHeight;
}

function syncParamsToEditor() {
    const editor = document.getElementById('ymlEditor');
    const saveBtn = document.getElementById('saveBtn');
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
        saveBtn.disabled = false;
        adjustEditorHeight(editor);
    }
}

function setupEventListeners() {
    const repoSelect = document.getElementById('repoSelect');
    const fileSelect = document.getElementById('fileSelect');
    const editor = document.getElementById('ymlEditor');
    const saveBtn = document.getElementById('saveBtn');
    const deployBtn = document.getElementById('deployBtn');
    const clearLogBtn = document.getElementById('clearLogBtn');
    
    repoSelect.addEventListener('change', function() {
        fileSelect.value = '';
        editor.value = '';
        editor.placeholder = '选择一个YML文件查看内容...';
        originalContent = '';
        saveBtn.disabled = true;
        deployBtn.disabled = true;
        const searchInput = document.getElementById('fileSearch');
        loadYmlFiles(this.value, searchInput.value);
    });
    
    fileSelect.addEventListener('change', function() {
        loadFileContent(repoSelect.value, this.value);
    });
    
    editor.addEventListener('input', function() {
        const hasChanges = this.value !== originalContent;
        saveBtn.disabled = !hasChanges;
        adjustEditorHeight(this);
    });
    
    document.addEventListener('input', function(e) {
        if (e.target.classList.contains('param-input')) {
            syncParamsToEditor();
        }
    });
    
    saveBtn.addEventListener('click', function() {
        saveFileContent(repoSelect.value, fileSelect.value, editor.value);
    });
    
    deployBtn.addEventListener('click', function() {
        deployApplication(repoSelect.value, fileSelect.value);
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
        filterFiles(this.value);
    });
    
    const easyModeBtn = document.getElementById('easyModeBtn');
    const advancedModeBtn = document.getElementById('advancedModeBtn');
    const paramsSection = document.getElementById('paramsSection');
    const editorSection = document.getElementById('editorSection');
    
    easyModeBtn.addEventListener('click', function() {
        easyModeBtn.classList.add('active');
        advancedModeBtn.classList.remove('active');
        paramsSection.style.display = 'block';
        editorSection.style.display = 'none';
        parseYmlAndShowParams(editor.value);
    });
    
    advancedModeBtn.addEventListener('click', function() {
        advancedModeBtn.classList.add('active');
        easyModeBtn.classList.remove('active');
        paramsSection.style.display = 'none';
        editorSection.style.display = 'block';
        adjustEditorHeight(editor);
    });
}
