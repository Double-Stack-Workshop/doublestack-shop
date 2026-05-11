from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from .schemas import AddRepoRequest
from .terminal import terminal_manager
from .version import VERSION, DOCKERHUB_REPO, BUILD_DATE
from .services import (
    get_all_repos,
    add_repo,
    get_repo,
    delete_repo,
    sync_repo,
    get_repo_files,
    get_yml_content,
    save_file_content,
    deploy_yml,
    get_running_containers_count,
    get_all_containers,
    get_container_by_id,
    start_container,
    stop_container,
    restart_container,
    remove_container,
    get_container_logs,
    get_latest_dockerhub_version
)
from .database import get_all_deployments, get_deployed_apps_count, get_deployment_success_rate
from .database import (
    get_user_by_username,
    verify_password,
    create_user,
    get_all_users,
    get_user_by_email,
    update_user,
    delete_user,
    verify_admin_password,
    reset_admin_password
)

router = APIRouter(prefix="/api")

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str = None

class UpdatePasswordRequest(BaseModel):
    password: str

@router.post("/login")
async def login(request: LoginRequest):
    user = get_user_by_username(request.username)
    
    if not user:
        return {"success": False, "message": "用户名或密码错误"}
    
    if verify_password(request.password, user['password']):
        return {
            "success": True,
            "message": "登录成功",
            "data": {
                "username": user['username'],
                "email": user['email'],
                "is_admin": user['is_admin'],
                "created_at": user['created_at']
            }
        }
    
    return {"success": False, "message": "用户名或密码错误"}

class RegisterRequest(BaseModel):
    username: str
    password: str
    admin_password: str

@router.post("/register")
async def register(request: RegisterRequest):
    if not verify_admin_password(request.admin_password):
        return {"success": False, "message": "管理员密码不正确"}
    
    if create_user(request.username, request.password, None, is_admin=False):
        return {"success": True, "message": "注册成功"}
    return {"success": False, "message": "用户名已存在"}

class ForgotPasswordRequest(BaseModel):
    admin_password: str
    new_password: str

@router.post("/users/forgot-password")
async def forgot_password(request: ForgotPasswordRequest):
    if not verify_admin_password(request.admin_password):
        return {"success": False, "message": "管理员密码不正确"}
    
    if len(request.new_password) < 6:
        return {"success": False, "message": "密码长度至少为6位"}
    
    if reset_admin_password(request.new_password):
        return {"success": True, "message": "密码重置成功，请使用新密码登录"}
    
    return {"success": False, "message": "密码重置失败"}

@router.get("/users")
async def list_users():
    users = get_all_users()
    return [{
        "id": user['id'],
        "username": user['username'],
        "email": user['email'],
        "is_admin": user['is_admin'],
        "created_at": user['created_at']
    } for user in users]

@router.get("/users/{username}")
async def get_user(username: str):
    user = get_user_by_username(username)
    if user:
        return {
            "id": user['id'],
            "username": user['username'],
            "email": user['email'],
            "is_admin": user['is_admin'],
            "created_at": user['created_at']
        }
    raise HTTPException(status_code=404, detail="用户不存在")

@router.put("/users/{username}")
async def update_user_endpoint(username: str, request: UpdatePasswordRequest):
    if update_user(username, request.password, None):
        return {"success": True, "message": "更新成功"}
    raise HTTPException(status_code=404, detail="用户不存在")

@router.delete("/users/{username}")
async def delete_user_endpoint(username: str):
    if delete_user(username):
        return {"success": True, "message": "删除成功"}
    raise HTTPException(status_code=404, detail="用户不存在")

@router.get("/repos")
async def list_repos():
    return get_all_repos()

@router.post("/repos")
async def create_repo(request: AddRepoRequest):
    result = add_repo(request.repo_url, request.branch, request.local_path, request.name)
    if result["success"]:
        return result
    else:
        return result

@router.get("/repos/{repo_name}")
async def read_repo(repo_name: str):
    repo = get_repo(repo_name)
    if repo:
        return repo
    raise HTTPException(status_code=404, detail="仓库不存在")

@router.post("/repos/{repo_name}/sync")
async def sync_repo_endpoint(repo_name: str):
    result = sync_repo(repo_name)
    if result["success"]:
        return result
    elif result["status"] == "error":
        return result
    raise HTTPException(status_code=404, detail="仓库不存在")

@router.get("/repos/{repo_name}/files")
async def list_repo_files(repo_name: str):
    files = get_repo_files(repo_name)
    if files is not None:
        return files
    raise HTTPException(status_code=404, detail="仓库不存在")

@router.get("/repos/{repo_name}/files/{file_name}")
async def read_file_content(repo_name: str, file_name: str):
    yml_content = get_yml_content(repo_name, file_name)
    if yml_content:
        return yml_content
    raise HTTPException(status_code=404, detail="文件不存在")

class SaveFileRequest(BaseModel):
    content: str

@router.put("/repos/{repo_name}/files/{file_name}")
async def update_file_content(repo_name: str, file_name: str, request: SaveFileRequest):
    if save_file_content(repo_name, file_name, request.content):
        return {"success": True, "message": "文件保存成功"}
    raise HTTPException(status_code=404, detail="文件不存在")

@router.delete("/repos/{repo_name}")
async def remove_repo(repo_name: str):
    if delete_repo(repo_name):
        return {"success": True, "message": "仓库已删除"}
    raise HTTPException(status_code=404, detail="仓库不存在")

class DeployRequest(BaseModel):
    repo_name: str
    file_name: str

@router.post("/deploy")
async def deploy_application(request: DeployRequest):
    result = deploy_yml(request.repo_name, request.file_name)
    if result:
        return result
    raise HTTPException(status_code=404, detail="仓库或文件不存在")

@router.post("/repos/{repo_name}/deploy/{file_path:path}")
async def deploy_yml_endpoint(repo_name: str, file_path: str):
    result = deploy_yml(repo_name, file_path)
    if result:
        return result
    raise HTTPException(status_code=404, detail="仓库或文件不存在")

@router.get("/containers/count")
async def get_containers_count():
    count = get_running_containers_count()
    return {"count": count}

@router.get("/deployments")
async def list_deployments(limit: int = 10):
    deployments = get_all_deployments(limit)
    return deployments

@router.get("/deployments/count")
async def get_deployed_apps_count_api():
    count = get_deployed_apps_count()
    return {"count": count}

@router.get("/deployments/success-rate")
async def get_deployment_success_rate_api():
    rate = get_deployment_success_rate()
    return {"rate": rate}

@router.get("/containers")
async def list_containers():
    containers = get_all_containers()
    return containers

@router.get("/containers/{container_id}")
async def get_container(container_id: str):
    container = get_container_by_id(container_id)
    if container:
        return container
    raise HTTPException(status_code=404, detail="容器不存在")

@router.post("/containers/{container_id}/start")
async def start_container_endpoint(container_id: str):
    if start_container(container_id):
        return {"success": True, "message": "容器启动成功"}
    raise HTTPException(status_code=500, detail="容器启动失败")

@router.post("/containers/{container_id}/stop")
async def stop_container_endpoint(container_id: str):
    if stop_container(container_id):
        return {"success": True, "message": "容器停止成功"}
    raise HTTPException(status_code=500, detail="容器停止失败")

@router.post("/containers/{container_id}/restart")
async def restart_container_endpoint(container_id: str):
    if restart_container(container_id):
        return {"success": True, "message": "容器重启成功"}
    raise HTTPException(status_code=500, detail="容器重启失败")

@router.delete("/containers/{container_id}")
async def remove_container_endpoint(container_id: str, force: bool = False):
    if remove_container(container_id, force):
        return {"success": True, "message": "容器删除成功"}
    raise HTTPException(status_code=500, detail="容器删除失败")

@router.get("/containers/{container_id}/logs")
async def get_container_logs_endpoint(container_id: str, tail: int = 100):
    logs = get_container_logs(container_id, tail)
    return {"logs": logs}

@router.get("/system/version")
async def get_system_version():
    return {"current_version": VERSION, "build_date": BUILD_DATE}

@router.get("/system/check-update")
async def check_for_updates():
    latest_version = get_latest_dockerhub_version(DOCKERHUB_REPO)
    
    if not latest_version:
        return {
            "success": False,
            "message": "无法连接到Docker Hub",
            "current_version": VERSION,
            "latest_version": None
        }
    
    is_update_available = latest_version > VERSION
    
    if is_update_available:
        generate_update_script(latest_version)
    
    return {
        "success": True,
        "current_version": VERSION,
        "latest_version": latest_version,
        "update_available": is_update_available
    }

def generate_update_script(latest_version):
    import os
    
    script_dir = "/app/scripts"
    script_path = os.path.join(script_dir, "update_app.sh")
    
    os.makedirs(script_dir, exist_ok=True)
    
    script_content = f"""#!/bin/bash
# Double Stack Store 更新脚本
# 目标版本: {latest_version}
# 当前版本: {VERSION}

set -e

echo "======================================"
echo "  Double Stack Store 更新脚本"
echo "  当前版本: {VERSION}"
echo "  目标版本: {latest_version}"
echo "======================================"

# 1. 获取当前容器信息
echo ""
echo "[1/5] 正在获取当前容器配置..."
CONTAINER_NAME=$(docker ps --filter "name=double-stack-app" --format "{{.Names}}")
IMAGE_NAME=$(docker inspect "$CONTAINER_NAME" --format "{{.Config.Image}}")
NETWORK_MODE=$(docker inspect "$CONTAINER_NAME" --format "{{.HostConfig.NetworkMode}}")

# 获取端口映射
PORT_MAP=$(docker inspect "$CONTAINER_NAME" --format "{{range .HostConfig.PortBindings}}{{.}}{{end}}" | tr -d '[\\]"')

# 获取挂载目录
VOLUMES=$(docker inspect "$CONTAINER_NAME" --format "{{range .Mounts}}-v {{.Source}}:{{.Destination}}{{end}}")

echo "  容器名称: $CONTAINER_NAME"
echo "  当前镜像: $IMAGE_NAME"
echo "  网络模式: $NETWORK_MODE"

# 2. 拉取最新镜像
echo ""
echo "[2/5] 正在拉取最新镜像..."
docker pull {DOCKERHUB_REPO}:{latest_version}

# 3. 停止当前容器
echo ""
echo "[3/5] 正在停止当前容器..."
docker stop "$CONTAINER_NAME"

# 4. 删除旧容器
echo ""
echo "[4/6] 正在删除旧容器..."
docker rm "$CONTAINER_NAME"

# 5. 启动新容器
echo ""
echo "[5/6] 正在启动新版本容器..."
docker run -d \\
  --name "$CONTAINER_NAME" \\
  --network "$NETWORK_MODE" \\
  $VOLUMES \\
  --privileged \\
  $PORT_MAP \\
  {DOCKERHUB_REPO}:{latest_version}

# 6. 清理旧镜像
echo ""
echo "[6/6] 正在清理旧镜像..."
OLD_IMAGE=$(docker images --filter "dangling=true" --format "{{.ID}}")
if [ -n "$OLD_IMAGE" ]; then
    docker rmi "$OLD_IMAGE"
fi

echo ""
echo "======================================"
echo "  更新完成！"
echo "  新版本: {latest_version}"
echo "======================================"
"""
    
    with open(script_path, "w") as f:
        f.write(script_content)
    
    os.chmod(script_path, 0o755)
    print(f"更新脚本已生成: {script_path}")

@router.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    await websocket.accept()

    cols = 80
    rows = 24

    try:
        while True:
            msg = await websocket.receive_json()
            if msg.get('type') == 'resize':
                cols = msg.get('cols', 80)
                rows = msg.get('rows', 24)
                break
    except:
        pass

    try:
        await terminal_manager.create_host_terminal(websocket, cols, rows)
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass
        await websocket.close()