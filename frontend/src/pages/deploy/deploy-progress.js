(function () {
    async function consume(response, onEvent) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let index;
            while ((index = buffer.indexOf('\n\n')) >= 0) {
                const raw = buffer.slice(0, index);
                buffer = buffer.slice(index + 2);
                if (!raw.startsWith('data:')) continue;
                try {
                    onEvent(JSON.parse(raw.slice(5).trim()));
                } catch (error) {
                    console.warn('忽略无法解析的部署事件', error);
                }
            }
        }
    }

    window.DeployProgress = { consume };
}());
