export default [
    {
        files: ["src/**/*.js"],
        languageOptions: {
            ecmaVersion: "latest",
            sourceType: "module",
            globals: {
                window: "readonly", document: "readonly", localStorage: "readonly",
                fetch: "readonly", EventSource: "readonly", WebSocket: "readonly",
                confirm: "readonly", prompt: "readonly", alert: "readonly",
                setTimeout: "readonly", setInterval: "readonly", clearInterval: "readonly",
                URLSearchParams: "readonly", FormData: "readonly", FileReader: "readonly",
                Terminal: "readonly", FitAddon: "readonly", WebLinksAddon: "readonly",
            },
        },
        rules: {
            "no-dupe-else-if": "error",
            "no-constant-condition": "error",
            "no-debugger": "error",
        },
    },
];
