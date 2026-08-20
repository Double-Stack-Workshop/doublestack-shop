import asyncio
import json
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
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
    get_latest_dockerhub_version,
    get_all_images,
    delete_image,
    search_dockerhub_images,
    pull_image,
    test_all_connectivity,
    get_proxy_config,
    set_proxy_config,
    create_container_backup,
    get_all_backups_list,
    get_backups_for_container,
    remove_backup,
    restore_backup,
    get_backup_by_id,
    get_docker_info,
    get_host_system_info,
    get_current_repo,
    set_current_repo,
    init_default_repos,
    load_recommend_config
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
    reset_admin_password,
    get_setting,
    set_setting
)
from .logger import log_service

router = APIRouter(prefix="/api")


def _sse_pack(event: dict) -> str:
    """把事件 dict 打包成 SSE 文本帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _sse_stream(gen):
    """把 yield dict 的生成器包成 SSE 文本流。"""
    for event in gen:
        yield _sse_pack(event)


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


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
        log_service.warning(f"用户登录失败: {request.username} - 用户不存在", 'auth')
        return {"success": False, "message": "用户名或密码错误"}
    
    if verify_password(request.password, user['password']):
        log_service.success(f"用户登录成功: {request.username}", 'auth')
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
    
    log_service.warning(f"用户登录失败: {request.username} - 密码错误", 'auth')
    return {"success": False, "message": "用户名或密码错误"}

class RegisterRequest(BaseModel):
    username: str
    password: str
    admin_password: str

@router.post("/register")
async def register(request: RegisterRequest):
    if not verify_admin_password(request.admin_password):
        log_service.warning(f"用户注册失败: {request.username} - 管理员密码错误", 'auth')
        return {"success": False, "message": "管理员密码不正确"}
    
    if create_user(request.username, request.password, None, is_admin=False):
        log_service.success(f"用户注册成功: {request.username}", 'auth')
        return {"success": True, "message": "注册成功"}
    
    log_service.warning(f"用户注册失败: {request.username} - 用户名已存在", 'auth')
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
    log_service.info("获取用户列表", 'query')
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
    log_service.info("获取仓库列表", 'query')
    return get_all_repos()

@router.post("/repos/init-default")
async def init_default_repos_route():
    await asyncio.to_thread(init_default_repos)
    log_service.info("初始化默认仓库", 'system')
    repos = get_all_repos()
    return {"success": True, "message": f"已初始化 {len(repos)} 个仓库", "data": repos}

@router.post("/repos")
async def create_repo(request: AddRepoRequest):
    result = await asyncio.to_thread(add_repo, request.repo_url, request.branch, request.local_path, request.name)
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
    result = await asyncio.to_thread(sync_repo, repo_name)
    if result["success"]:
        return result
    elif result["status"] == "error":
        return result
    raise HTTPException(status_code=404, detail="仓库不存在")

@router.get("/repos/{repo_name}/files")
async def list_repo_files(repo_name: str):
    files = get_repo_files(repo_name)
    if files is not None:
        log_service.info(f"获取仓库文件列表: {repo_name}", 'file')
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
        log_service.success(f"文件保存成功: {repo_name}/{file_name}", 'file')
        return {"success": True, "message": "文件保存成功"}
    log_service.warning(f"文件保存失败: {repo_name}/{file_name} - 文件不存在", 'file')
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
    """流式部署，返回 SSE 事件流。"""
    return StreamingResponse(
        _sse_stream(deploy_yml(request.repo_name, request.file_name)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )

@router.post("/repos/{repo_name}/deploy/{file_path:path}")
async def deploy_yml_endpoint(repo_name: str, file_path: str):
    """流式部署，返回 SSE 事件流。"""
    return StreamingResponse(
        _sse_stream(deploy_yml(repo_name, file_path)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )

@router.get("/containers/count")
async def get_containers_count():
    log_service.info("获取容器数量", 'query')
    count = get_running_containers_count()
    return {"count": count}

@router.get("/deployments")
async def list_deployments(limit: int = 10):
    log_service.info(f"获取部署列表 (限制: {limit})", 'query')
    deployments = get_all_deployments(limit)
    return deployments

@router.get("/deployments/count")
async def get_deployed_apps_count_api():
    log_service.info("获取部署数量", 'query')
    count = get_deployed_apps_count()
    return {"count": count}

@router.get("/deployments/success-rate")
async def get_deployment_success_rate_api():
    log_service.info("获取部署成功率", 'query')
    rate = get_deployment_success_rate()
    return {"rate": rate}

@router.get("/containers")
async def list_containers():
    log_service.info("获取容器列表", 'query')
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
    log_service.info("获取系统版本信息", 'system')
    return {"current_version": VERSION, "build_date": BUILD_DATE}

@router.get("/system/docker-info")
async def get_docker_info_route():
    log_service.info("获取 Docker 版本信息", 'system')
    docker_info = get_docker_info()
    return {"success": True, "data": docker_info}

@router.get("/system/host-info")
async def get_host_info_route():
    log_service.info("获取宿主机系统信息", 'system')
    host_info = get_host_system_info()
    return {"success": True, "data": host_info}

@router.get("/system/check-update")
async def check_for_updates():
    log_service.info("检查系统更新", 'system')
    latest_version = get_latest_dockerhub_version(DOCKERHUB_REPO)
    
    if not latest_version:
        log_service.warning("无法连接到Docker Hub", 'system')
        return {
            "success": False,
            "message": "无法连接到Docker Hub",
            "current_version": VERSION,
            "latest_version": None
        }
    
    is_update_available = latest_version > VERSION
    
    if is_update_available:
        log_service.info(f"发现新版本: {latest_version}", 'system')
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
echo "[1/6] 正在获取当前容器配置..."
CONTAINER_NAME=$(docker ps --filter "name=doublestack-shop" --format "{{{{.Names}}}}")

if [ -z "$CONTAINER_NAME" ]; then
    echo "错误：未找到运行中的 doublestack-shop 容器！"
    exit 1
fi

IMAGE_NAME=$(docker inspect "$CONTAINER_NAME" --format "{{{{.Config.Image}}}}")
NETWORK_MODE=$(docker inspect "$CONTAINER_NAME" --format "{{{{.HostConfig.NetworkMode}}}}")

PORT_MAPPING=$(docker port "$CONTAINER_NAME" 2>/dev/null | head -1)
if [ -n "$PORT_MAPPING" ]; then
    CONTAINER_PORT=$(echo "$PORT_MAPPING" | awk -F '->' '{{print $1}}' | cut -d'/' -f1)
    HOST_PORT=$(echo "$PORT_MAPPING" | awk -F '->' '{{print $2}}' | sed 's/^[ \t]*//;s/[ \t]*$//')
    PORT_PARAM="-p $HOST_PORT:$CONTAINER_PORT"
else
    PORT_PARAM=""
fi

VOLUMES=$(docker inspect "$CONTAINER_NAME" --format "{{{{range .Mounts}}}} -v {{{{.Source}}}}:{{{{.Destination}}}} {{{{end}}}}")

echo "  容器名称: $CONTAINER_NAME"
echo "  当前镜像: $IMAGE_NAME"
echo "  网络模式: $NETWORK_MODE"
echo "  端口映射: $PORT_PARAM"

# 2. 拉取最新镜像
echo ""
echo "[2/6] 正在拉取最新镜像..."
docker pull {DOCKERHUB_REPO}:{latest_version}

# 3. 停止当前容器
echo ""
echo "[3/6] 正在停止当前容器..."
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
  $PORT_PARAM \\
  {DOCKERHUB_REPO}:{latest_version}

# 6. 清理旧镜像
echo ""
echo "[6/6] 正在清理旧镜像..."
if [ -n "$IMAGE_NAME" ]; then
    docker rmi "$IMAGE_NAME" > /dev/null 2>&1
    echo "  已删除旧镜像: $IMAGE_NAME"
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

@router.get("/images")
async def list_images():
    log_service.info("获取镜像列表", 'query')
    images = get_all_images()
    return images

class PullImageRequest(BaseModel):
    image_name: str

@router.post("/images/pull")
async def pull_image_endpoint(request: PullImageRequest):
    """流式拉取镜像，返回 SSE 事件流。"""
    return StreamingResponse(
        _sse_stream(pull_image(request.image_name)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )

@router.delete("/images/{image_id}")
async def delete_image_endpoint(image_id: str):
    result = delete_image(image_id)
    if result["success"]:
        return result
    raise HTTPException(status_code=500, detail=result["message"])

@router.get("/images/search")
async def search_images(query: str):
    results = search_dockerhub_images(query)
    return results

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

@router.get("/connectivity")
async def check_connectivity():
    """测试网络连通性"""
    log_service.info("测试网络连通性", 'system')
    result = test_all_connectivity()
    return result

@router.get("/proxy")
async def get_proxy():
    """获取当前代理配置"""
    result = get_proxy_config()
    return {"success": True, "data": result}

class ProxyRequest(BaseModel):
    http_proxy: str = ""
    https_proxy: str = ""

@router.put("/proxy")
async def update_proxy(request: ProxyRequest):
    """更新代理配置"""
    result = set_proxy_config(request.http_proxy, request.https_proxy)
    return result

# 当前系统仓库相关路由
@router.get("/current-repo")
async def get_current_repo_route():
    """获取当前系统仓库"""
    repo_name = get_current_repo()
    return {"success": True, "data": {"repo_name": repo_name}}

class CurrentRepoRequest(BaseModel):
    repo_name: str = ""

@router.put("/current-repo")
async def set_current_repo_route(request: CurrentRepoRequest):
    """设置当前系统仓库"""
    set_current_repo(request.repo_name)
    log_service.info(f"当前系统仓库已设置为: {request.repo_name}", 'system')
    return {"success": True, "message": f"当前系统仓库已设置为: {request.repo_name}"}

# 容器推荐配置路由
@router.get("/recommend-config")
async def get_recommend_config_route():
    """获取容器推荐配置（从 data/recommend.json 读取）"""
    config = load_recommend_config()
    return {"success": True, "data": config}

# 全局域名/IP 设置相关路由
@router.get("/global-domain")
async def get_global_domain():
    """获取全局域名/IP配置"""
    domain = get_setting("global_domain", "")
    return {"success": True, "data": {"global_domain": domain}}

class GlobalDomainRequest(BaseModel):
    global_domain: str = ""

@router.put("/global-domain")
async def update_global_domain(request: GlobalDomainRequest):
    """更新全局域名/IP配置"""
    set_setting("global_domain", request.global_domain)
    log_service.info(f"全局域名/IP已更新: {request.global_domain}", 'system')
    return {"success": True, "message": "配置已保存"}

# Docker 加速源相关路由
import json
from pathlib import Path

DAEMON_JSON_PATH = Path("/etc/docker/daemon.json")

@router.get("/docker-mirrors")
async def get_docker_mirrors():
    """获取 Docker 加速源配置"""
    try:
        if DAEMON_JSON_PATH.exists():
            with open(DAEMON_JSON_PATH, 'r') as f:
                config = json.load(f)
                mirrors = config.get("registry-mirrors", [])
                log_service.info("获取 Docker 加速源配置", 'system')
                return {"success": True, "mirrors": mirrors}
        else:
            return {"success": True, "mirrors": []}
    except Exception as e:
        log_service.error(f"获取 Docker 加速源配置失败: {str(e)}", 'system')
        return {"success": False, "message": f"读取配置失败: {str(e)}", "mirrors": []}

class DockerMirrorsRequest(BaseModel):
    mirrors: list[str]

@router.put("/docker-mirrors")
async def update_docker_mirrors(request: DockerMirrorsRequest):
    """更新 Docker 加速源配置"""
    try:
        # 读取现有配置
        config = {}
        if DAEMON_JSON_PATH.exists():
            with open(DAEMON_JSON_PATH, 'r') as f:
                config = json.load(f)
        
        # 更新加速源
        config["registry-mirrors"] = request.mirrors
        
        # 写回配置文件
        with open(DAEMON_JSON_PATH, 'w') as f:
            json.dump(config, f, indent=2)
        
        log_service.success(f"更新 Docker 加速源配置: {len(request.mirrors)} 个加速源", 'system')
        return {
            "success": True, 
            "message": "配置已保存，需重启 Docker 服务生效",
            "mirrors": request.mirrors
        }
    except PermissionError:
        log_service.error("更新 Docker 加速源配置失败: 权限不足", 'system')
        return {"success": False, "message": "权限不足，请确保容器以正确权限运行"}
    except Exception as e:
        log_service.error(f"更新 Docker 加速源配置失败: {str(e)}", 'system')
        return {"success": False, "message": f"保存配置失败: {str(e)}"}

# 日志相关路由
@router.get("/logs")
async def get_logs(level: str = None, type: str = None):
    """获取操作日志"""
    from .logger import log_service
    logs = log_service.get_logs(level, type)
    return {"logs": logs}

@router.delete("/logs")
async def clear_logs():
    """清空所有日志"""
    from .logger import log_service
    log_service.clear_logs()
    return {"success": True, "message": "日志已清空"}

# ============ 容器备份相关路由 ============

class CreateBackupRequest(BaseModel):
    container_id: str

@router.post("/containers/{container_id}/backup")
async def create_backup_endpoint(container_id: str):
    """创建容器备份"""
    result = create_container_backup(container_id)
    if result["success"]:
        return result
    else:
        return result

@router.get("/backups")
async def list_backups():
    """获取所有备份列表"""
    backups = get_all_backups_list()
    return backups

@router.get("/backups/{backup_id}")
async def get_backup_detail(backup_id: int):
    """获取单个备份详情"""
    backup = get_backup_by_id(backup_id)
    if backup:
        return backup
    raise HTTPException(status_code=404, detail="备份不存在")

@router.get("/backups/container/{container_name}")
async def list_backups_by_container(container_name: str):
    """获取指定容器的备份列表"""
    backups = get_backups_for_container(container_name)
    return backups

@router.delete("/backups/{backup_id}")
async def delete_backup_endpoint(backup_id: int):
    """删除备份"""
    result = remove_backup(backup_id)
    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=404, detail=result["message"])

@router.post("/backups/{backup_id}/restore")
async def restore_backup_endpoint(backup_id: int):
    """恢复备份"""
    result = restore_backup(backup_id)
    if result["success"]:
        return result
    else:
        return result

@router.get("/backups/{backup_id}/download")
async def download_backup_endpoint(backup_id: int):
    """下载备份文件"""
    from fastapi.responses import FileResponse
    import os
    
    backup = get_backup_by_id(backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="备份不存在")
    
    file_path = backup.get('file_path', '')
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="备份文件不存在")
    
    filename = os.path.basename(file_path)
    return FileResponse(path=file_path, filename=filename, media_type='application/x-tar')

from fastapi import UploadFile

@router.post("/backups/restore-file")
async def restore_backup_from_file_endpoint(file: UploadFile):
    """从上传的.tar文件恢复备份"""
    import os
    import shutil
    import datetime
    import subprocess
    from pathlib import Path
    
    if not file.filename.endswith('.tar'):
        raise HTTPException(status_code=400, detail="请上传 .tar 格式的备份文件")
    
    temp_dir = Path("/app/backup") / f"restore-upload-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        temp_file = temp_dir / file.filename
        with open(temp_file, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        restore_dir = temp_dir / "extracted"
        restore_dir.mkdir(exist_ok=True)
        
        result = subprocess.run(
            ["tar", "-xf", str(temp_file), "-C", str(restore_dir)],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode != 0:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"解压备份失败: {result.stderr}")
        
        image_path = None
        config_path = None
        volume_files = []
        bind_files = []
        
        for root, dirs, files in os.walk(restore_dir):
            if 'image.tar' in files:
                image_path = Path(root) / 'image.tar'
            if 'container-config.json' in files:
                config_path = Path(root) / 'container-config.json'
            for f in files:
                if f.startswith('volume-') and f.endswith('.tar.gz'):
                    volume_files.append(Path(root) / f)
                if f.startswith('bind-') and f.endswith('.tar.gz'):
                    bind_files.append(Path(root) / f)
            if image_path and config_path:
                break
        
        if not image_path and not config_path:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="备份文件中未找到image.tar或container-config.json")
        
        log_service.info(f"恢复备份: image存在={image_path is not None}, config存在={config_path is not None}, volume数量={len(volume_files)}, bind数量={len(bind_files)}", 'backup')
        
        if image_path and image_path.exists():
            result = subprocess.run(
                ["docker", "load", "-i", str(image_path)],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise HTTPException(status_code=500, detail=f"加载镜像失败: {result.stderr}")
            log_service.info(f"镜像加载成功: {result.stdout[:100]}...", 'backup')
        else:
            log_service.warning("备份文件中未找到image.tar", 'backup')
        
        if config_path and config_path.exists():
            import json
            with open(config_path, "r") as f:
                config_data = json.load(f)
            
            config = config_data[0] if isinstance(config_data, list) else config_data
            
            container_name = config.get('Name', '').lstrip('/')
            log_service.info(f"容器名称: {container_name}", 'backup')
            
            if container_name:
                mounts = config.get('Mounts', [])
                volume_params = []
                for mount in mounts:
                    mount_type = mount.get('Type', '')
                    destination = mount.get('Destination', '')
                    if mount_type == 'volume':
                        volume_name = mount.get('Name', '')
                        if volume_name and destination:
                            volume_params.append("-v")
                            volume_params.append(f"{volume_name}:{destination}")
                    elif mount_type == 'bind':
                        source = mount.get('Source', '')
                        if source and destination:
                            volume_params.append("-v")
                            volume_params.append(f"{source}:{destination}")
                
                env_params = []
                env_list = config.get('Config', {}).get('Env', [])
                for env in env_list:
                    env_params.append("-e")
                    env_params.append(env)
                
                network_mode = config.get('HostConfig', {}).get('NetworkMode', 'bridge')
                
                port_bindings = config.get('HostConfig', {}).get('PortBindings', {})
                port_params = []
                for container_port, host_bindings in port_bindings.items():
                    if host_bindings:
                        host_port = host_bindings[0].get('HostPort', '')
                        if host_port:
                            port_params.append("-p")
                            port_params.append(f"{host_port}:{container_port}")
                
                image_name = config.get('Config', {}).get('Image', '')
                log_service.info(f"镜像名称: {image_name}", 'backup')
                
                if image_name:
                    existing_containers = subprocess.run(
                        ["docker", "ps", "-a", "--filter", f"name=^{container_name}$", "--format", "{{.Names}}"],
                        capture_output=True,
                        text=True
                    )
                    
                    if existing_containers.stdout.strip():
                        log_service.info(f"停止并删除现有容器: {container_name}", 'backup')
                        subprocess.run(["docker", "stop", container_name], capture_output=True)
                        subprocess.run(["docker", "rm", container_name], capture_output=True)
                    
                    for vol_file in volume_files:
                        volume_name = vol_file.name.replace('volume-', '').replace('.tar.gz', '')
                        log_service.info(f"恢复命名卷: {volume_name}", 'backup')
                        
                        result = subprocess.run(
                            ["docker", "volume", "rm", volume_name],
                            capture_output=True,
                            text=True,
                            timeout=30
                        )
                        
                        result = subprocess.run(
                            ["docker", "volume", "create", volume_name],
                            capture_output=True,
                            text=True,
                            timeout=30
                        )
                        if result.returncode == 0:
                            backup_dir = str(vol_file.parent)
                            backup_filename = vol_file.name
                            result = subprocess.run(
                                ["docker", "run", "--rm", "-v", f"{volume_name}:/volume", "-v", f"{backup_dir}:/backup", "alpine", "sh", "-c", f"tar -xzf /backup/{backup_filename} -C /volume"],
                                capture_output=True,
                                text=True,
                                timeout=300
                            )
                            if result.returncode != 0:
                                log_service.warning(f"恢复命名卷失败: {volume_name} - {result.stderr}", 'backup')
                            else:
                                log_service.info(f"命名卷恢复成功: {volume_name}", 'backup')
                        else:
                            log_service.warning(f"创建命名卷失败: {volume_name}", 'backup')
                    
                    for bind_file in bind_files:
                        bind_name = bind_file.name.replace('bind-', '').replace('.tar.gz', '')
                        mount_info = None
                        for mount in mounts:
                            if mount.get('Type') == 'bind':
                                source = mount.get('Source', '')
                                if source and bind_name == os.path.basename(source):
                                    mount_info = mount
                                    break
                        
                        if mount_info:
                            dest_path = mount_info.get('Source', '')
                            host_dest_path = Path("/host") / dest_path.lstrip('/')
                            host_dest_dir = host_dest_path.parent
                            
                            if host_dest_path.exists() and host_dest_path.is_dir():
                                shutil.rmtree(str(host_dest_path), ignore_errors=True)
                            
                            if host_dest_dir and not host_dest_dir.exists():
                                host_dest_dir.mkdir(parents=True, exist_ok=True)
                            
                            result = subprocess.run(
                                ["tar", "-xzf", str(bind_file), "-C", str(host_dest_dir)],
                                capture_output=True,
                                text=True,
                                timeout=300
                            )
                            if result.returncode != 0:
                                log_service.warning(f"恢复绑定挂载失败: {bind_name} - {result.stderr}", 'backup')
                            else:
                                log_service.info(f"绑定挂载恢复成功: {bind_name}", 'backup')
                        else:
                            log_service.warning(f"未找到绑定挂载配置: {bind_name}", 'backup')
                    
                    run_command = [
                        "docker", "run", "-d",
                        "--name", container_name,
                        "--network", network_mode
                    ] + volume_params + env_params + port_params + [image_name]
                    
                    log_service.info(f"执行命令: {' '.join(run_command)}", 'backup')
                    
                    result = subprocess.run(
                        run_command,
                        capture_output=True,
                        text=True,
                        timeout=300
                    )
                    
                    if result.returncode != 0:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        raise HTTPException(status_code=500, detail=f"启动容器失败: {result.stderr}")
                    log_service.success(f"容器启动成功: {container_name}", 'backup')
                else:
                    log_service.warning("配置文件中未找到镜像名称", 'backup')
            else:
                log_service.warning("配置文件中未找到容器名称", 'backup')
        else:
            log_service.warning("备份文件中未找到container-config.json", 'backup')
        
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        return {"success": True, "message": "备份恢复成功"}
    
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        log_service.error(f"恢复备份失败: {str(e)}", 'backup')
        raise HTTPException(status_code=500, detail=f"恢复备份失败: {str(e)}")