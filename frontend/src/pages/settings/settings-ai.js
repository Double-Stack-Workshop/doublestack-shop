(function () {
    const { API_BASE_URL, apiFetch } = window.AppPage;
    const form = document.getElementById('aiConfigForm');
    const fields = document.getElementById('aiConfigFields');
    const status = document.getElementById('aiConfigStatus');
    const key = document.getElementById('aiApiKey');
    const testButton = document.getElementById('testAiBtn');
    const saveButton = document.getElementById('saveAiBtn');
    let dirty = false;
    let keyEdited = false;
    let busy = false;

    function showStatus(text, error = false) {
        status.textContent = text;
        status.hidden = !text;
        status.classList.toggle('error', error);
    }

    function render(config, keepKey = false) {
        document.getElementById('aiEnabled').checked = config.enabled;
        document.getElementById('aiBaseUrl').value = config.base_url;
        document.getElementById('aiModel').value = config.model;
        // 初次加载只显示掩码；保存后保留本页输入，不从服务器回传真实密钥。
        if (!keepKey) key.value = config.has_api_key ? '********' : '';
        keyEdited = false;
        dirty = false;
    }

    function setBusy(value, lockFields = false) {
        busy = value;
        // 首次读取前锁定空表单；保存和测试期间只锁定操作按钮，避免输入栏闪烁。
        fields.disabled = lockFields;
        testButton.disabled = value;
        saveButton.disabled = value;
    }

    async function readResult(response) {
        let result;
        try {
            result = await response.json();
        } catch {
            throw new Error(`AI 请求返回非 JSON 响应（HTTP ${response.status}），请检查反向代理和后端服务日志。`);
        }
        if (!response.ok || !result?.success) {
            throw new Error(typeof result?.detail === 'string' ? result.detail
                : `请求失败（HTTP ${response.status}），请检查填写内容或服务日志`);
        }
        return result;
    }

    form.addEventListener('input', () => { dirty = true; });
    key.addEventListener('input', () => { keyEdited = true; });
    key.addEventListener('focus', () => {
        if (!keyEdited && key.value === '********') key.select();
    });

    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy) return;
        const body = {
            enabled: document.getElementById('aiEnabled').checked,
            base_url: document.getElementById('aiBaseUrl').value.trim(),
            model: document.getElementById('aiModel').value.trim(),
            api_key: keyEdited ? key.value || null : null,
        };
        setBusy(true);
        showStatus('');
        try {
            const result = await readResult(await apiFetch(`${API_BASE_URL}/ai/config`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
            }));
            render(result.data, true);
            showStatus('');
            window.showMessage('OpenAI 配置保存成功', 'success');
        } catch (error) {
            window.showMessage(error.message, 'error');
        } finally {
            setBusy(false);
        }
    });

    testButton.addEventListener('click', async () => {
        if (busy) return;
        if (dirty) {
            showStatus('');
            window.showMessage('配置已修改，请先保存再测试', 'error');
            return;
        }
        setBusy(true, true);
        showStatus('');
        try {
            const result = await readResult(await apiFetch(`${API_BASE_URL}/ai/test`, { method: 'POST' }));
            window.showMessage(result.message || 'OpenAI 连接测试成功', 'success');
        } catch (error) {
            window.showMessage(error.message, 'error');
        } finally {
            setBusy(false);
        }
    });

    async function initialize() {
        setBusy(true);
        try {
            const result = await readResult(await apiFetch(`${API_BASE_URL}/ai/config`));
            render(result.data);
            setBusy(false);
            showStatus('');
        } catch (error) {
            showStatus(`读取配置失败：${error.message}。请确认管理员登录后刷新页面。`, true);
        }
    }
    initialize();
}());
