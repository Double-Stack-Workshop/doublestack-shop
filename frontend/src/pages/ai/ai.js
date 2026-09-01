(function () {
    const { apiFetch, copyText, loadSidebar, populateUsername, syncCurrentUser } = window.AppPage;
    const byId = id => document.getElementById(id);
    const input = byId('chatInput');
    const send = byId('sendChatBtn');
    const stop = byId('stopChatBtn');
    const history = byId('chatHistory');
    const conversationList = byId('conversationList');
    const card = byId('chatCard');
    let conversationId = null;
    let conversations = [];
    let loadingConversation = true;
    let remoteBusy = false;
    let historySequence = 0;
    let pollTimer;
    let messages = [];
    let warnings = new Map();
    let config = null;
    let controller = null;
    let sequence = 0;
    let pendingText = null;
    let loadingConfig = false;
    let hasError = false;

    function status(text, error = false) {
        hasError = error && !!text;
        byId('chatStatus').textContent = error ? text : '';
        byId('chatStatus').hidden = !error || !text;
        byId('chatStatus').classList.toggle('error', error);
        updateControls();
    }
    function updateControls() {
        const ready = config?.enabled && config?.model && !loadingConfig && !loadingConversation && conversationId;
        input.disabled = !!controller || remoteBusy || !ready;
        send.disabled = !!controller || remoteBusy || !ready;
        conversationList.querySelectorAll('button').forEach(button => { button.disabled = !!controller || loadingConversation; });
        byId('newChatBtn').disabled = loadingConversation;
        send.hidden = !!controller;
        stop.hidden = !controller;
        const busy = controller || remoteBusy || loadingConfig || loadingConversation;
        const failed = hasError || (!config && !busy);
        const state = failed ? 'error' : busy ? 'busy' : ready ? 'ready' : '';
        card.className = `chat-card${state ? ` ${state}` : ''}`;
        const label = failed ? '失败' : controller || remoteBusy ? '回复中' : busy ? '恢复中' : ready ? '已就绪' : '未就绪';
        byId('chatState').textContent = label;
        card.title = label;
    }
    async function readResult(response) {
        let result;
        try { result = await response.json(); } catch {
            throw new Error(`AI 请求返回非 JSON 响应（HTTP ${response.status}），请检查反向代理和后端日志。`);
        }
        if (!response.ok || !result?.success) {
            const error = new Error(typeof result?.detail === 'string' ? result.detail
                : `AI 请求失败（HTTP ${response.status}），请检查完整对话长度或服务配置。`);
            error.status = response.status;
            throw error;
        }
        return result.data;
    }
    async function copy(text) {
        try {
            await copyText(text);
            status('已复制。');
        } catch { status('复制失败，请选中对话文本手动复制。', true); }
    }
    function appendMessage(message, index, pending = false) {
        const article = document.createElement('article');
        article.className = `chat-message ${message.role}`;
        const header = document.createElement('div');
        header.className = 'message-header';
        const name = document.createElement('strong');
        name.textContent = message.role === 'user' ? '你' : 'AI';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'app-button app-button-secondary';
        button.textContent = '复制';
        button.addEventListener('click', () => copy(message.content));
        header.append(name, button);
        const content = document.createElement('div');
        content.className = 'message-body';
        window.AIMessage.render(content, message.content, { onCopy: copy, thinking: message.role === 'assistant' });
        article.append(header, content);
        const note = pending ? '正在等待 AI 回复…' : warnings.get(index);
        if (note) {
            const label = document.createElement('p');
            label.className = 'message-warning';
            label.textContent = note;
            article.append(label);
        }
        history.append(article);
    }
    function render() {
        history.replaceChildren();
        if (!messages.length && pendingText === null) {
            const empty = document.createElement('div');
            empty.className = 'chat-empty';
            empty.textContent = '开始一段新对话。你可以自由提问，也可以粘贴代码或 YML 一起讨论。';
            history.append(empty);
        }
        messages.forEach((message, index) => appendMessage(message, index));
        if (pendingText !== null) appendMessage({ role: 'user', content: pendingText }, -1, true);
        updateControls();
        resizeInput();
    }
    function resizeInput() {
        input.style.height = 'auto';
        input.style.height = `${Math.max(40, input.scrollHeight)}px`;
    }
    async function readConfig() {
        loadingConfig = true;
        updateControls();
        try {
            config = await readResult(await apiFetch('/api/ai/config'));
            status('');
        } catch (error) {
            config = null;
            status(error.message, true);
        } finally { loadingConfig = false; updateControls(); }
    }
    function updateHistory(data) {
        conversations = [{ id: data.id, title: data.title }, ...conversations.filter(item => item.id !== data.id)];
        conversationList.replaceChildren();
        conversations.forEach(item => {
            const row = document.createElement('li');
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'history-conversation';
            button.textContent = item.title || '新对话';
            button.setAttribute('aria-current', item.id === conversationId ? 'true' : 'false');
            button.addEventListener('click', () => switchConversation(item.id));
            row.append(button);
            conversationList.append(row);
        });
        updateControls();
    }
    function applyConversation(data) {
        conversationId = data.id;
        messages = data.messages;
        warnings = new Map(data.warnings.map(item => [item.index, item.text]));
        input.value = data.draft || '';
        pendingText = null;
        remoteBusy = data.busy;
        updateHistory(data);
        render();
    }
    function pollConversation() {
        clearTimeout(pollTimer);
        if (!remoteBusy) return;
        const current = conversationId;
        const generation = historySequence;
        pollTimer = setTimeout(async () => {
            try {
                const data = await readResult(await apiFetch(`/api/ai/conversations/${current}`));
                if (generation !== historySequence || current !== conversationId || controller) return;
                applyConversation(data);
                status('');
                pollConversation();
            } catch (error) {
                if (generation !== historySequence) return;
                status(error.message, true);
                pollConversation();
            }
        }, 2500);
    }
    async function openConversation(id = null) {
        const generation = ++historySequence;
        clearTimeout(pollTimer);
        loadingConversation = true;
        status('');
        try {
            const data = await readResult(await apiFetch(id ? `/api/ai/conversations/${id}` : '/api/ai/conversations',
                id ? undefined : { method: 'POST' }));
            if (generation !== historySequence) return;
            applyConversation(data);
            status('');
            pollConversation();
        } catch (error) {
            if (generation !== historySequence) return;
            status(error.message, true);
        } finally {
            if (generation === historySequence) { loadingConversation = false; updateControls(); }
        }
    }
    function cancel() {
        sequence += 1;
        controller?.abort();
        controller = null;
        if (pendingText !== null) input.value = pendingText;
        pendingText = null;
        render();
    }
    byId('chatForm').addEventListener('submit', async event => {
        event.preventDefault();
        if (controller || loadingConfig || loadingConversation || remoteBusy || !conversationId || !config?.enabled || !config.model) return;
        if (!input.value.trim()) return status('请先输入消息。', true);
        const outgoing = [...messages, { role: 'user', content: input.value }];
        if (outgoing.length > 99 || new TextEncoder().encode(outgoing.map(message => message.content).join('')).length > 256 * 1024) {
            return status('对话已达到 50 轮或 256 KB 上限，请开启新对话。历史记录已保留，不会自动删减。', true);
        }
        const id = ++sequence;
        controller = new AbortController();
        pendingText = input.value;
        input.value = '';
        render();
        status('正在等待完整回复，最多约 2 分钟；不会执行任何文件或部署操作。');
        try {
            const data = await readResult(await apiFetch('/api/ai/chat', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
                body: JSON.stringify({ messages: outgoing, config_revision: config.revision, conversation_id: conversationId }),
            }));
            if (id !== sequence) return;
            if (!Array.isArray(data?.messages) || data.messages.length !== outgoing.length + 1
                || data.messages.some((message, index) => !message || typeof message.content !== 'string'
                    || message.role !== (index % 2 === 0 ? 'user' : 'assistant'))) {
                throw new Error('AI 对话响应格式错误，原有对话已保留。');
            }
            messages = data.messages;
            if (data.warning) warnings.set(messages.length - 1, data.warning);
            if (data.conversation) updateHistory(data.conversation);
            pendingText = null;
            status(data.warning || '已收到完整回复，可继续提问。');
        } catch (error) {
            if (id !== sequence) return;
            input.value = pendingText ?? '';
            pendingText = null;
            if (error.status === 409) {
                config = null;
                await readConfig();
                if (id !== sequence) return;
                status(config ? 'AI 配置已更新，请再次点击发送消息。' : '读取 AI 配置失败，请检查接口设置。', true);
            } else if (error.status === 412 || error.status === 429) {
                const draft = input.value;
                await openConversation(conversationId);
                if (id !== sequence) return;
                if (!remoteBusy) input.value = draft;
                status(error.message, true);
            } else {
                status(error.name === 'AbortError' ? '已停止等待，消息已恢复。' : error.message, true);
            }
        } finally {
            if (id === sequence) {
                controller = null;
                render();
                if (!input.disabled) input.focus({ preventScroll: true });
            }
        }
    });
    input.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.isComposing) {
            event.preventDefault();
            byId('chatForm').requestSubmit();
        }
    });
    input.addEventListener('input', resizeInput);
    stop.addEventListener('click', () => {
        cancel();
        status('已停止等待，消息已恢复。服务商可能仍在处理或计费。');
    });
    byId('newChatBtn').addEventListener('click', async () => {
        if (loadingConversation) return;
        if ((pendingText !== null || input.value) && !confirm('开始新对话？未发送的输入将丢弃，已提交的对话会保留在历史记录中。')) return;
        cancel();
        await openConversation();
    });
    async function switchConversation(id) {
        if (controller || loadingConversation) return;
        if (input.value && !remoteBusy && !confirm('切换对话将丢弃未发送的输入，是否继续？')) {
            return;
        }
        await openConversation(id);
    }
    window.addEventListener('focus', () => {
        if (!controller && !loadingConfig) return readConfig();
    });
    window.addEventListener('pageshow', event => {
        if (event.persisted && !controller && !loadingConfig) return readConfig();
    });
    window.addEventListener('beforeunload', event => {
        if (pendingText !== null || input.value) { event.preventDefault(); event.returnValue = ''; }
    });
    async function initialize() {
        const user = await syncCurrentUser();
        if (!user || user.must_change_password) return;
        if (!user.is_admin) { window.location.replace('/src/pages/dashboard/dashboard.html'); return; }
        populateUsername();
        loadSidebar('/ai/ai.html').catch(() => status('侧边栏加载失败，请刷新页面。', true));
        await readConfig();
        conversations = await readResult(await apiFetch('/api/ai/conversations'));
        await openConversation(conversations[0]?.id);
    }
    initialize().catch(() => status('初始化失败，请检查登录状态后刷新页面。', true));
}());
