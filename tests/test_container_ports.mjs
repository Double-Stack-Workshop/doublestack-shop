import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../frontend/src/pages/container/container.js', import.meta.url), 'utf8');

function loadHelpers() {
    const context = {
        URL,
        console,
        document: {
            addEventListener() {},
            getElementById() { return { addEventListener() {} }; },
        },
        window: { AppPage: { API_BASE_URL: '/api', apiFetch() {} } },
    };
    vm.createContext(context);
    vm.runInContext(`${source}\n;globalThis.portTest = {
        getPortMappings, getAccessLinks,
        setDomain(value) { globalDomain = value; }
    };`, context);
    return context.portTest;
}

test('dual-stack duplicate mappings collapse while multiple ports remain', () => {
    const helpers = loadHelpers();
    const mappings = helpers.getPortMappings({ ports: [
        '0.0.0.0:8080->80/tcp', '[::]:8080->80/tcp',
        '0.0.0.0:8443->443/tcp', '[::]:8443->443/tcp',
    ] });
    assert.deepEqual(Array.from(mappings, item => item.value), ['8080->80/tcp', '8443->443/tcp']);
});

test('global domain creates one access link for every published TCP port', () => {
    const helpers = loadHelpers();
    helpers.setDomain('https://panel.example.com');
    const links = helpers.getAccessLinks({ ports: [
        '8080->80/tcp', '8443->443/tcp', '5353->53/udp',
    ] });
    assert.deepEqual(Array.from(links, item => item.url), [
        'https://panel.example.com:8080', 'https://panel.example.com:8443',
    ]);
});
