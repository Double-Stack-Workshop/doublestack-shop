import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const script = readFileSync(new URL('../frontend/src/pages/ai/ai.js', import.meta.url), 'utf8');
const renderer = readFileSync(new URL('../frontend/src/pages/ai/ai-message.js', import.meta.url), 'utf8');
const settingsScript = readFileSync(new URL('../frontend/src/pages/settings/settings-ai.js', import.meta.url), 'utf8');
const commonScript = readFileSync(new URL('../frontend/src/components/common/page-common.js', import.meta.url), 'utf8');
class Element {
    constructor() {
        this.value = ''; this.textContent = ''; this.children = [];
        this.disabled = false; this.hidden = false; this.checked = false;
        this.events = new Map(); this.classList = { toggle() {} };
        this.style = {}; this.scrollHeight = 40; this.attributes = {};
    }
    set innerHTML(_) { throw new Error('Never render AI content as HTML'); }
    addEventListener(name, fn) {
        if (!this.events.has(name)) this.events.set(name, []);
        this.events.get(name).push(fn);
    }
    async fire(name, extra = {}) {
        if (name === 'click' && this.disabled) return;
        await Promise.all((this.events.get(name) || []).map(fn => fn({ preventDefault() {}, ...extra })));
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tagName === tag ? [child] : []), ...child.querySelectorAll(tag)]); }
    focus() {}
}
async function setup(store = new Map()) {
    const elements = Object.fromEntries(['chatInput', 'chatState', 'chatCard', 'sendChatBtn', 'stopChatBtn',
        'chatHistory', 'chatStatus', 'chatForm', 'newChatBtn', 'conversationList'].map(id => [id, new Element()]));
    const state = { elements, calls: [], store, clipboard: '', confirm: true,
        config: { enabled: true, model: 'test-model', base_url: 'https://ai.example.test/v1', revision: 'r1' } };
    state.answer = '这是完整解释。\n<script>alert("xss")</script>';
    const response = (body, code = 200) => ({ ok: code === 200, status: code, json: async () => body });
    state.chat = async outgoing => response({ success: true, data: {
        messages: [...outgoing, { role: 'assistant', content: state.answer }], warning: '', finish_reason: 'stop',
    } });
    const windowEvents = new Map();
    const context = {
        window: { AppPage: {
            syncCurrentUser: async () => ({ is_admin: true }), populateUsername() {}, loadSidebar: async () => {},
            copyText: async text => { if (state.copyFails) throw new Error('denied'); state.clipboard = text; },
            apiFetch: async (url, options) => {
                if (url.startsWith('/api/ai/conversations')) {
                    let data;
                    if (options?.method === 'POST') {
                        data = { id: `conversation-${store.size}`, title: '新对话', messages: [], warnings: [], draft: '', busy: false };
                        store.set(data.id, data);
                    } else if (url === '/api/ai/conversations') {
                        data = [...store.values()].reverse();
                    } else data = store.get(url.split('/').at(-1));
                    return response({ success: true, data: structuredClone(data) });
                }
                state.calls.push({ url, options });
                if (url === '/api/ai/config') return response({ success: true, data: { ...state.config } });
                assert.equal(url, '/api/ai/chat', 'Chat must never save files or deploy');
                const body = JSON.parse(options.body);
                const conversation = store.get(body.conversation_id);
                assert.ok(conversation, 'Every chat from the page must be persisted');
                conversation.draft = body.messages.at(-1).content;
                const reply = await state.chat(body.messages);
                if (!reply.ok) return reply;
                const result = await reply.json();
                conversation.messages = result.data.messages;
                conversation.title = body.messages[0].content.slice(0, 40);
                conversation.draft = '';
                if (result.data.warning) conversation.warnings.push({ index: conversation.messages.length - 1, text: result.data.warning });
                return response({ ...result, data: { ...result.data, conversation: structuredClone(conversation) } });
            },
        }, addEventListener: (name, fn) => windowEvents.set(name, fn) },
        document: { getElementById: id => elements[id], createElement: tag => { const element = new Element(); element.tagName = tag; return element; } },
        navigator: { clipboard: { writeText: async text => { state.clipboard = text; } } },
        AbortController, TextEncoder, confirm: () => state.confirm,
        setTimeout: fn => { state.poll = fn; }, clearTimeout() {},
    };
    vm.runInNewContext(renderer, context);
    vm.runInNewContext(script, context);
    await new Promise(setImmediate);
    state.response = response;
    state.focus = () => windowEvents.get('focus')();
    state.beforeUnload = event => windowEvents.get('beforeunload')(event);
    state.send = async text => {
        elements.chatInput.value = text;
        await elements.chatForm.fire('submit');
    };
    state.find = (root, predicate) => [root, ...root.children.flatMap(child => state.find(child, predicate))].filter(predicate);
    state.copyReply = () => state.find(elements.chatHistory, node => node.className === 'message-header').at(-1).children[1].fire('click');
    state.contents = () => state.find(elements.chatHistory, node => node.className === 'message-body')
        .map(body => state.find(body, node => node.className === 'message-content' || node.tagName === 'code').map(node => node.textContent).join(''));
    return state;
}

test('copy helper supports plain HTTP pages without using the secure clipboard API', async () => {
    let selected = '';
    const body = new Element();
    const context = {
        window: { isSecureContext: false },
        navigator: { clipboard: { writeText: async () => { throw new Error('must not be used over HTTP'); } } },
        localStorage: { getItem() {}, setItem() {}, removeItem() {} },
        document: {
            body,
            createElement: () => {
                const helper = new Element();
                helper.setSelectionRange = () => { selected = helper.value; };
                helper.select = () => {};
                helper.remove = () => {};
                return helper;
            },
            execCommand: command => command === 'copy' && selected === '普通文本',
        },
    };
    vm.runInNewContext(commonScript, context);
    await context.window.AppPage.copyText('普通文本');
    assert.equal(selected, '普通文本');
});

test('chat shows entire replies safely and sends all previous turns as context', async () => {
    const state = await setup();
    const e = state.elements;
    e.chatInput.value = '你好';
    await e.chatForm.fire('submit');
    assert.equal(state.calls.length, 2, 'Send directly without a consent checkbox');
    assert.deepEqual(state.contents(), ['你好', state.answer]);
    await state.send('请解释第二点');
    assert.deepEqual(JSON.parse(state.calls[2].options.body).messages, [
        { role: 'user', content: '你好' }, { role: 'assistant', content: state.answer },
        { role: 'user', content: '请解释第二点' },
    ]);
    assert.equal(state.contents().length, 4);
    await state.copyReply();
    assert.equal(state.clipboard, state.answer);
});

test('failed replies preserve history and restore pending input for retry', async () => {
    const state = await setup();
    await state.send('第一条');
    state.chat = async () => state.response({ detail: '[AI_UPSTREAM_HTTP] HTTP 401' }, 502);
    await state.send('第二条');
    assert.deepEqual(state.contents(), ['第一条', state.answer]);
    assert.equal(state.elements.chatInput.value, '第二条');
    assert.match(state.elements.chatStatus.textContent, /HTTP 401/);
    assert.equal(state.elements.chatCard.className, 'chat-card error');
    assert.equal(state.elements.sendChatBtn.disabled, false);
    state.chat = async () => ({ status: 502, ok: false, json: async () => { throw new Error('private HTML'); } });
    await state.send('第二条');
    assert.match(state.elements.chatStatus.textContent, /HTTP 502/);
    assert.doesNotMatch(state.elements.chatStatus.textContent, /private HTML/);
});

test('stopping ignores a late reply and a new conversation does not include old history', async () => {
    const state = await setup();
    const e = state.elements;
    await state.send('旧消息');
    let resolve;
    const originalChat = state.chat;
    state.chat = () => new Promise(done => { resolve = done; });
    const pending = state.send('取消的消息');
    await e.stopChatBtn.fire('click');
    assert.equal(state.calls[2].options.signal.aborted, true);
    assert.equal(e.chatInput.value, '取消的消息');
    await e.newChatBtn.fire('click');
    resolve(await originalChat([{ role: 'user', content: '迟到的消息' }]));
    await pending;
    assert.deepEqual(state.contents(), []);
    assert.equal(e.chatInput.value, '');
    state.chat = originalChat;
    await state.send('新消息');
    assert.deepEqual(JSON.parse(state.calls.at(-1).options.body).messages, [{ role: 'user', content: '新消息' }]);
});

test('partial output is kept with a visible warning and can be continued', async () => {
    const state = await setup();
    state.chat = async messages => state.response({ success: true, data: {
        messages: [...messages, { role: 'assistant', content: '第一部分内容' }], warning: '达到输出上限，可继续', finish_reason: 'length',
    } });
    await state.send('长问题');
    assert.deepEqual(state.contents(), ['长问题', '第一部分内容']);
    assert.equal(state.elements.chatStatus.hidden, true);
    assert.match(state.find(state.elements.chatHistory, node => node.className === 'message-warning')[0].textContent, /达到输出上限/);
    await state.send('请继续');
    assert.equal(JSON.parse(state.calls.at(-1).options.body).messages[1].content, '第一部分内容');
});

test('changed settings reload automatically but never automatically resend history', async () => {
    const state = await setup();
    const e = state.elements;
    const original = state.chat;
    state.chat = async () => {
        state.config.revision = 'r2';
        state.config.base_url = 'https://other.example.test/v1';
        return state.response({ detail: 'AI 配置已改变' }, 409);
    };
    await state.send('你好');
    assert.equal(state.calls.length, 3, 'Only the failed chat and config reload are added');
    assert.equal(e.chatState.textContent, '失败');
    assert.equal(e.chatCard.className, 'chat-card error');
    assert.equal(e.chatInput.value, '你好');
    state.chat = original;
    await e.chatForm.fire('submit');
    assert.equal(state.calls.length, 4, 'A new explicit submit sends the pending input');
    assert.equal(JSON.parse(state.calls.at(-1).options.body).config_revision, 'r2');
    assert.equal(e.chatCard.className, 'chat-card ready');
});

test('oversized input is rejected locally without silently trimming or sending it', async () => {
    const state = await setup();
    await state.send('中'.repeat(90000));
    assert.equal(state.calls.length, 1);
    assert.match(state.elements.chatStatus.textContent, /256 KB/);
    assert.equal(state.elements.chatInput.value.length, 90000);
});

test('thinking headings are collapsed by default while the answer remains outside', async () => {
    const state = await setup();
    state.answer = 'Thinking Process:\n分析输入。\n\n**Final Response:**\n你好！有什么可以帮助你的？';
    await state.send('你好');
    const history = state.elements.chatHistory;
    const details = state.find(history, node => node.tagName === 'details');
    assert.equal(details.length, 1);
    assert.notEqual(details[0].open, true);
    assert.equal(state.find(details[0], node => node.className === 'message-content')[0].textContent, '分析输入。');
    const body = state.find(history, node => node.className === 'message-body')[1];
    assert.equal(body.children.at(-1).textContent, '你好！有什么可以帮助你的？');
    await state.copyReply();
    assert.ok(state.clipboard.includes(state.answer), 'Copy all retains the complete original message');
    await state.send('继续');
    assert.equal(JSON.parse(state.calls.at(-1).options.body).messages[1].content, state.answer);
});

test('think tags fold safely and fenced code gets its own exact copy button', async () => {
    const state = await setup();
    const code = 'services:\n  web:\n    image: nginx\n    ports: ["4000:3000"]\n';
    state.answer = '<think>仅用于展示的思考文字</think>已修改端口。\n```yaml\n' + code + '```\n请检查配置。';
    await state.send('修改配置');
    const history = state.elements.chatHistory;
    const frames = state.find(history, node => node.className === 'message-code');
    assert.equal(frames.length, 1);
    assert.equal(state.find(frames[0], node => node.tagName === 'code')[0].textContent, code);
    await state.find(frames[0], node => node.tagName === 'button')[0].fire('click');
    assert.equal(state.clipboard, code, 'Copy code excludes fences and explanation, retaining indentation and newline');
    const body = state.find(history, node => node.className === 'message-body')[1];
    assert.equal(body.children[0].tagName, 'details');
    assert.equal(body.children[1].textContent, '已修改端口。');
    assert.equal(body.children.at(-1).textContent, '请检查配置。');
});

test('code fences protect thinking-like text, preserve HTML as code, and allow unfinished fences', async () => {
    const state = await setup();
    const code = '<think>not reasoning</think>\nThinking Process:\n<script>alert(1)</script>\n';
    state.answer = '~~~~html\n' + code + '~~~~\n说明\n```js\nconst x = 1;';
    await state.send('示例');
    const history = state.elements.chatHistory;
    assert.equal(state.find(history, node => node.tagName === 'details').length, 0);
    const codes = state.find(history, node => node.tagName === 'code');
    assert.deepEqual(codes.map(node => node.textContent), [code, 'const x = 1;']);
    await state.find(history, node => node.className === 'message-code-header')[1].children[1].fire('click');
    assert.equal(state.clipboard, 'const x = 1;');
});

test('unclosed thinking remains available on expansion without deleting original content', async () => {
    const state = await setup();
    state.answer = 'Thinking Process:\n内容尚未结束';
    await state.send('开始');
    const details = state.find(state.elements.chatHistory, node => node.tagName === 'details');
    assert.equal(details.length, 1);
    assert.equal(state.find(details[0], node => node.className === 'message-content')[0].textContent, '内容尚未结束');
    await state.copyReply();
    assert.ok(state.clipboard.includes(state.answer));
});

test('missing opening think tag folds the prefix and leaves only the answer outside', async () => {
    const state = await setup();
    const reasoning = 'Okay, the user just said hello.\n\nLet me reply politely.';
    state.answer = reasoning + '\n</think>\n\n你好！有什么可以帮助你的？';
    await state.send('你好');
    const body = state.find(state.elements.chatHistory, node => node.className === 'message-body')[1];
    assert.equal(body.children.length, 2);
    const details = body.children[0];
    assert.equal(details.tagName, 'details');
    assert.notEqual(details.open, true);
    assert.equal(details.children[0].textContent, '思考过程');
    assert.equal(state.find(details, node => node.className === 'message-content')[0].textContent, reasoning);
    assert.equal(body.children[1].textContent, '你好！有什么可以帮助你的？');
    await state.copyReply();
    assert.ok(state.clipboard.includes(state.answer));
});

test('blank lines between thinking and code create no empty display blocks and code copy stays exact', async () => {
    const state = await setup();
    const code = '\nservices:\n  app:\n    ports: ["4000:3000"]\n\n';
    state.answer = '<think>修改端口</think>\n\n \n\n```yaml\n' + code + '```\n\n';
    await state.send('修改端口');
    const body = state.find(state.elements.chatHistory, node => node.className === 'message-body')[1];
    assert.deepEqual(body.children.map(node => node.className), ['message-thinking', 'message-code']);
    await state.find(body, node => node.className === 'message-code-header')[0].children[1].fire('click');
    assert.equal(state.clipboard, code);
});

test('a closing think tag inside a code example or user message never folds ordinary text', async () => {
    const state = await setup();
    state.answer = '这是标签示例。\n```html\n</think>\n```\n普通正文。';
    await state.send('原始输入\n</think>');
    assert.equal(state.find(state.elements.chatHistory, node => node.tagName === 'details').length, 0);
});

test('card border state reflects readiness, generation and cancellation without a visible badge', async () => {
    const state = await setup();
    const e = state.elements;
    assert.equal(e.chatState.textContent, '已就绪');
    assert.equal(e.chatCard.className, 'chat-card ready');
    assert.equal(e.chatStatus.hidden, true);
    let resolve;
    state.chat = () => new Promise(done => { resolve = done; });
    const pending = state.send('测试');
    assert.equal(e.chatState.textContent, '回复中');
    assert.equal(e.chatCard.className, 'chat-card busy');
    await e.stopChatBtn.fire('click');
    assert.equal(e.chatState.textContent, '已就绪');
    resolve(state.response({ detail: 'stopped' }, 502));
    await pending;
    state.config.enabled = false;
    await state.focus();
    assert.equal(e.chatState.textContent, '未就绪');
    assert.equal(e.chatCard.className, 'chat-card');
});

test('reload restores exact saved replies, reasoning, code and warning without calling AI', async () => {
    const state = await setup();
    state.answer = '<think>分析</think>\n```yaml\nservices: {}\n```';
    await state.send('保存代码');
    const restored = await setup(state.store);
    assert.deepEqual(restored.contents(), state.contents());
    assert.equal(restored.calls.length, 1, 'Reload only reads config and history, never generates');
    let warned = false;
    restored.beforeUnload({ preventDefault: () => { warned = true; } });
    assert.equal(warned, false, 'Saved chats do not block leaving the page');
    await restored.send('继续');
    assert.equal(JSON.parse(restored.calls.at(-1).options.body).messages[1].content, state.answer);
});

test('new conversations retain old history and allow switching back without mixing context', async () => {
    const state = await setup();
    await state.send('旧对话');
    const oldId = [...state.store.keys()][0];
    await state.elements.newChatBtn.fire('click');
    await state.send('新对话');
    assert.equal(state.store.size, 2);
    const oldRow = state.elements.conversationList.querySelectorAll('button').find(button => button.textContent === '旧对话');
    assert.equal(oldRow.attributes['aria-current'], 'false');
    await oldRow.fire('click');
    assert.equal(state.elements.conversationList.querySelectorAll('button')[0].attributes['aria-current'], 'true');
    assert.deepEqual(state.contents(), ['旧对话', state.answer]);
    await state.send('接着旧对话');
    const body = JSON.parse(state.calls.at(-1).options.body);
    assert.equal(body.conversation_id, oldId);
    assert.equal(body.messages[0].content, '旧对话');
});

test('submitted input survives failed requests and page reload', async () => {
    const state = await setup();
    await state.send('已完成');
    state.chat = async () => state.response({ detail: 'upstream unavailable' }, 502);
    await state.send('请继续解释');
    const restored = await setup(state.store);
    assert.deepEqual(restored.contents(), ['已完成', state.answer]);
    assert.equal(restored.elements.chatInput.value, '请继续解释');
});

test('stale tab reloads stored context without automatically resending', async () => {
    const state = await setup();
    const other = await setup(state.store);
    await other.send('另一标签页消息');
    state.chat = async () => state.response({ detail: '对话已在其他页面更新' }, 412);
    await state.send('本页消息');
    assert.deepEqual(state.contents(), ['另一标签页消息', other.answer]);
    assert.equal(state.elements.chatInput.value, '本页消息');
    assert.equal(state.calls.filter(call => call.url.endsWith('/chat')).length, 1);
});

test('reload during generation polls saved results without submitting again', async () => {
    const store = new Map([['running', { id: 'running', title: '处理中', messages: [], warnings: [], draft: '待回答问题', busy: true }]]);
    const state = await setup(store);
    assert.equal(state.elements.chatState.textContent, '回复中');
    assert.equal(state.elements.chatCard.className, 'chat-card busy');
    assert.equal(state.elements.sendChatBtn.disabled, true);
    store.get('running').messages = [{ role: 'user', content: '待回答问题' }, { role: 'assistant', content: '完成' }];
    store.get('running').busy = false;
    store.get('running').draft = '';
    await state.poll();
    assert.deepEqual(state.contents(), ['待回答问题', '完成']);
    assert.equal(state.elements.sendChatBtn.disabled, false);
    assert.equal(state.calls.length, 1);
});

async function settingsSetup(failRead = false) {
    const e = Object.fromEntries(['aiConfigForm', 'aiConfigFields', 'aiConfigStatus', 'aiApiKey', 'testAiBtn',
        'saveAiBtn', 'aiEnabled', 'aiBaseUrl', 'aiModel'].map(id => [id, new Element()]));
    const state = { e, calls: [], toasts: [], savedKey: 'existing-secret' };
    const config = { enabled: true, base_url: 'https://ai.example.test/v1', model: 'test-model', has_api_key: true };
    state.save = async body => {
        if (body.api_key) state.savedKey = body.api_key;
        return { ok: true, json: async () => ({ success: true, data: config }) };
    };
    vm.runInNewContext(settingsScript, {
        document: { getElementById: id => e[id] },
        window: { showMessage: (text, type) => state.toasts.push({ text, type }), AppPage: { API_BASE_URL: '/api', apiFetch: async (url, options) => {
            state.calls.push({ url, options });
            if (options?.method === 'PUT') return state.save(JSON.parse(options.body));
            if (failRead) throw new Error('连接失败');
            return { ok: true, json: async () => ({ success: true, data: config, message: '连接成功' }) };
        } } },
    });
    await new Promise(setImmediate);
    return state;
}

test('settings keep saved key when blank and disable header actions during save', async () => {
    const state = await settingsSetup();
    const e = state.e;
    assert.equal(e.aiApiKey.value, '********');
    assert.equal(e.aiConfigStatus.hidden, true);
    const save = state.save;
    let resolve;
    state.save = body => new Promise(done => { resolve = async () => done(await save(body)); });
    const pending = e.aiConfigForm.fire('submit');
    assert.equal(e.aiConfigFields.disabled, false, 'Saving must not make the input fields flash gray');
    assert.equal(e.saveAiBtn.disabled, true);
    assert.equal(e.testAiBtn.disabled, true);
    const body = JSON.parse(state.calls.at(-1).options.body);
    assert.equal(body.api_key, null);
    assert.equal('clear_api_key' in body, false);
    await e.testAiBtn.fire('click');
    assert.equal(state.calls.length, 2);
    await resolve();
    await pending;
    assert.equal(state.savedKey, 'existing-secret');
    assert.equal(e.saveAiBtn.disabled, false);
    assert.equal(e.testAiBtn.disabled, false);
    state.save = save;
    e.aiApiKey.value = 'replacement-secret';
    await e.aiApiKey.fire('input');
    await e.aiConfigForm.fire('input');
    await e.aiConfigForm.fire('submit');
    assert.equal(state.savedKey, 'replacement-secret');
    assert.equal(e.aiApiKey.value, 'replacement-secret', 'Save must not clear the locally entered key');
    assert.equal(e.aiConfigStatus.hidden, true);
    assert.deepEqual(state.toasts.at(-1), { text: 'OpenAI 配置保存成功', type: 'success' });
    await e.testAiBtn.fire('click');
    assert.deepEqual(state.toasts.at(-1), { text: '连接成功', type: 'success' });
    assert.equal(e.aiConfigStatus.hidden, true);
    await e.aiConfigForm.fire('submit');
    assert.equal(JSON.parse(state.calls.at(-1).options.body).api_key, null, 'An unchanged key is never resent, including masks');
    state.save = async () => ({ ok: false, status: 500, json: async () => ({ detail: '保存失败' }) });
    await e.aiConfigForm.fire('submit');
    assert.deepEqual(state.toasts.at(-1), { text: '保存失败', type: 'error' });
    assert.equal(e.aiApiKey.value, 'replacement-secret');
    assert.equal(e.saveAiBtn.disabled, false);
});

test('settings cannot submit after config load failure or test unsaved changes', async () => {
    const failed = await settingsSetup(true);
    assert.equal(failed.e.saveAiBtn.disabled, true);
    assert.equal(failed.e.testAiBtn.disabled, true);
    await failed.e.aiConfigForm.fire('submit');
    assert.equal(failed.calls.length, 1);
    const state = await settingsSetup();
    await state.e.aiConfigForm.fire('input');
    await state.e.testAiBtn.fire('click');
    assert.equal(state.calls.length, 1);
    assert.deepEqual(state.toasts.at(-1), { text: '配置已修改，请先保存再测试', type: 'error' });
    assert.equal(state.e.aiConfigStatus.hidden, true);
});
