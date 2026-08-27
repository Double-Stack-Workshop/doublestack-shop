(function () {
    async function clearAppCache() {
        sessionStorage.clear();
        if ('caches' in window) {
            await Promise.all((await caches.keys()).map((key) => caches.delete(key)));
        }
        // localStorage 保存了当前登录用户与管理员标识，不属于可安全删除的资源缓存。
        // 删除它会让页面在服务端会话仍有效时错误地隐藏管理员菜单。
    }

    async function clearCacheAndReload() {
        await clearAppCache();
        const url = new URL(window.location.href);
        url.searchParams.set('_refresh', Date.now().toString());
        window.location.replace(url.toString());
    }

    window.AppCache = { clearAppCache, clearCacheAndReload };
}());
