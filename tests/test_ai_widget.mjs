import assert from 'node:assert/strict';
import test from 'node:test';

class Element {
    constructor(tag = '') {
        this.tagName = tag; this.children = []; this.events = new Map(); this.attributes = {};
        this.className = ''; this.textContent = ''; this.value = ''; this.hidden = false; this.disabled = false;
        this.style = {}; this.scrollHeight = 40;
        this.classList = { toggle: (name, force) => {
            const names = new Set(this.className.split(/\s+/).filter(Boolean));
            if (force) names.add(name); else names.delete(name);
            this.className = [...names].join(' ');
        } };
    }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(name, fn) { if (!this.events.has(name)) this.events.set(name, []); this.events.get(name).push(fn); }
    async fire(name, extra = {}) {
        if (name === 'click' && this.disabled) return;
        await Promise.all((this.events.get(name) || []).map(fn => fn({ preventDefault() {}, ...extra })));
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    focus() {}
    requestSubmit() { return this.fire('submit'); }
}

function descendants(root) { return [root, ...root.children.flatMap(descendants)]; }

test('floating AI opens on project pages, resumes persisted chat and sends through the existing API', async () => {
    const head = new Element('head'); const body = new Element('body');
    const store = new Map(); const calls = [];
    const response = (data, status = 200) => ({ ok: status === 200, status, json: async () => data });
    globalThis.document = {
        head, body,
        createElement: tag => new Element(tag),
        getElementById: id => [...descendants(head), ...descendants(body)].find(node => node.id === id),
    };
    Object.defineProperty(globalThis, 'navigator', {
        configurable: true, value: { clipboard: { writeText: async () => {} } },
    });
    globalThis.confirm = () => true;
    globalThis.requestAnimationFrame = fn => fn();
    let copied = '';
    globalThis.window = {
        AIMessage: { render: (container, text) => container.append(Object.assign(new Element('p'), { textContent: text })) },
        AppPage: { copyText: async text => { copied = text; }, apiFetch: async (url, options) => {
            calls.push({ url, options });
            if (url === '/api/ai/config') return response({ success: true, data: { enabled: true, model: 'model', revision: 'r1' } });
            if (url === '/api/ai/conversations' && !options) return response({ success: true, data: [...store.values()] });
            if (url === '/api/ai/conversations' && options.method === 'POST') {
                const conversation = { id: `c${store.size + 1}`, title: '新对话', messages: [], draft: '', busy: false };
                store.set(conversation.id, conversation);
                return response({ success: true, data: structuredClone(conversation) });
            }
            if (url.startsWith('/api/ai/conversations/')) return response({ success: true, data: structuredClone(store.get(url.split('/').at(-1))) });
            assert.equal(url, '/api/ai/chat');
            const request = JSON.parse(options.body);
            const conversation = store.get(request.conversation_id);
            conversation.messages = [...request.messages, { role: 'assistant', content: '悬浮窗口回复' }];
            conversation.title = request.messages[0].content;
            return response({ success: true, data: { messages: conversation.messages, conversation: structuredClone(conversation) } });
        } },
    };
    const { initAIWidget } = await import('../frontend/src/components/ai-widget/ai-widget.js');
    await initAIWidget();
    const launcher = document.getElementById('aiFloatingLauncher');
    const panel = document.getElementById('aiFloatingPanel');
    assert.ok(launcher); assert.equal(panel.hidden, true);
    await launcher.fire('click');
    assert.equal(panel.hidden, false); assert.equal(store.size, 1);
    const textarea = descendants(panel).find(node => node.tagName === 'textarea');
    const form = descendants(panel).find(node => node.tagName === 'form');
    textarea.value = '你好';
    await form.fire('submit');
    const chat = calls.find(call => call.url === '/api/ai/chat');
    assert.deepEqual(JSON.parse(chat.options.body).messages, [{ role: 'user', content: '你好' }]);
    assert.equal(store.get('c1').messages.at(-1).content, '悬浮窗口回复');
    assert.ok(descendants(panel).some(node => node.textContent === '悬浮窗口回复'));
    // Fake DOM does not assign parentNode, so the newest copy button corresponds to the assistant reply.
    const copyButtons = descendants(panel).filter(node => node.attributes['aria-label'] === '复制消息');
    await copyButtons.at(-1).fire('click');
    assert.equal(copied, '悬浮窗口回复');
    const newChat = descendants(panel).find(node => node.attributes['aria-label'] === '新对话');
    await newChat.fire('click');
    assert.equal(store.size, 2);
});
