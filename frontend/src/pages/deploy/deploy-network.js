(function () {
    function render(networks, escapeHtml) {
        if (!networks.length) return '<tr><td colspan="3" class="empty-cell">暂无网络</td></tr>';
        return networks.map((network) => {
            const driver = (network.driver || '').toLowerCase();
            return `
            <tr>
                <td><code class="network-name">${escapeHtml(network.name || '')}</code></td>
                <td><span class="driver-tag ${driver}">${escapeHtml(network.driver || '')}</span></td>
                <td>${escapeHtml(network.scope || '')}</td>
            </tr>`;
        }).join('');
    }

    window.DeployNetwork = { render };
}());
