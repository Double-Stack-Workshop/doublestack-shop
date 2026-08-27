let term;
let fitAddon;
let ws;
let isConnected = false;

async function loadSidebar() {
    return window.AppPage.loadSidebar('terminal');
}

function initTerminal() {
    term = new Terminal({
        cursorBlink: true,
        cursorStyle: 'block',
        fontSize: 14,
        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
        theme: {
            background: '#1e1e1e',
            foreground: '#d4d4d4',
            cursor: '#ffffff',
            selection: '#264f78',
            black: '#1e1e1e',
            red: '#f44747',
            green: '#6a9955',
            yellow: '#dcdcaa',
            blue: '#569cd6',
            magenta: '#c586c0',
            cyan: '#4ec9b0',
            white: '#d4d4d4',
            brightBlack: '#5a5a5a',
            brightRed: '#f44747',
            brightGreen: '#6a9955',
            brightYellow: '#dcdcaa',
            brightBlue: '#569cd6',
            brightMagenta: '#c586c0',
            brightCyan: '#4ec9b0',
            brightWhite: '#ffffff'
        },
        scrollback: 10000,
        allowProposedApi: true,
        lineHeight: 1.4
    });

    fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon.WebLinksAddon());

    term.open(document.getElementById('terminal'));
    
    setTimeout(() => {
        fitTerminal();
        term.focus();
    }, 100);

    term.writeln('\x1b[1;32m(Double Stack Store Terminal)\x1b[0m');
    term.writeln('\x1b[33m点击 "连接" 按钮连接到宿主主机终端\x1b[0m');
    term.writeln('');

    term.onData(data => {
        if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'input', data: data }));
        }
    });

    term.onResize(({ cols, rows }) => {
        if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'resize', cols: cols, rows: rows }));
        }
    });

    window.addEventListener('resize', () => {
        if (fitAddon && term) {
            fitTerminal();
        }
    });
}

function fitTerminal() {
    const terminalWrapper = document.querySelector('.terminal-wrapper');
    if (!terminalWrapper || !fitAddon || !term) return;
    
    const rect = terminalWrapper.getBoundingClientRect();
    const padding = 0;
    
    terminalWrapper.style.width = `${rect.width}px`;
    terminalWrapper.style.maxWidth = '100%';
    
    fitAddon.fit();
    
    const viewport = document.querySelector('.xterm-viewport');
    if (viewport) {
        viewport.style.overflowX = 'hidden';
        viewport.style.width = '100%';
    }
    
    const terminalEl = document.getElementById('terminal');
    if (terminalEl) {
        terminalEl.style.width = '100%';
        terminalEl.style.overflowX = 'hidden';
    }
}

function updateConnectionStatus(status) {
    const statusEl = document.getElementById('connectionStatus');
    const connectBtn = document.getElementById('connectBtn');
    const disconnectBtn = document.getElementById('disconnectBtn');

    statusEl.className = 'connection-status ' + status;

    if (status === 'connected') {
        statusEl.innerHTML = '<i class="fas fa-circle"></i><span>已连接</span>';
        connectBtn.disabled = true;
        disconnectBtn.disabled = false;
        isConnected = true;
    } else if (status === 'disconnected') {
        statusEl.innerHTML = '<i class="fas fa-circle"></i><span>未连接</span>';
        connectBtn.disabled = false;
        disconnectBtn.disabled = true;
        isConnected = false;
    } else if (status === 'connecting') {
        statusEl.innerHTML = '<i class="fas fa-circle"></i><span>连接中...</span>';
        connectBtn.disabled = true;
        disconnectBtn.disabled = true;
    }
}

function connect() {
    if (isConnected) return;

    updateConnectionStatus('connecting');
    term.clear();
    term.writeln('\x1b[33m正在连接到宿主主机...\x1b[0m');

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/ws/terminal`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        updateConnectionStatus('connected');
        term.writeln('\x1b[32m已连接到宿主主机终端\x1b[0m');
        term.writeln('');
        ws.send(JSON.stringify({
            type: 'resize',
            cols: term.cols,
            rows: term.rows
        }));
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === 'output') {
                term.write(msg.data);
            } else if (msg.type === 'error') {
                term.writeln(`\x1b[31m错误: ${msg.message}\x1b[0m`);
            }
        } catch (e) {
            term.write(event.data);
        }
    };

    ws.onerror = (error) => {
        term.writeln('\x1b[31mWebSocket 连接错误\x1b[0m');
        updateConnectionStatus('disconnected');
    };

    ws.onclose = () => {
        term.writeln('');
        term.writeln('\x1b[33m连接已断开\x1b[0m');
        updateConnectionStatus('disconnected');
    };
}

function disconnect() {
    if (ws) {
        ws.close();
        ws = null;
    }
}

function clearTerminal() {
    if (term) {
        term.clear();
    }
}

function toggleFullscreen() {
    const terminalWrapper = document.querySelector('.terminal-wrapper');
    if (!document.fullscreenElement) {
        terminalWrapper.requestFullscreen().then(() => {
            setTimeout(() => fitAddon.fit(), 100);
        }).catch(err => {
            console.error('全屏失败:', err);
        });
    } else {
        document.exitFullscreen().then(() => {
            setTimeout(() => fitAddon.fit(), 100);
        });
    }
}

function checkLogin() {
    return window.AppPage.requireLogin();
}

function loadUserInfo() {
    window.AppPage.populateUsername();
}

document.addEventListener('DOMContentLoaded', async function() {
    if (!checkLogin()) return;
    
    await loadSidebar();
    loadUserInfo();
    initTerminal();

    document.getElementById('connectBtn').addEventListener('click', connect);
    document.getElementById('disconnectBtn').addEventListener('click', disconnect);
    document.getElementById('clearBtn').addEventListener('click', clearTerminal);
    document.getElementById('fullscreenBtn').addEventListener('click', toggleFullscreen);
});