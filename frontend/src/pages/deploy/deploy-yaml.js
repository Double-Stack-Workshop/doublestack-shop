(function () {
    function parseServices(content) {
        if (!window.jsyaml) {
            throw new Error('YAML 解析器尚未加载');
        }
        const document = window.jsyaml.load(content);
        if (!document || typeof document.services !== 'object' || Array.isArray(document.services)) {
            return {};
        }
        return document.services;
    }

    function collectPortInputs(inputs) {
        const groups = new Map();
        Array.from(inputs || []).forEach(input => {
            const service = input.dataset.service || '';
            const index = input.dataset.idx || '0';
            const key = `${service}\u0000${index}`;
            if (!groups.has(key)) {
                groups.set(key, {
                    service,
                    index,
                    original: input.dataset.original || '',
                    host: '',
                    container: '',
                });
            }
            const entry = groups.get(key);
            if (input.dataset.part === 'host') entry.host = input.value.trim();
            if (input.dataset.part === 'container') entry.container = input.value.trim();
        });
        return groups;
    }

    window.DeployYaml = { parseServices, collectPortInputs };
}());
