(function () {
    // 仅解析展示结构，原始消息仍用于复制、上下文和 API 返回。
    function blocks(text) {
        const result = [];
        const lines = text.match(/[^\n]*\n|[^\n]+$/g) || [];
        let plain = '';
        for (let index = 0; index < lines.length; index++) {
            const opening = lines[index].match(/^ {0,3}(`{3,}|~{3,})([^\r\n]*)\r?\n?$/);
            if (!opening) { plain += lines[index]; continue; }
            if (plain) { result.push({ type: 'text', text: plain }); plain = ''; }
            const fence = opening[1];
            const close = new RegExp(`^ {0,3}${fence[0]}{${fence.length},}[ \\t]*\\r?\\n?$`);
            let code = '';
            while (++index < lines.length && !close.test(lines[index])) code += lines[index];
            result.push({ type: 'code', text: code, language: opening[2].trim() || 'text' });
        }
        if (plain) result.push({ type: 'text', text: plain });
        return result;
    }

    function render(container, text, { onCopy, thinking = true }) {
        let target = container;
        let inThinking = false;
        const sections = blocks(text);
        const markers = /<think>|<\/think>|^[ \t]{0,3}(?:#{1,6}[ \t]*)?(?:\*\*|__)?(Thinking Process|思考过程|Final Answer|Final Response|Answer|Response|最终回答|最终回复|最终答案|正式回答|正式回复)[ \t]*[:：]?(?:\*\*|__)?[ \t]*[:：]?[ \t]*(?:\r?\n|$)/gim;
        function plain(value) {
            // 清除结构边界上的空行，避免折叠区和代码框之间出现空白段落。
            const display = value.replace(/^(?:[ \t]*\r?\n)+|(?:\r?\n[ \t]*)+$/g, '');
            if (!display.trim()) return;
            const paragraph = document.createElement('div');
            paragraph.className = 'message-content';
            paragraph.textContent = display;
            target.append(paragraph);
        }
        function startThinking() {
            if (inThinking) return;
            const details = document.createElement('details');
            details.className = 'message-thinking';
            const summary = document.createElement('summary');
            summary.textContent = '思考过程';
            const body = document.createElement('div');
            body.className = 'message-thinking-content';
            details.append(summary, body);
            container.append(details);
            target = body;
            inThinking = true;
        }
        // 部分模型省略开头的 <think>，只返回思考内容和 </think>。
        // 仅当代码框外的第一个结构标记就是结束标签时，将前缀作为思考段。
        if (thinking) {
            for (const section of sections) {
                if (section.type !== 'text') continue;
                const first = section.text.matchAll(markers).next().value;
                if (!first) continue;
                if (first[0].toLowerCase() === '</think>') startThinking();
                break;
            }
        }
        for (const block of sections) {
            if (block.type === 'code') {
                const frame = document.createElement('div');
                frame.className = 'message-code';
                const header = document.createElement('div');
                header.className = 'message-code-header';
                const language = document.createElement('span');
                language.textContent = block.language;
                const copy = document.createElement('button');
                copy.type = 'button';
                copy.className = 'app-button app-button-secondary';
                copy.textContent = '复制代码';
                copy.addEventListener('click', () => onCopy(block.text));
                header.append(language, copy);
                const pre = document.createElement('pre');
                const code = document.createElement('code');
                code.textContent = block.text;
                pre.append(code);
                frame.append(header, pre);
                target.append(frame);
                continue;
            }
            if (!thinking) { plain(block.text); continue; }
            // 只识别明确标记，代码框内的同名文字和标签不会被当作思考过程。
            let offset = 0;
            for (const match of block.text.matchAll(markers)) {
                const start = match[0].toLowerCase() === '<think>' || /^(Thinking Process|思考过程)$/i.test(match[1] || '');
                if (!start && !inThinking) continue;
                plain(block.text.slice(offset, match.index));
                if (start) startThinking();
                else { inThinking = false; target = container; }
                offset = match.index + match[0].length;
            }
            plain(block.text.slice(offset));
        }
    }
    window.AIMessage = { render };
}());
