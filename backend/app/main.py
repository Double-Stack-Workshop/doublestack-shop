import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 先初始化数据库，确保表已创建
from .database import get_user_by_session, init_db
init_db()

# 现在可以安全地导入其他模块
from .routes import router
from .version import VERSION

app = FastAPI(title="双栈商店 API", version=VERSION)
CORS_ORIGINS = [
    origin.strip() for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:8000,http://localhost:8001"
    ).split(",") if origin.strip()
]

_PUBLIC_API_PATHS = {
    "/api/login",
    "/api/register",
    "/api/users/forgot-password",
}
_ADMIN_API_PREFIXES = (
    "/api/users", "/api/repos", "/api/deploy", "/api/containers", "/api/networks",
    "/api/images", "/api/proxy", "/api/current-repo",
    "/api/global-domain", "/api/docker-mirrors", "/api/logs", "/api/backups",
)


@app.middleware("http")
async def require_authenticated_api_user(request: Request, call_next):
    """在路由执行前验证会话，并拦截管理员专属的管理接口。"""
    path = request.url.path
    if path.startswith("/api/") and path not in _PUBLIC_API_PATHS:
        user = get_user_by_session(request.cookies.get("session_token"))
        if not user:
            return JSONResponse(status_code=401, content={"detail": "请先登录"})
        if path.startswith(_ADMIN_API_PREFIXES) and not user["is_admin"]:
            return JSONResponse(status_code=403, content={"detail": "需要管理员权限"})
        request.state.user = user
    response = await call_next(request)
    if request.url.path.startswith("/src/"):
        # 前端脚本与样式随镜像发布，禁止浏览器复用旧副本导致界面与 API 不匹配。
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/src", StaticFiles(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend/src")), name="src")

app.include_router(router)

@app.get("/")
async def root():
    return RedirectResponse(url="/src/pages/login/login.html")

@app.get("/register")
async def register_page():
    return RedirectResponse(url="/src/pages/register/register.html")

@app.get("/forgot")
async def forgot_page():
    return RedirectResponse(url="/src/pages/forgot/forgot.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
