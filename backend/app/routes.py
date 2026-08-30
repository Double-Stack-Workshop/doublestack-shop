import asyncio
import os
import json
import time
from pathlib import Path
from fastapi import APIRouter, Cookie, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from .schemas import (
    AddRepoRequest, CreateNetworkRequest, CurrentRepoRequest, DeployRequest,
    AppriseConfigRequest, DockerMirrorsRequest, ForgotPasswordRequest, GlobalDomainRequest,
    LoginRequest, ProxyRequest, PullImageRequest, RegisterRequest, CreateUserRequest,
    SaveFileRequest, UpdatePasswordRequest,
)
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
    list_docker_networks,
    create_docker_network,
    get_latest_dockerhub_version,
    get_all_images,
    delete_image,
    export_image_archive,
    import_image_archive,
    search_dockerhub_images,
    pull_image,
    test_all_connectivity,
    get_proxy_config,
    set_proxy_config,
    get_apprise_config,
    set_apprise_config,
    test_apprise_notification,
    notify_apprise,
    create_container_backup,
    get_all_backups_list,
    get_backups_for_container,
    remove_backup,
    restore_backup,
    get_backup_by_id,
    get_docker_info,
    get_host_system_info,
    stream_host_metrics,
    get_current_repo,
    set_current_repo,
    load_recommend_config,
    detect_docker_compose,
    generate_compose_upgrade_script,
    resolve_host_scripts_dir,
    schedule_host_docker_restart,
    wait_for_docker_ready,
)
from .database import get_all_deployments, get_deployed_apps_count, get_deployment_success_rate
from .database import (
    get_user_by_username,
    verify_password,
    create_user,
    get_all_users,
    update_user,
    delete_user,
    verify_admin_password,
    reset_admin_password,
    get_setting,
    set_setting,
    create_user_session,
    delete_user_session,
    get_user_by_session,
    password_hash_needs_upgrade,
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

_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCK_SECONDS = 15 * 60
_login_attempts = {}


def _login_key(request: Request, username: str) -> tuple[str, str]:
    return (request.client.host if request.client else "unknown", username.lower())


def _is_login_locked(request: Request, username: str) -> bool:
    attempts, locked_until = _login_attempts.get(_login_key(request, username), (0, 0))
    if locked_until > time.time():
        return True
    if locked_until:
        _login_attempts.pop(_login_key(request, username), None)
    return False


def _record_login_failure(request: Request, username: str) -> None:
    key = _login_key(request, username)
    attempts, locked_until = _login_attempts.get(key, (0, 0))
    attempts += 1
    _login_attempts[key] = (attempts, time.time() + _LOGIN_LOCK_SECONDS if attempts >= _LOGIN_MAX_ATTEMPTS else locked_until)


@router.post("/login")
async def login(request: Request, credentials: LoginRequest):
    if _is_login_locked(request, credentials.username):
        return {"success": False, "message": "登录尝试过多，请 15 分钟后再试"}

    user = get_user_by_username(credentials.username)
    
    if not user:
        _record_login_failure(request, credentials.username)
        log_service.warning(f"用户登录失败: {credentials.username} - 用户不存在", 'auth')
        return {"success": False, "message": "用户名或密码错误"}
    
    if verify_password(credentials.password, user['password']):
        if password_hash_needs_upgrade(user['password']):
            update_user(credentials.username, password=credentials.password)
        _login_attempts.pop(_login_key(request, credentials.username), None)
        log_service.success(f"用户登录成功: {credentials.username}", 'auth')
        token, max_age = create_user_session(user['id'])
        response = JSONResponse(content={
            "success": True,
            "message": "登录成功",
            "data": {
                "username": user['username'],
                "email": user['email'],
                "is_admin": user['is_admin'],
                "created_at": user['created_at']
            }
        })
        response.set_cookie(
            key="session_token",
            value=token,
            max_age=max_age,
            httponly=True,
            samesite="lax",
            secure=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
            path="/",
        )
        return response
    
    _record_login_failure(request, credentials.username)
    log_service.warning(f"用户登录失败: {credentials.username} - 密码错误", 'auth')
    return {"success": False, "message": "用户名或密码错误"}


@router.post("/logout")
async def logout(session_token: str = Cookie(default=None)):
    delete_user_session(session_token)
    response = JSONResponse(content={"success": True, "message": "已退出登录"})
    response.delete_cookie("session_token", path="/")
    return response


@router.get("/me")
async def get_current_user(request: Request):
    """返回由服务端会话确认的当前用户，前端不得自行推断管理员身份。"""
    return {"success": True, "data": request.state.user}

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


@router.post("/users")
async def create_user_endpoint(request: CreateUserRequest):
    """由已登录管理员创建普通用户；权限由 HTTP 中间件统一校验。"""
    if len(request.password) < 6:
        raise HTTPException(status_code=422, detail="密码长度至少为 6 位")
    if create_user(request.username, request.password, None, is_admin=False):
        return {"success": True, "message": "用户添加成功"}
    raise HTTPException(status_code=409, detail="用户名已存在")

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

@router.post("/repos")
async def create_repo(request: AddRepoRequest):
    result = await asyncio.to_thread(
        add_repo, request.repo_url, request.branch, request.local_path, request.name, request.repo_type
    )
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
        count = result.get("data", {}).get("file_count", 0)
        notify_apprise("仓库同步成功", f"仓库“{repo_name}”同步完成，发现 {count} 个文件。", "success", "repo")
        return result
    notify_apprise("仓库同步失败", f"仓库“{repo_name}”同步失败：{result.get('message', '未知错误')}。", "failure", "repo")
    if result.get("status") == "error":
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


@router.get("/dashboard/stats")
async def get_dashboard_stats():
    """向普通登录用户提供仪表盘所需的只读汇总数据。"""
    return {
        "repo_count": len(get_all_repos()),
        "deployed_apps_count": get_deployed_apps_count(),
        "container_count": get_running_containers_count(),
        "success_rate": get_deployment_success_rate(),
    }

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

@router.get("/networks")
async def list_networks_route():
    log_service.info("获取 Docker 网络列表", 'system')
    return {"success": True, "data": list_docker_networks()}

@router.post("/networks")
async def create_network_route(request: CreateNetworkRequest):
    log_service.info(f"创建 Docker 网络: {request.name} (driver={request.driver})", 'system')
    result = create_docker_network(request.name, request.driver)
    if result.get("success"):
        log_service.success(f"Docker 网络创建成功: {request.name}", 'system')
    else:
        log_service.error(f"Docker 网络创建失败: {request.name} - {result.get('message', '')}", 'system')
    return {"success": result.get("success", False), **result}

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


@router.get("/system/host-metrics/stream")
def stream_host_metrics_route():
    """SSE 实时推送 CPU + 内存指标。
    每秒推送一条 {"type":"metrics", ...}，客户端断开时自动结束。
    磁盘/OS/网卡等不变信息仍由 GET /api/system/host-info 一次性返回。
    """
    log_service.info("建立宿主机实时指标SSE连接", 'system')
    return StreamingResponse(
        _sse_stream(stream_host_metrics(cpu_interval_sec=1.0, max_updates=0)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


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
    update_script_path = None
    host_paths = None
    
    if is_update_available:
        log_service.info(f"发现新版本: {latest_version}", 'system')
        update_script_path = generate_update_script(latest_version)
        host_paths = resolve_host_scripts_dir("update_app.sh")
    
    return {
        "success": True,
        "current_version": VERSION,
        "latest_version": latest_version,
        "update_available": is_update_available,
        "update_script_path": update_script_path,
        "host": host_paths,
        "run_command_one_liner": host_paths.get("command_one_liner") if host_paths else None,
    }

@router.get("/system/check-compose-upgrade")
async def check_compose_upgrade_route():
    """检测 Docker Compose v1/v2 状态，若仅 v1 或未安装则生成 v2 升级脚本。
    行为参考 /system/check-update：检测 → 按需生成脚本 → 返回状态。
    """
    log_service.info("检查 Docker Compose 升级状态", 'system')
    compose_info = detect_docker_compose()

    script_generated = False
    script_path = None
    script_error = None

    if compose_info.get("needs_upgrade"):
        log_service.info(
            f"Docker Compose 需要升级 (status={compose_info.get('status')})，正在生成升级脚本",
            'system'
        )
        try:
            script_path = generate_compose_upgrade_script()
            script_generated = True
            log_service.success(f"Docker Compose 升级脚本已生成: {script_path}", 'system')
        except Exception as e:
            script_error = str(e)
            log_service.error(f"Docker Compose 升级脚本生成失败: {script_error}", 'system')

    # 无论是否生成了脚本，都反向推导宿主机脚本路径并给出用户可直接执行的命令
    host_paths = resolve_host_scripts_dir("upgrade_docker_compose_to_v2.sh")

    return {
        "success": True,
        "v1_version": compose_info.get("v1_version", ""),
        "v2_version": compose_info.get("v2_version", ""),
        "status": compose_info.get("status", "not_installed"),
        "needs_upgrade": compose_info.get("needs_upgrade", False),
        "upgrade_script_generated": script_generated,
        "upgrade_script_path": script_path,
        "upgrade_script_error": script_error,
        # ↓↓ 宿主机路径推导结果 + 用户可直接粘贴执行的命令 ↓↓
        "host": host_paths,
        "run_command_one_liner": host_paths.get("command_one_liner"),
        "run_command_cd_style": host_paths.get("command_cd_style"),
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
    return script_path

@router.get("/images")
async def list_images():
    log_service.info("获取镜像列表", 'query')
    images = get_all_images()
    return images

@router.post("/images/pull")
async def pull_image_endpoint(request: PullImageRequest):
    """流式拉取镜像，返回 SSE 事件流。"""
    return StreamingResponse(
        _sse_stream(pull_image(request.image_name)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/images/{image_id}/export")
async def export_image_endpoint(image_id: str):
    result = export_image_archive(image_id)
    if not result["success"]:
        status_code = 404 if result["message"] == "未找到镜像" else 500
        raise HTTPException(status_code=status_code, detail=result["message"])
    return FileResponse(
        path=result["archive_path"],
        filename=result["filename"],
        media_type="application/x-tar",
    )


@router.post("/images/import")
async def import_image_endpoint(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".tar"):
        raise HTTPException(status_code=400, detail="请选择 .tar 格式的 Docker 镜像包")

    max_size = int(os.getenv("MAX_IMAGE_IMPORT_SIZE", str(20 * 1024 ** 3)))
    import_dir = Path(os.getenv("IMAGE_ARCHIVE_DIR", "/app/image"))
    import_dir.mkdir(parents=True, exist_ok=True)
    original_name = Path(file.filename).name
    safe_name = "".join(char if char.isalnum() or char in "._-" else "_" for char in original_name)
    archive_path = import_dir / f"import-{int(time.time() * 1000)}-{safe_name or 'docker-image.tar'}"
    total_size = 0
    upload_completed = False
    try:
        with archive_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > max_size:
                    raise HTTPException(status_code=413, detail="镜像包超过允许的导入大小")
                destination.write(chunk)
        if total_size == 0:
            raise HTTPException(status_code=400, detail="镜像包为空")
        upload_completed = True
        result = import_image_archive(archive_path)
        if result["success"]:
            log_service.info(f"镜像导入包已保存: {archive_path.name}", 'image')
            result["message"] = "镜像导入成功，镜像包已保存到 image 目录"
            return result
        raise HTTPException(status_code=500, detail=result["message"])
    finally:
        await file.close()
        if not upload_completed:
            try:
                archive_path.unlink(missing_ok=True)
            except OSError as exc:
                log_service.warning(f"无法清理未完成的镜像包: {exc}", 'image')

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
    user = get_user_by_session(websocket.cookies.get("session_token"))
    if not user or not user["is_admin"]:
        await websocket.close(code=1008, reason="需要管理员登录")
        return
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
    except (WebSocketDisconnect, ValueError):
        # 客户端在首次尺寸协商前关闭时无需创建终端。
        return

    try:
        await terminal_manager.create_host_terminal(websocket, cols, rows)
    except (OSError, RuntimeError) as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except (WebSocketDisconnect, RuntimeError):
            # 连接已断开，无法再向客户端报告终端创建错误。
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

@router.put("/proxy")
async def update_proxy(request: ProxyRequest):
    """更新代理配置"""
    result = set_proxy_config(request.http_proxy, request.https_proxy)
    return result


@router.get("/apprise")
async def get_apprise():
    """获取 Apprise 局域网通知配置。"""
    return {"success": True, "data": get_apprise_config()}


@router.put("/apprise")
async def update_apprise(request: AppriseConfigRequest):
    """更新 Apprise 通知配置。"""
    return set_apprise_config(request.url, request.key, request.enabled, request.events)


@router.post("/apprise/test")
async def test_apprise():
    """发送一条 Apprise 测试通知。"""
    result = test_apprise_notification()
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result

# 当前系统仓库相关路由
@router.get("/current-repo")
async def get_current_repo_route():
    """获取当前系统仓库"""
    repo_name = get_current_repo()
    return {"success": True, "data": {"repo_name": repo_name}}

@router.put("/current-repo")
async def set_current_repo_route(request: CurrentRepoRequest):
    """设置当前系统仓库"""
    if not set_current_repo(request.repo_name):
        raise HTTPException(status_code=400, detail="仅已存在的 Compose 仓库可设为当前仓库")
    log_service.info(f"当前系统仓库已设置为: {request.repo_name}", 'system')
    return {"success": True, "message": f"当前系统仓库已设置为: {request.repo_name}"}

# 容器推荐配置路由
@router.get("/recommend-config")
async def get_recommend_config_route():
    """获取容器推荐配置（从运行时 data/recommend.json 读取）"""
    config = load_recommend_config()
    return {"success": True, "data": config}

# 全局域名/IP 设置相关路由
@router.get("/global-domain")
async def get_global_domain():
    """获取全局域名/IP配置"""
    domain = get_setting("global_domain", "")
    return {"success": True, "data": {"global_domain": domain}}

@router.put("/global-domain")
async def update_global_domain(request: GlobalDomainRequest):
    """更新全局域名/IP配置"""
    set_setting("global_domain", request.global_domain)
    log_service.info(f"全局域名/IP已更新: {request.global_domain}", 'system')
    return {"success": True, "message": "配置已保存"}

# Docker 加速源相关路由

DAEMON_JSON_PATH = Path("/etc/docker/daemon.json")
docker_restart_status = {"state": "idle", "message": "尚未执行 Docker 重启", "updated_at": None}


def _write_daemon_config(config: dict) -> None:
    """原子写入 Docker daemon 配置，避免服务读取到半截 JSON。"""
    temp_path = DAEMON_JSON_PATH.with_suffix(".json.tmp")
    with open(temp_path, 'w') as f:
        json.dump(config, f, indent=2)
    temp_path.replace(DAEMON_JSON_PATH)


async def _monitor_docker_restart(previous_config: dict) -> None:
    """确认 Docker 重启结果；无法恢复时回滚加速源并再次安排恢复重启。"""
    global docker_restart_status
    recovered = await asyncio.to_thread(wait_for_docker_ready)
    docker_restart_status = {
        "state": "ready" if recovered else "failed",
        "message": "Docker 已恢复" if recovered else "Docker 未在限定时间内恢复，已回滚加速源配置",
        "updated_at": time.time(),
    }
    if recovered:
        log_service.success("Docker 重启完成，服务已恢复", 'system')
        notify_apprise("Docker 服务已恢复", "Docker 加速源配置生效，Docker 服务已恢复。", "success", "docker")
        return

    try:
        _write_daemon_config(previous_config)
        rollback = await asyncio.to_thread(schedule_host_docker_restart, 1)
        docker_restart_status["rollback_scheduled"] = rollback["success"]
        if not rollback["success"]:
            docker_restart_status["message"] += f"；恢复重启任务创建失败：{rollback['message']}"
        log_service.error(docker_restart_status["message"], 'system')
        notify_apprise("Docker 重启失败", docker_restart_status["message"], "failure", "docker")
    except (OSError, TypeError, ValueError) as exc:
        docker_restart_status["message"] += f"；回滚失败：{exc}"
        log_service.error(docker_restart_status["message"], 'system')
        notify_apprise("Docker 重启失败", docker_restart_status["message"], "failure", "docker")

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
    except (OSError, json.JSONDecodeError) as e:
        log_service.error(f"获取 Docker 加速源配置失败: {str(e)}", 'system')
        return {"success": False, "message": f"读取配置失败: {str(e)}", "mirrors": []}

@router.put("/docker-mirrors")
async def update_docker_mirrors(request: DockerMirrorsRequest):
    """更新 Docker 加速源配置并安排重启宿主机 Docker 服务。"""
    try:
        # 读取现有配置
        config = {}
        if DAEMON_JSON_PATH.exists():
            with open(DAEMON_JSON_PATH, 'r') as f:
                config = json.load(f)
        
        previous_config = config.copy()
        # 更新加速源
        config["registry-mirrors"] = request.mirrors
        
        _write_daemon_config(config)

        restart_result = await asyncio.to_thread(schedule_host_docker_restart)
        if not restart_result["success"]:
            _write_daemon_config(previous_config)
            log_service.error(f"Docker 加速源已保存，但重启任务创建失败: {restart_result['message']}", 'system')
            notify_apprise("Docker 重启失败", f"重启任务未创建，已回滚加速源配置：{restart_result['message']}。", "failure", "docker")
            return {
                "success": False,
                "message": f"未能创建 Docker 重启任务，已还原原加速源配置：{restart_result['message']}",
                "mirrors": previous_config.get("registry-mirrors", []),
            }
        
        docker_restart_status.update({
            "state": "restarting", "message": "Docker 正在重启", "updated_at": time.time(),
            "helper_container_id": restart_result.get("helper_container_id"),
        })
        asyncio.create_task(_monitor_docker_restart(previous_config))
        log_service.success(f"更新 Docker 加速源配置: {len(request.mirrors)} 个加速源", 'system')
        return {
            "success": True, 
            "message": "配置已保存，Docker 正在重启，服务恢复后将自动刷新页面",
            "mirrors": request.mirrors
        }
    except PermissionError:
        log_service.error("更新 Docker 加速源配置失败: 权限不足", 'system')
        return {"success": False, "message": "权限不足，请确保容器以正确权限运行"}
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
        log_service.error(f"更新 Docker 加速源配置失败: {str(e)}", 'system')
        return {"success": False, "message": f"保存配置失败: {str(e)}"}


@router.get("/docker-mirrors/restart-status")
async def get_docker_restart_status():
    """返回最近一次加速源触发的 Docker 重启/回滚状态。"""
    return {"success": True, **docker_restart_status}

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

@router.post("/containers/{container_id}/backup")
async def create_backup_endpoint(container_id: str):
    """创建容器备份"""
    result = create_container_backup(container_id)
    if result["success"]:
        container_name = result.get("data", {}).get("container_name", container_id[:12])
        notify_apprise("容器备份成功", f"容器“{container_name}”备份已创建。", "success", "backup")
        return result
    notify_apprise("容器备份失败", f"容器 {container_id[:12]} 备份失败：{result.get('message', '未知错误')}。", "failure", "backup")
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
        container_name = result.get("data", {}).get("container_name", "")
        notify_apprise("容器备份恢复成功", f"容器“{container_name or backup_id}”已从备份恢复。", "success", "backup")
        return result
    notify_apprise("容器备份恢复失败", f"备份 #{backup_id} 恢复失败：{result.get('message', '未知错误')}。", "failure", "backup")
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
