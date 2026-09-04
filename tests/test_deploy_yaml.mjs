import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../frontend/src/pages/deploy/deploy-yaml.js', import.meta.url), 'utf8');

function input(service, part, value, original, index = '0') {
    return { dataset: { service, part, original, idx: index }, value };
}

test('port fields with the same index stay isolated by Compose service', () => {
    const context = { window: {} };
    vm.createContext(context);
    vm.runInContext(source, context);
    const groups = context.window.DeployYaml.collectPortInputs([
        input('romm', 'host', '8085', '8085:8080'),
        input('romm', 'container', '8080', '8085:8080'),
        input('romm-db', 'host', '3306', '3306:3306'),
        input('romm-db', 'container', '3306', '3306:3306'),
    ]);

    assert.equal(groups.size, 2);
    assert.deepEqual(
        Array.from(groups.values(), ({ service, host, container }) => ({ service, host, container })),
        [
            { service: 'romm', host: '8085', container: '8080' },
            { service: 'romm-db', host: '3306', container: '3306' },
        ],
    );
});
