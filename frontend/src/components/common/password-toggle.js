(function () {
    document.querySelectorAll('.password-toggle').forEach((button) => {
        const input = document.getElementById(button.getAttribute('aria-controls'));
        if (!input) return;
        const label = input.labels[0]?.textContent || '密码';
        function setVisible(visible) {
            input.type = visible ? 'text' : 'password';
            button.setAttribute('aria-pressed', String(visible));
            button.setAttribute('aria-label', `${visible ? '隐藏' : '显示'}${label}`);
            button.title = visible ? '隐藏密码' : '显示密码';
        }
        button.addEventListener('click', () => {
            setVisible(input.type === 'password');
            input.focus({ preventScroll: true });
        });
        input.form?.addEventListener('reset', () => setVisible(false));
    });
}());
