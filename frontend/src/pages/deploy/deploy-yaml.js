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

    window.DeployYaml = { parseServices };
}());
