from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 先初始化数据库，确保表已创建
from .database import init_db
init_db()

# 现在可以安全地导入其他模块
from .routes import router
from .services import add_repo, get_all_repos

DEFAULT_REPOS = [
    {
        "name": "飞牛容器仓库",
        "repo_url": "https://github.com/Double-Stack-Workshop/Compose-File",
        "branch": "main",
        "local_path": "fnOS"
    },
    {
        "name": "绿联新系统容器仓库",
        "repo_url": "https://github.com/Double-Stack-Workshop/Compose-File",
        "branch": "main",
        "local_path": "UgreenNew"
    },
    {
        "name": "绿联旧系统容器仓库",
        "repo_url": "https://github.com/Double-Stack-Workshop/Compose-File",
        "branch": "main",
        "local_path": "Ugreen（Abandoned）"
    },
    {
        "name": "极空间容器仓库",
        "repo_url": "https://github.com/Double-Stack-Workshop/Compose-File",
        "branch": "main",
        "local_path": "ZSpace"
    }
]

def init_default_repo():
    repos = get_all_repos()
    repo_names = [repo["name"] for repo in repos]
    for repo_config in DEFAULT_REPOS:
        if repo_config["name"] not in repo_names:
            add_repo(
                repo_url=repo_config["repo_url"],
                branch=repo_config["branch"],
                local_path=repo_config["local_path"],
                name=repo_config["name"]
            )

init_default_repo()

app = FastAPI(title="双栈商店 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import os
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