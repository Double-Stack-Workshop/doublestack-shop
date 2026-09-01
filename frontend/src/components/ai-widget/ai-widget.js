let initialized = false;

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function icon(name) {
    return element('i', `fas ${name}`);
}

async function loadAssets() {
    if (!document.getElementById('aiWidgetStyles')) {
        const link = element('link');
        link.id = 'aiWidgetStyles';
        link.rel = 'stylesheet';
        link.href = '/src/components/ai-widget/ai-widget.css';
        document.head.append(link);
    }
    if (window.AIMessage) return;
    await new Promise((resolve, reject) => {
        const existing = document.getElementById('aiMessageRenderer');
        if (existing) {
            existing.addEventListener('load', resolve, { once: true });
            existing.addEventListener('error', reject, { once: true });
            return;
        }
        const script = element('script');
        script.id = 'aiMessageRenderer';
        script.src = '/src/pages/ai/ai-message.js';
        script.addEventListener('load', resolve, { once: true });
        script.addEventListener('error', reject, { once: true });
        document.head.append(script);
    });
}

async function result(response) {
    let body;
    try { body = await response.json(); } catch { throw new Error(`请求失败（HTTP ${response.status}）`); }
    if (!response.ok || !body?.success) {
        const error = new Error(typeof body?.detail === 'string' ? body.detail : `请求失败（HTTP ${response.status}）`);
        error.status = response.status;
        throw error;
    }
    return body.data;
}

export async function initAIWidget() {
    if (initialized || document.getElementById('aiFloatingLauncher')) return;
    initialized = true;
    await loadAssets();
    const { apiFetch, copyText } = window.AppPage;
    let config;
    let conversation;
    let loading = false;
    let started = false;
    let pollTimer;

    const launcher = element('button', 'ai-floating-launcher');
    launcher.id = 'aiFloatingLauncher';
    launcher.type = 'button';
    launcher.title = '打开 AI 对话';
    launcher.setAttribute('aria-label', '打开 AI 对话');
    launcher.setAttribute('aria-expanded', 'false');
    launcher.append(icon('fa-comments'));

    const panel = element('section', 'ai-floating-panel');
    panel.id = 'aiFloatingPanel';
    panel.hidden = true;
    panel.setAttribute('aria-label', 'AI 对话悬浮窗口');
    const header = element('header', 'ai-floating-header');
    const title = element('strong', '', 'AI 对话');
    const headerActions = element('div', 'ai-floating-header-actions');
    const fresh = element('button', 'ai-floating-icon-button');
    fresh.type = 'button'; fresh.title = '新对话'; fresh.setAttribute('aria-label', '新对话'); fresh.append(icon('fa-plus'));
    const close = element('button', 'ai-floating-icon-button');
    close.type = 'button'; close.title = '关闭'; close.setAttribute('aria-label', '关闭'); close.append(icon('fa-times'));
    headerActions.append(fresh, close);
    header.append(title, headerActions);

    const log = element('div', 'ai-floating-log');
    log.setAttribute('role', 'log');
    log.setAttribute('aria-live', 'polite');
    const status = element('p', 'ai-floating-status');
    status.hidden = true;
    status.setAttribute('role', 'alert');
    const form = element('form', 'ai-floating-composer');
    const input = element('textarea');
    input.rows = 1;
    input.maxLength = 262144;
    input.placeholder = '询问 AI';
    input.setAttribute('aria-label', '消息');
    const send = element('button', 'ai-floating-send');
    send.type = 'submit'; send.title = '发送'; send.setAttribute('aria-label', '发送'); send.append(icon('fa-arrow-up'));
    form.append(input, send);
    panel.append(header, log, status, form);
    document.body.append(launcher, panel);

    function showError(message = '') {
        status.textContent = message;
        status.hidden = !message;
        panel.classList.toggle('error', !!message);
    }
    function setLoading(value) {
        loading = value;
        input.disabled = value || !config?.enabled || !config?.model || !conversation || conversation.busy;
        send.disabled = input.disabled;
        fresh.disabled = value;
        panel.classList.toggle('busy', value || !!conversation?.busy);
    }
    async function copy(text) {
        try { await copyText(text); } catch { showError('复制失败，请手动选择内容。'); }
    }
    function draw() {
        log.replaceChildren();
        const messages = conversation?.messages || [];
        if (!messages.length) log.append(element('p', 'ai-floating-empty', '想聊些什么？'));
        messages.forEach(message => {
            const bubble = element('article', `ai-floating-message ${message.role}`);
            const copyButton = element('button', 'ai-floating-copy');
            copyButton.type = 'button'; copyButton.title = '复制'; copyButton.setAttribute('aria-label', '复制消息');
            copyButton.append(icon('fa-copy'));
            copyButton.addEventListener('click', () => copy(message.content));
            const body = element('div', 'message-body');
            window.AIMessage.render(body, message.content, { onCopy: copy, thinking: message.role === 'assistant' });
            bubble.append(copyButton, body);
            log.append(bubble);
        });
        input.value = conversation?.draft || '';
        setLoading(false);
        requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; });
        schedulePoll();
    }
    function schedulePoll() {
        clearTimeout(pollTimer);
        if (!conversation?.busy) return;
        const id = conversation.id;
        pollTimer = setTimeout(async () => {
            try {
                const latest = await result(await apiFetch(`/api/ai/conversations/${id}`));
                if (conversation?.id !== id) return;
                conversation = latest;
                draw();
            } catch (error) {
                showError(error.message);
                schedulePoll();
            }
        }, 2500);
    }
    async function openConversation(id) {
        const endpoint = id ? `/api/ai/conversations/${id}` : '/api/ai/conversations';
        conversation = await result(await apiFetch(endpoint, id ? undefined : { method: 'POST' }));
        draw();
    }
    async function start() {
        if (started) return;
        started = true;
        setLoading(true);
        try {
            config = await result(await apiFetch('/api/ai/config'));
            const records = await result(await apiFetch('/api/ai/conversations'));
            await openConversation(records[0]?.id);
            if (!config.enabled || !config.model) showError('请先在系统设置中完成 OpenAI 配置。');
        } catch (error) {
            showError(error.message);
            setLoading(false);
        }
    }
    async function newConversation() {
        if (input.value && !confirm('新建对话将丢弃未发送的内容，是否继续？')) return;
        setLoading(true); showError('');
        try { await openConversation(); } catch (error) { showError(error.message); setLoading(false); }
    }

    launcher.addEventListener('click', async () => {
        panel.hidden = !panel.hidden;
        launcher.setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');
        if (!panel.hidden) { await start(); if (!input.disabled) input.focus(); }
    });
    close.addEventListener('click', () => {
        panel.hidden = true;
        launcher.setAttribute('aria-expanded', 'false');
        launcher.focus();
    });
    fresh.addEventListener('click', newConversation);
    input.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.isComposing) {
            event.preventDefault();
            form.requestSubmit();
        }
    });
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = `${Math.min(input.scrollHeight, 100)}px`;
    });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (loading || input.disabled || !input.value.trim()) return;
        const prompt = input.value;
        const outgoing = [...conversation.messages, { role: 'user', content: prompt }];
        if (outgoing.length > 99 || new TextEncoder().encode(outgoing.map(message => message.content).join('')).length > 256 * 1024) {
            showError('对话已达到上限，请新建对话。');
            return;
        }
        input.value = '';
        conversation.messages = outgoing;
        draw(); setLoading(true); showError('');
        try {
            const data = await result(await apiFetch('/api/ai/chat', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ messages: outgoing, conversation_id: conversation.id, config_revision: config.revision }),
            }));
            conversation = data.conversation || { ...conversation, messages: data.messages, draft: '', busy: false };
            draw();
        } catch (error) {
            if (error.status === 412 || error.status === 429) {
                try { conversation = await result(await apiFetch(`/api/ai/conversations/${conversation.id}`)); } catch { /* 保留本地记录 */ }
            } else {
                conversation.messages = outgoing.slice(0, -1);
            }
            input.value = prompt;
            draw();
            input.value = prompt;
            showError(error.message);
            setLoading(false);
        }
    });
}
