from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 先初始化数据库，确保表已创建
from .database import init_db
init_db()

# 现在可以安全地导入其他模块
from .routes import router
from .version import VERSION

app = FastAPI(title="双栈商店 API", version=VERSION)

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