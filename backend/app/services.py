import subprocess
import os
import shutil
import datetime
from pathlib import Path
from typing import List, Dict, Optional
from .schemas import YmlFile, RepoInfo
from .logger import log_service

REPOS_DIR = Path("./repos")
REPOS_DIR.mkdir(exist_ok=True)

from .database import (
    get_all_repos_from_db,
    add_repo_to_db,
    update_repo_in_db,
    delete_repo_from_db,
    get_repo_by_name_from_db,
    get_proxy_config as db_get_proxy_config,
    set_proxy_config as db_set_proxy_config,
    get_images_cache,
    update_images_cache
)

# 初始化：从数据库加载仓库信息
repos_db: List[RepoInfo] = []
_repos_loaded = False

def _load_repos_from_db():
    global repos_db, _repos_loaded
    if _repos_loaded:
        return
    
    try:
        db_repos = get_all_repos_from_db()
        for repo in db_repos:
            yml_files = []
            if repo.get('yml_files'):
                for f in repo['yml_files']:
                    yml_files.append(YmlFile(
                        name=f['name'],
                        path=f['path'],
                        content=f.get('content', '')
                    ))
            
            repo_info = RepoInfo(
                name=repo['name'],
                url=repo['url'],
                branch=repo['branch'],
                local_path=repo.get('local_path', ''),
                yml_files=yml_files,
                last_sync=repo.get('last_sync'),
                status=repo.get('status', 'active'),
                repo_dir_name=repo.get('repo_dir_name', repo['name'])
            )
            repos_db.append(repo_info)
        _repos_loaded = True
    except Exception as e:
        print(f"从数据库加载仓库信息失败: {e}")

# 延迟加载仓库
def _ensure_repos_loaded():
    if not _repos_loaded:
        _load_repos_from_db()

# 初始化代理配置
proxy_config: Dict = db_get_proxy_config()

def get_repo_name_from_url(url: str) -> str:
    return url.rstrip('/').split('/')[-1].replace('.git', '')

def clone_or_pull_repo(repo_url: str, branch: str, local_path: str) -> Dict:
    repo_name = get_repo_name_from_url(repo_url)
    repo_dir = REPOS_DIR / repo_name
    
    try:
        if repo_dir.exists():
            result = subprocess.run(
                ["git", "pull", "origin", branch],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=60
            )
            sync_type = "更新"
        else:
            result = subprocess.run(
                ["git", "clone", "-b", branch, "--depth", "1", repo_url, str(repo_dir)],
                capture_output=True,
                text=True,
                timeout=120
            )
            sync_type = "克隆"
            
        if result.returncode != 0:
            return {
                "success": False,
                "message": f"{sync_type}失败: {result.stderr}",
                "status": "error"
            }
        
        return {
            "success": True,
            "message": f"仓库{sync_type}成功",
            "status": "active",
            "path": str(repo_dir)
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": "操作超时",
            "status": "error"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"操作失败: {str(e)}",
            "status": "error"
        }

def scan_yml_files(repo_dir: Path, local_path: str = "") -> List[YmlFile]:
    yml_files = []
    
    scan_dir = repo_dir
    if local_path:
        scan_dir = repo_dir / local_path
        if not scan_dir.exists():
            scan_dir = repo_dir
    
    for yml_path in scan_dir.rglob("*.yml"):
        try:
            with open(yml_path, 'r', encoding='utf-8') as f:
                content = f.read()
                yml_files.append(YmlFile(
                    name=yml_path.name,
                    path=str(yml_path.relative_to(repo_dir)),
                    content=content
                ))
        except Exception:
            continue
            
    for yml_path in scan_dir.rglob("*.yaml"):
        try:
            with open(yml_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if yml_path.name not in [y.name for y in yml_files]:
                    yml_files.append(YmlFile(
                        name=yml_path.name,
                        path=str(yml_path.relative_to(repo_dir)),
                        content=content
                    ))
        except Exception:
            continue
    
    return yml_files

def get_all_repos() -> List[Dict]:
    _ensure_repos_loaded()
    return [
        {
            "name": repo.name,
            "url": repo.url,
            "branch": repo.branch,
            "local_path": repo.local_path,
            "yml_count": len(repo.yml_files),
            "last_sync": repo.last_sync,
            "status": repo.status
        }
        for repo in repos_db
    ]

def add_repo(repo_url: str, branch: str, local_path: str, name: Optional[str] = None) -> Dict:
    _ensure_repos_loaded()
    repo_name = name if name else get_repo_name_from_url(repo_url)
    actual_repo_dir_name = get_repo_name_from_url(repo_url)
    
    for repo in repos_db:
        if repo.name == repo_name:
            log_service.warning(f"仓库已存在: {repo_name}", 'system')
            return {"success": False, "message": "仓库已存在", "status": "error"}
    
    result = clone_or_pull_repo(repo_url, branch, local_path)
    
    if not result["success"]:
        log_service.error(f"仓库添加失败: {repo_name} - {result.get('message', '未知错误')}", 'system')
        return result
    
    repo_dir = Path(result["path"])
    yml_files = scan_yml_files(repo_dir, local_path)
    
    repo_info = RepoInfo(
        name=repo_name,
        url=repo_url,
        branch=branch,
        local_path=local_path,
        yml_files=yml_files,
        last_sync="刚刚",
        status="active",
        repo_dir_name=actual_repo_dir_name
    )
    repos_db.append(repo_info)
    
    # 保存到数据库
    try:
        add_repo_to_db(repo_name, repo_url, branch, local_path, actual_repo_dir_name, yml_files, "刚刚", "active")
    except Exception as e:
        print(f"保存仓库到数据库失败: {e}")
    
    log_service.success(f"仓库添加成功: {repo_name} (发现 {len(yml_files)} 个 YML 文件)", 'system')
    
    return {
        "success": True,
        "message": f"仓库添加成功，发现 {len(yml_files)} 个 YML 文件",
        "data": {
            "name": repo_name,
            "yml_count": len(yml_files),
            "yml_files": [
                {"name": f.name, "path": f.path}
                for f in yml_files
            ]
        }
    }

def sync_repo(repo_name: str) -> Dict:
    _ensure_repos_loaded()
    for i, repo in enumerate(repos_db):
        if repo.name == repo_name:
            result = clone_or_pull_repo(repo.url, repo.branch, repo.local_path)
            
            if result["success"]:
                repo_dir = Path(result["path"])
                repo.yml_files = scan_yml_files(repo_dir, repo.local_path)
                repo.status = "active"
                repo.last_sync = "刚刚"
                repos_db[i] = repo
                
                # 更新数据库
                try:
                    update_repo_in_db(repo_name, yml_files=repo.yml_files, last_sync="刚刚", status="active")
                except Exception as e:
                    print(f"更新仓库数据库失败: {e}")
                
                log_service.info(f"仓库同步成功: {repo_name} (发现 {len(repo.yml_files)} 个 YML 文件)", 'system')
                
                return {
                    "success": True,
                    "message": f"同步成功，发现 {len(repo.yml_files)} 个 YML 文件",
                    "data": {
                        "yml_count": len(repo.yml_files),
                        "yml_files": [
                            {"name": f.name, "path": f.path}
                            for f in repo.yml_files
                        ]
                    }
                }
            else:
                log_service.error(f"仓库同步失败: {repo_name} - {result.get('message', '未知错误')}", 'system')
                return result
    
    log_service.warning(f"仓库不存在: {repo_name}", 'system')
    return {"success": False, "message": "仓库不存在", "status": "error"}

def get_repo(repo_name: str) -> Optional[Dict]:
    _ensure_repos_loaded()
    for repo in repos_db:
        if repo.name == repo_name:
            return {
                "name": repo.name,
                "url": repo.url,
                "branch": repo.branch,
                "local_path": repo.local_path,
                "yml_files": [
                    {"name": f.name, "path": f.path}
                    for f in repo.yml_files
                ],
                "last_sync": repo.last_sync,
                "status": repo.status
            }
    return None

def get_yml_content(repo_name: str, file_path: str) -> Optional[Dict]:
    _ensure_repos_loaded()
    for repo in repos_db:
        if repo.name == repo_name:
            actual_repo_dir_name = repo.repo_dir_name if repo.repo_dir_name else get_repo_name_from_url(repo.url)
            repo_dir = REPOS_DIR / actual_repo_dir_name
            for yml_file in repo.yml_files:
                if yml_file.path == file_path or yml_file.name == file_path:
                    file_full_path = repo_dir / yml_file.path
                    if file_full_path.exists():
                        import os
                        mtime = file_full_path.stat().st_mtime
                        from datetime import datetime, timezone, timedelta
                        last_modified = datetime.fromtimestamp(mtime, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        last_modified = repo.last_sync or '未知'
                    
                    return {
                        "name": yml_file.name,
                        "path": yml_file.path,
                        "content": yml_file.content,
                        "last_modified": last_modified
                    }
    return None

def get_repo_files(repo_name: str) -> Optional[List[Dict]]:
    _ensure_repos_loaded()
    for repo in repos_db:
        if repo.name == repo_name:
            return [
                {"name": f.name, "path": f.path}
                for f in repo.yml_files
            ]
    return None

def save_file_content(repo_name: str, file_name: str, content: str) -> bool:
    _ensure_repos_loaded()
    for repo in repos_db:
        if repo.name == repo_name:
            actual_repo_dir_name = repo.repo_dir_name if repo.repo_dir_name else get_repo_name_from_url(repo.url)
            repo_dir = REPOS_DIR / actual_repo_dir_name
            for i, yml_file in enumerate(repo.yml_files):
                if yml_file.name == file_name:
                    file_path = repo_dir / yml_file.path
                    try:
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(content)
                        repo.yml_files[i].content = content
                        # 更新数据库
                        try:
                            update_repo_in_db(repo_name, yml_files=repo.yml_files)
                        except Exception as e:
                            print(f"更新仓库数据库失败: {e}")
                        return True
                    except Exception as e:
                        print(f"保存文件失败: {e}")
                        return False
    return False

def delete_repo(repo_name: str) -> bool:
    _ensure_repos_loaded()
    for i, repo in enumerate(repos_db):
        if repo.name == repo_name:
            repos_db.pop(i)
            # 从数据库删除
            try:
                delete_repo_from_db(repo_name)
            except Exception as e:
                print(f"从数据库删除仓库失败: {e}")
            log_service.warning(f"仓库已删除: {repo_name}", 'system')
            return True
    log_service.warning(f"删除仓库失败: {repo_name} - 仓库不存在", 'system')
    return False

def deploy_yml(repo_name: str, file_path: str) -> Optional[Dict]:
    from .database import add_deployment
    
    for repo in repos_db:
        if repo.name == repo_name:
            for yml_file in repo.yml_files:
                if yml_file.path == file_path or yml_file.name == file_path:
                    # 使用实际的仓库目录名称，而不是自定义名称
                    actual_repo_dir_name = repo.repo_dir_name if repo.repo_dir_name else get_repo_name_from_url(repo.url)
                    repo_dir = REPOS_DIR / actual_repo_dir_name
                    yml_full_path = repo_dir / yml_file.path
                    
                    # 设置环境变量（包含代理配置）
                    env = os.environ.copy()
                    if proxy_config["http_proxy"]:
                        env["HTTP_PROXY"] = proxy_config["http_proxy"]
                        env["http_proxy"] = proxy_config["http_proxy"]
                    if proxy_config["https_proxy"]:
                        env["HTTPS_PROXY"] = proxy_config["https_proxy"]
                        env["https_proxy"] = proxy_config["https_proxy"]
                    
                    try:
                        deployment_logs = []
                        
                        deployment_logs.append(f"[部署开始] 正在处理文件: {yml_file.name}")
                        deployment_logs.append(f"[部署开始] 文件路径: {yml_full_path}")
                        
                        pull_result = subprocess.run(
                            ["docker-compose", "-f", str(yml_full_path), "pull"],
                            capture_output=True,
                            text=True,
                            timeout=300,
                            env=env
                        )
                        
                        if pull_result.stdout:
                            for line in pull_result.stdout.strip().split('\n'):
                                if line.strip():
                                    deployment_logs.append(f"[镜像拉取] {line.strip()}")
                        
                        if pull_result.stderr:
                            for line in pull_result.stderr.strip().split('\n'):
                                if line.strip():
                                    deployment_logs.append(f"[镜像拉取] {line.strip()}")
                        
                        deployment_logs.append("[部署阶段] 启动容器...")
                        
                        up_result = subprocess.run(
                            ["docker-compose", "-f", str(yml_full_path), "up", "-d"],
                            capture_output=True,
                            text=True,
                            timeout=120,
                            env=env
                        )
                        
                        if up_result.stdout:
                            for line in up_result.stdout.strip().split('\n'):
                                if line.strip():
                                    deployment_logs.append(f"[启动日志] {line.strip()}")
                        
                        if up_result.stderr:
                            for line in up_result.stderr.strip().split('\n'):
                                if line.strip():
                                    deployment_logs.append(f"[启动日志] {line.strip()}")
                        
                        result = up_result
                        
                        if result.returncode == 0:
                            container_id = None
                            container_name = None
                            
                            try:
                                ps_result = subprocess.run(
                                    ["docker-compose", "-f", str(yml_full_path), "ps", "-q"],
                                    capture_output=True,
                                    text=True,
                                    timeout=30
                                )
                                if ps_result.returncode == 0 and ps_result.stdout.strip():
                                    container_id = ps_result.stdout.strip().split('\n')[0]
                                
                                ps_full_result = subprocess.run(
                                    ["docker-compose", "-f", str(yml_full_path), "ps"],
                                    capture_output=True,
                                    text=True,
                                    timeout=30
                                )
                                if ps_full_result.returncode == 0:
                                    lines = ps_full_result.stdout.strip().split('\n')
                                    if len(lines) > 1:
                                        container_name = lines[1].split()[0]
                            except Exception:
                                pass
                            
                            add_deployment(repo_name, yml_file.name, container_id, container_name, 'deployed', f'部署成功')
                            
                            deployment_logs.append(f"[部署成功] 容器ID: {container_id}")
                            deployment_logs.append(f"[部署成功] 容器名称: {container_name}")
                            
                            # 记录日志（包含详细部署日志）
                            log_service.success(f"容器部署成功: {yml_file.name} (容器名: {container_name})", 'deploy', deployment_logs)
                            
                            return {
                                "success": True,
                                "message": f"部署成功: {yml_file.name}",
                                "data": {
                                    "repo_name": repo_name,
                                    "file_name": yml_file.name,
                                    "file_path": yml_file.path,
                                    "status": "deployed",
                                    "output": result.stdout,
                                    "detailed_logs": deployment_logs,
                                    "container_id": container_id,
                                    "container_name": container_name
                                }
                            }
                        else:
                            # 记录日志
                            log_service.error(f"容器部署失败: {yml_file.name} - {result.stderr}", 'deploy')
                            return {
                                "success": False,
                                "message": f"部署失败: {result.stderr}",
                                "data": {
                                    "repo_name": repo_name,
                                    "file_name": yml_file.name,
                                    "file_path": yml_file.path,
                                    "status": "failed",
                                    "error": result.stderr
                                }
                            }
                    except subprocess.TimeoutExpired:
                        # 记录日志
                        log_service.error(f"容器部署超时: {yml_file.name}", 'deploy')
                        return {
                            "success": False,
                            "message": "部署超时",
                            "data": {
                                "repo_name": repo_name,
                                "file_name": yml_file.name,
                                "file_path": yml_file.path,
                                "status": "timeout"
                            }
                        }
                    except FileNotFoundError:
                        # 记录日志
                        log_service.error(f"docker-compose 命令未找到: {yml_file.name}", 'deploy')
                        return {
                            "success": False,
                            "message": "docker-compose 命令未找到，请确保已安装 Docker Compose",
                            "data": {
                                "repo_name": repo_name,
                                "file_name": yml_file.name,
                                "file_path": yml_file.path,
                                "status": "error"
                            }
                        }
                    except Exception as e:
                        # 记录日志
                        log_service.error(f"容器部署异常: {yml_file.name} - {str(e)}", 'deploy')
                        return {
                            "success": False,
                            "message": f"部署异常: {str(e)}",
                            "data": {
                                "repo_name": repo_name,
                                "file_name": yml_file.name,
                                "file_path": yml_file.path,
                                "status": "error"
                            }
                        }
    return None

def get_running_containers_count() -> int:
    try:
        result = subprocess.run(
            ["docker", "ps", "--quiet"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            return len([line for line in lines if line.strip()])
        else:
            return 0
    except subprocess.TimeoutExpired:
        return 0
    except FileNotFoundError:
        return 0
    except Exception:
        return 0

def get_all_containers() -> list:
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}|{{.CreatedAt}}|{{.Command}}"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            containers = []
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line.strip():
                    parts = line.split('|')
                    if len(parts) >= 7:
                        container_id = parts[0].strip()
                        name = parts[1].strip()
                        image = parts[2].strip()
                        status = parts[3].strip()
                        ports = parts[4].strip()
                        created_at = parts[5].strip()
                        command = parts[6].strip()
                        
                        state = 'running' if 'Up' in status else 'exited'
                        uptime = ''
                        if 'Up' in status:
                            uptime_match = status.split('Up ')[1].split(' ')[0]
                            uptime = uptime_match
                        
                        ports_list = []
                        if ports != '<none>':
                            ports_list = [p.strip() for p in ports.split(',')]
                        
                        containers.append({
                            'id': container_id,
                            'name': name,
                            'image': image,
                            'state': state,
                            'status': status,
                            'uptime': uptime,
                            'ports': ports_list,
                            'created_at': created_at,
                            'command': command
                        })
            return containers
        else:
            return []
    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        return []
    except Exception:
        return []

def get_container_by_id(container_id: str) -> dict:
    import datetime
    try:
        result = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)[0]
            
            state = data['State']['Status']
            uptime = ''
            if state == 'running' and data['State']['StartedAt']:
                started_at = datetime.datetime.fromisoformat(data['State']['StartedAt'].replace('Z', '+00:00'))
                now = datetime.datetime.now(datetime.timezone.utc)
                delta = now - started_at
                
                days = delta.days
                hours = delta.seconds // 3600
                minutes = (delta.seconds % 3600) // 60
                
                if days > 0:
                    uptime = f'{days}天{hours}小时{minutes}分钟'
                elif hours > 0:
                    uptime = f'{hours}小时{minutes}分钟'
                else:
                    uptime = f'{minutes}分钟'
            
            ports = []
            try:
                network_ports = data.get('NetworkSettings', {}).get('Ports', [])
                if isinstance(network_ports, list):
                    for port in network_ports:
                        if isinstance(port, dict) and port.get('PublicPort'):
                            ports.append(f"{port['PublicPort']}->{port['PrivatePort']}/{port['Type']}")
                elif isinstance(network_ports, dict):
                    for private_port, bindings in network_ports.items():
                        if bindings and isinstance(bindings, list):
                            for binding in bindings:
                                if binding and binding.get('HostPort'):
                                    ports.append(f"{binding['HostPort']}->{private_port}")
            except Exception:
                ports = []
            
            created_at = data['Created']
            if created_at:
                created_dt = datetime.datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                created_dt = created_dt.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
                created_at = created_dt.strftime('%Y-%m-%d %H:%M:%S')
            
            return {
                'id': data['Id'],
                'name': data['Name'].lstrip('/'),
                'image': data['Config']['Image'],
                'state': state,
                'status': data['State']['Status'],
                'uptime': uptime,
                'ports': ports,
                'created_at': created_at,
                'command': ' '.join(data['Config']['Cmd']) if data['Config']['Cmd'] else ''
            }
        else:
            return None
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None

def start_container(container_id: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "start", container_id],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            log_service.success(f"容器启动成功: {container_id[:12]}", 'container')
            return True
        else:
            log_service.error(f"容器启动失败: {container_id[:12]} - {result.stderr}", 'container')
            return False
    except subprocess.TimeoutExpired:
        log_service.error(f"容器启动超时: {container_id[:12]}", 'container')
        return False
    except FileNotFoundError:
        return False
    except Exception:
        return False

def stop_container(container_id: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "stop", container_id],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            log_service.warning(f"容器已停止: {container_id[:12]}", 'container')
            return True
        else:
            log_service.error(f"容器停止失败: {container_id[:12]} - {result.stderr}", 'container')
            return False
    except subprocess.TimeoutExpired:
        log_service.error(f"容器停止超时: {container_id[:12]}", 'container')
        return False
    except FileNotFoundError:
        return False
    except Exception:
        return False

def restart_container(container_id: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "restart", container_id],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            log_service.success(f"容器重启成功: {container_id[:12]}", 'container')
            return True
        else:
            log_service.error(f"容器重启失败: {container_id[:12]} - {result.stderr}", 'container')
            return False
    except subprocess.TimeoutExpired:
        log_service.error(f"容器重启超时: {container_id[:12]}", 'container')
        return False
    except FileNotFoundError:
        return False
    except Exception:
        return False

def remove_container(container_id: str, force: bool = False) -> bool:
    try:
        cmd = ["docker", "rm", container_id]
        if force:
            cmd.insert(2, "-f")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            log_service.warning(f"容器已删除: {container_id[:12]}", 'container')
            return True
        else:
            log_service.error(f"容器删除失败: {container_id[:12]} - {result.stderr}", 'container')
            return False
    except subprocess.TimeoutExpired:
        log_service.error(f"容器删除超时: {container_id[:12]}", 'container')
        return False
    except FileNotFoundError:
        return False
    except Exception:
        return False

def get_all_images(use_cache=True) -> list:
    """获取所有镜像列表，支持缓存"""
    # 如果使用缓存，尝试从数据库读取
    if use_cache:
        try:
            cached = get_images_cache()
            if cached:
                return cached
        except Exception as e:
            print(f"读取镜像缓存失败: {e}")
    
    # 从 Docker 获取
    try:
        result = subprocess.run(
            ["docker", "images", "--filter", "dangling=false", "--format", "{{.ID}}|{{.Repository}}|{{.Tag}}|{{.Size}}|{{.CreatedSince}}|{{.CreatedAt}}"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            images = []
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line.strip():
                    parts = line.split('|')
                    if len(parts) >= 6:
                        image_id = parts[0].strip()
                        repository = parts[1].strip()
                        tag = parts[2].strip()
                        size = parts[3].strip()
                        created_since = parts[4].strip()
                        created_at = parts[5].strip()
                        
                        repo_tags = []
                        if repository != '<none>':
                            repo_tags.append(f"{repository}:{tag}")
                        
                        images.append({
                            'id': image_id,
                            'name': repository if repository != '<none>' else 'untagged',
                            'tag': tag,
                            'repo_tags': repo_tags,
                            'size': parse_size(size),
                            'created_since': created_since,
                            'created_at': created_at
                        })
            
            # 更新缓存
            try:
                update_images_cache(images)
            except Exception as e:
                print(f"更新镜像缓存失败: {e}")
            
            return images
        else:
            return []
    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        return []
    except Exception:
        return []

def refresh_images_cache() -> list:
    """强制刷新镜像缓存"""
    return get_all_images(use_cache=False)

def parse_size(size_str: str) -> int:
    try:
        size_str = size_str.strip()
        if size_str.endswith('GB'):
            return int(float(size_str[:-2]) * 1024 * 1024 * 1024)
        elif size_str.endswith('MB'):
            return int(float(size_str[:-2]) * 1024 * 1024)
        elif size_str.endswith('KB'):
            return int(float(size_str[:-2]) * 1024)
        elif size_str.endswith('B'):
            return int(size_str[:-1])
        return 0
    except Exception:
        return 0

def delete_image(image_id: str) -> Dict:
    try:
        result = subprocess.run(
            ["docker", "rmi", "-f", image_id],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            # 刷新缓存
            try:
                refresh_images_cache()
            except Exception as e:
                print(f"刷新镜像缓存失败: {e}")
            log_service.warning(f"镜像已删除: {image_id}", 'image')
            return {"success": True, "message": "镜像删除成功"}
        else:
            log_service.error(f"镜像删除失败: {image_id} - {result.stderr}", 'image')
            return {"success": False, "message": f"删除失败: {result.stderr}"}
    except subprocess.TimeoutExpired:
        log_service.error(f"镜像删除超时: {image_id}", 'image')
        return {"success": False, "message": "删除操作超时"}
    except FileNotFoundError:
        return {"success": False, "message": "Docker命令不可用"}
    except Exception as e:
        log_service.error(f"镜像删除异常: {image_id} - {str(e)}", 'image')
        return {"success": False, "message": f"删除失败: {str(e)}"}

def search_dockerhub_images(query: str) -> list:
    try:
        url = f"https://hub.docker.com/v2/search/repositories?query={query}&page_size=20"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            results = []
            for result in data.get('results', []):
                name = result.get('name') or result.get('repo_name')
                
                if not name:
                    continue
                    
                description = result.get('description') or result.get('short_description') or '暂无描述'
                is_official = result.get('is_official', False)
                is_automated = result.get('is_automated', False)
                
                tags = ['latest']
                try:
                    tags_url = f"https://hub.docker.com/v2/repositories/{name}/tags?page_size=10"
                    tags_response = requests.get(tags_url, timeout=5)
                    if tags_response.status_code == 200:
                        tags_data = tags_response.json()
                        tag_results = tags_data.get('results', [])
                        tags = [tag.get('name') for tag in tag_results if tag.get('name')][:5]
                except Exception:
                    tags = ['latest']
                
                results.append({
                    'name': name,
                    'description': description if description else '暂无描述',
                    'is_official': is_official,
                    'is_automated': is_automated,
                    'tags': tags if tags else ['latest']
                })
            return results
        return []
    except requests.exceptions.RequestException:
        return []
    except Exception:
        return []

def pull_image(image_name: str) -> Dict:
    try:
        result = subprocess.run(
            ["docker", "pull", image_name],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            # 刷新缓存
            try:
                refresh_images_cache()
            except Exception as e:
                print(f"刷新镜像缓存失败: {e}")
            log_service.success(f"镜像拉取成功: {image_name}", 'image')
            return {"success": True, "message": f"镜像拉取成功: {image_name}"}
        else:
            log_service.error(f"镜像拉取失败: {image_name} - {result.stderr}", 'image')
            return {"success": False, "message": f"拉取失败: {result.stderr}"}
    except subprocess.TimeoutExpired:
        log_service.error(f"镜像拉取超时: {image_name}", 'image')
        return {"success": False, "message": "拉取操作超时"}
    except FileNotFoundError:
        return {"success": False, "message": "Docker命令不可用"}
    except Exception as e:
        return {"success": False, "message": f"拉取失败: {str(e)}"}

import requests
import time

def test_connectivity(url: str, timeout: int = 10) -> Dict:
    """测试网络连通性"""
    try:
        start_time = time.time()
        response = requests.get(url, timeout=timeout, verify=True)
        latency = int((time.time() - start_time) * 1000)
        
        if response.status_code == 200:
            return {
                "success": True,
                "url": url,
                "status": "reachable",
                "latency": latency,
                "status_code": response.status_code,
                "message": "连接成功"
            }
        else:
            return {
                "success": False,
                "url": url,
                "status": "unreachable",
                "latency": latency,
                "status_code": response.status_code,
                "message": f"连接失败，HTTP状态码: {response.status_code}"
            }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "url": url,
            "status": "timeout",
            "latency": timeout * 1000,
            "message": "连接超时"
        }
    except requests.exceptions.SSLError:
        return {
            "success": False,
            "url": url,
            "status": "ssl_error",
            "latency": 0,
            "message": "SSL证书错误"
        }
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "url": url,
            "status": "connection_error",
            "latency": 0,
            "message": "连接失败，无法建立连接"
        }
    except Exception as e:
        return {
            "success": False,
            "url": url,
            "status": "error",
            "latency": 0,
            "message": f"测试异常: {str(e)}"
        }

def test_all_connectivity() -> Dict:
    """测试所有预设的网络连接"""
    targets = [
        {"name": "GitHub", "url": "https://github.com/"},
        {"name": "Docker Hub", "url": "https://hub.docker.com/"}
    ]
    
    results = []
    for target in targets:
        result = test_connectivity(target["url"])
        result["name"] = target["name"]
        results.append(result)
    
    all_successful = all(r["success"] for r in results)
    
    return {
        "success": all_successful,
        "results": results,
        "total_tests": len(results),
        "successful_tests": sum(1 for r in results if r["success"])
    }

def get_proxy_config() -> Dict:
    """获取当前代理配置"""
    return proxy_config.copy()

def set_proxy_config(http_proxy: str = "", https_proxy: str = "") -> Dict:
    """设置代理配置"""
    global proxy_config
    
    # 验证代理格式
    def validate_proxy(url: str) -> bool:
        if not url:
            return True
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.scheme in ('http', 'https') and parsed.hostname and parsed.port
        except:
            return False
    
    if http_proxy and not validate_proxy(http_proxy):
        log_service.error(f"代理配置失败: HTTP代理格式不正确", 'system')
        return {"success": False, "message": "HTTP代理格式不正确，请使用 http://ip:port 格式"}
    
    if https_proxy and not validate_proxy(https_proxy):
        log_service.error(f"代理配置失败: HTTPS代理格式不正确", 'system')
        return {"success": False, "message": "HTTPS代理格式不正确，请使用 https://ip:port 格式"}
    
    proxy_config["http_proxy"] = http_proxy.strip() if http_proxy else ""
    proxy_config["https_proxy"] = https_proxy.strip() if https_proxy else ""
    
    # 保存到数据库
    try:
        db_set_proxy_config(proxy_config["http_proxy"], proxy_config["https_proxy"])
    except Exception as e:
        print(f"保存代理配置到数据库失败: {e}")
    
    log_service.info(f"代理配置已更新: HTTP={http_proxy or '无'}, HTTPS={https_proxy or '无'}", 'system')
    
    return {"success": True, "message": "代理配置已保存"}

def get_docker_info():
    docker_version = ""
    docker_compose_version = ""
    
    try:
        result = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            docker_version = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        docker_version = "Docker 未安装或不可用"
    
    try:
        result = subprocess.run(["docker-compose", "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            docker_compose_version = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        docker_compose_version = "Docker Compose 未安装或不可用"
    
    return {
        "docker_version": docker_version,
        "docker_compose_version": docker_compose_version
    }

def get_host_system_info():
    info = {
        "cpu_usage": "0%",
        "memory_total": "0 MB",
        "memory_used": "0 MB",
        "memory_usage": "0%",
        "disk_total": "0 GB",
        "disk_used": "0 GB",
        "disk_usage": "0%",
        "os_version": "未知",
        "network_info": []
    }
    
    try:
        result = subprocess.run(["cat", "/proc/stat"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line.startswith('cpu '):
                    parts = line.split()
                    total = sum(int(p) for p in parts[1:])
                    idle = int(parts[4])
                    usage = ((total - idle) / total) * 100
                    info["cpu_usage"] = f"{usage:.1f}%"
                    break
    except Exception:
        try:
            result = subprocess.run(["ps", "-aux"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                info["cpu_usage"] = "获取中..."
        except Exception:
            info["cpu_usage"] = "无法获取"
    
    try:
        result = subprocess.run(["cat", "/proc/meminfo"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            mem_total = 0
            mem_available = 0
            for line in result.stdout.strip().split('\n'):
                if line.startswith('MemTotal:'):
                    mem_total = int(line.split()[1]) // 1024
                elif line.startswith('MemAvailable:'):
                    mem_available = int(line.split()[1]) // 1024
            if mem_total > 0:
                mem_used = mem_total - mem_available
                info["memory_total"] = f"{mem_total} MB"
                info["memory_used"] = f"{mem_used} MB"
                info["memory_usage"] = f"{(mem_used / mem_total * 100):.1f}%"
    except Exception:
        try:
            result = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if line.startswith('Mem:'):
                        parts = line.split()
                        info["memory_total"] = f"{parts[1]} MB"
                        info["memory_used"] = f"{parts[2]} MB"
                        info["memory_usage"] = f"{(int(parts[2]) / int(parts[1]) * 100):.1f}%"
                        break
        except Exception:
            pass
    
    try:
        result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                info["disk_total"] = parts[1]
                info["disk_used"] = parts[2]
                info["disk_usage"] = parts[4].replace('%', '') + '%'
    except Exception:
        try:
            result = subprocess.run(["df", "-H", "/"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if len(lines) >= 2:
                    parts = lines[1].split()
                    info["disk_total"] = parts[1]
                    info["disk_used"] = parts[2]
                    info["disk_usage"] = parts[4].replace('%', '') + '%'
        except Exception:
            pass
    
    try:
        result = subprocess.run(["cat", "/etc/os-release"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            os_info = {}
            for line in result.stdout.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    os_info[key] = value.strip('"')
            info["os_version"] = os_info.get('PRETTY_NAME', os_info.get('NAME', '未知'))
    except Exception:
        try:
            result = subprocess.run(["uname", "-a"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                info["os_version"] = result.stdout.strip()
        except Exception:
            pass
    
    try:
        result = subprocess.run(["ip", "addr"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            interfaces = []
            current_iface = None
            for line in result.stdout.strip().split('\n'):
                if line.startswith(' '):
                    if current_iface and 'inet ' in line:
                        ip = line.split('inet ')[1].split('/')[0]
                        if not ip.startswith('127.'):
                            current_iface["ip"] = ip
                elif ':' in line:
                    if current_iface and current_iface.get("ip"):
                        interfaces.append(current_iface)
                    name = line.split(':')[1].strip()
                    current_iface = {"name": name, "ip": ""}
            if current_iface and current_iface.get("ip"):
                interfaces.append(current_iface)
            info["network_info"] = interfaces
    except Exception:
        try:
            result = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                interfaces = []
                current_iface = None
                for line in result.stdout.strip().split('\n'):
                    if line.strip() and not line.startswith(' '):
                        if current_iface and current_iface.get("ip"):
                            interfaces.append(current_iface)
                        name = line.split(':')[0].strip()
                        current_iface = {"name": name, "ip": ""}
                    elif current_iface and 'inet ' in line:
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part == 'inet' and i + 1 < len(parts):
                                ip = parts[i + 1]
                                if not ip.startswith('127.'):
                                    current_iface["ip"] = ip
                                break
                if current_iface and current_iface.get("ip"):
                    interfaces.append(current_iface)
                info["network_info"] = interfaces
        except Exception:
            pass
    
    return info

def get_latest_dockerhub_version(repo_name: str) -> Optional[str]:
    try:
        url = f"https://hub.docker.com/v2/repositories/{repo_name}/tags"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            tags = []
            for result in data.get('results', []):
                name = result.get('name')
                if name and name.startswith('v'):
                    tags.append(name)
            if tags:
                tags.sort(key=lambda v: tuple(map(int, v[1:].split('.'))))
                return tags[-1]
        return None
    except requests.exceptions.RequestException:
        return None
    except Exception:
        return None

def get_container_logs(container_id: str, tail: int = 100) -> str:
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(tail), container_id],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            return result.stdout
        else:
            return result.stderr
    except subprocess.TimeoutExpired:
        return "获取日志超时"
    except FileNotFoundError:
        return "Docker命令不可用"
    except Exception as e:
        return f"获取日志失败: {str(e)}"

# ============ 容器备份相关函数 ============

BACKUPS_DIR = Path("/app/backup")
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

VOLUMES_DIR = Path("/host/var/lib/docker/volumes")

from .database import (
    add_backup,
    get_all_backups,
    get_backups_by_container,
    delete_backup_by_id,
    get_backup_by_id as db_get_backup_by_id,
    update_backup_status
)

def get_container_mounts(container_id: str) -> list:
    """获取容器的挂载信息"""
    try:
        result = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)[0]
            mounts = data.get('Mounts', [])
            return mounts
        else:
            return []
    except Exception:
        return []

def save_image(container_id: str, backup_dir: Path) -> tuple:
    """保存容器镜像"""
    try:
        container_info = get_container_by_id(container_id)
        if not container_info:
            return False, "无法获取容器信息"
        
        image_name = container_info.get('image', '')
        if not image_name:
            return False, "无法获取容器镜像名称"
        
        image_path = backup_dir / "image.tar"
        
        result = subprocess.run(
            ["docker", "save", "-o", str(image_path), image_name],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            return True, str(image_path)
        else:
            return False, f"保存镜像失败: {result.stderr}"
    except subprocess.TimeoutExpired:
        return False, "保存镜像超时"
    except FileNotFoundError:
        return False, "Docker命令不可用"
    except Exception as e:
        return False, f"保存镜像异常: {str(e)}"

def export_config(container_id: str, backup_dir: Path) -> tuple:
    """导出容器配置"""
    try:
        config_path = backup_dir / "container-config.json"
        
        result = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            with open(config_path, 'w') as f:
                f.write(result.stdout)
            return True, str(config_path)
        else:
            return False, f"导出配置失败: {result.stderr}"
    except Exception as e:
        return False, f"导出配置异常: {str(e)}"

def pack_volumes(container_id: str, backup_dir: Path) -> tuple:
    """打包所有挂载卷"""
    try:
        mounts = get_container_mounts(container_id)
        if not mounts:
            return True, []
        
        packed_volumes = []
        
        for mount in mounts:
            mount_type = mount.get('Type', '')
            name = mount.get('Name', '')
            source = mount.get('Source', '')
            destination = mount.get('Destination', '')
            
            if not source:
                continue
            
            if mount_type == 'volume':
                volume_path = VOLUMES_DIR / name / "_data"
                if volume_path.exists():
                    volume_tar = backup_dir / f"volume-{name}.tar.gz"
                    result = subprocess.run(
                        ["tar", "-czvf", str(volume_tar), "-C", str(volume_path.parent), "_data"],
                        capture_output=True,
                        text=True,
                        timeout=300
                    )
                    if result.returncode == 0:
                        packed_volumes.append({
                            'name': name,
                            'type': 'volume',
                            'source': str(volume_path),
                            'destination': destination,
                            'file': str(volume_tar)
                        })
                    else:
                        log_service.warning(f"打包命名卷失败: {name} - {result.stderr}", 'backup')
            
            elif mount_type == 'bind':
                basename = os.path.basename(source)
                if not basename or basename == '/':
                    log_service.warning(f"跳过无效的绑定挂载: {source}", 'backup')
                    continue
                
                bind_tar = backup_dir / f"bind-{basename}.tar.gz"
                dirname = os.path.dirname(source)
                if not dirname or dirname == '/':
                    dirname = '/'
                
                host_source = Path("/host") / source.lstrip('/')
                if not host_source.exists():
                    log_service.warning(f"绑定挂载路径不存在: {host_source}", 'backup')
                    continue
                
                host_dirname = os.path.dirname(str(host_source))
                if not host_dirname or host_dirname == '/':
                    host_dirname = '/'
                
                result = subprocess.run(
                    ["tar", "-czvf", str(bind_tar), "-C", host_dirname, basename],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if result.returncode == 0:
                    packed_volumes.append({
                        'name': basename,
                        'type': 'bind',
                        'source': source,
                        'destination': destination,
                        'file': str(bind_tar)
                    })
                else:
                    log_service.warning(f"打包绑定挂载失败: {source} - {result.stderr}", 'backup')
        
        return True, packed_volumes
    except Exception as e:
        return False, f"打包卷异常: {str(e)}"

def create_container_backup(container_id: str) -> Dict:
    """创建容器完整备份"""
    try:
        container_info = get_container_by_id(container_id)
        if not container_info:
            return {"success": False, "message": "容器不存在"}
        
        container_name = container_info.get('name', '')
        backup_name = f"{container_name}-backup"
        
        timestamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
        backup_dir = BACKUPS_DIR / f"{backup_name}-{timestamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        log_service.info(f"开始创建容器备份: {container_name}", 'backup')
        
        was_running = container_info.get('status') == 'running'
        
        if was_running:
            subprocess.run(["docker", "stop", container_id], capture_output=True)
            log_service.info(f"备份前停止容器: {container_name}", 'backup')
        
        steps = []
        
        success, result = save_image(container_id, backup_dir)
        if not success:
            shutil.rmtree(backup_dir, ignore_errors=True)
            if was_running:
                subprocess.run(["docker", "start", container_id], capture_output=True)
            return {"success": False, "message": result}
        steps.append("镜像保存成功")
        log_service.info(f"镜像保存成功: {container_name}", 'backup')
        
        success, result = export_config(container_id, backup_dir)
        if not success:
            shutil.rmtree(backup_dir, ignore_errors=True)
            if was_running:
                subprocess.run(["docker", "start", container_id], capture_output=True)
            return {"success": False, "message": result}
        steps.append("配置导出成功")
        log_service.info(f"配置导出成功: {container_name}", 'backup')
        
        success, volumes = pack_volumes(container_id, backup_dir)
        if not success:
            shutil.rmtree(backup_dir, ignore_errors=True)
            if was_running:
                subprocess.run(["docker", "start", container_id], capture_output=True)
            return {"success": False, "message": volumes}
        steps.append(f"卷打包成功 ({len(volumes)} 个)")
        log_service.info(f"卷打包成功: {container_name} ({len(volumes)} 个)", 'backup')
        
        archive_path = BACKUPS_DIR / f"{backup_name}-{timestamp}.tar"
        
        result = subprocess.run(
            ["tar", "-cf", str(archive_path), "-C", str(BACKUPS_DIR), f"{backup_name}-{timestamp}"],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode != 0:
            shutil.rmtree(backup_dir, ignore_errors=True)
            return {"success": False, "message": f"归档失败: {result.stderr}"}
        
        shutil.rmtree(backup_dir, ignore_errors=True)
        
        if was_running:
            subprocess.run(["docker", "start", container_id], capture_output=True)
            log_service.info(f"备份完成后重启容器: {container_name}", 'backup')
        
        backup_size = os.path.getsize(archive_path)
        
        backup_id = add_backup(
            container_id=container_id,
            container_name=container_name,
            name=backup_name,
            file_path=str(archive_path),
            size=backup_size,
            status='success'
        )
        
        log_service.success(f"容器备份创建成功: {container_name}", 'backup')
        
        return {
            "success": True,
            "message": "备份创建成功",
            "data": {
                "id": backup_id,
                "name": backup_name,
                "container_name": container_name,
                "container_id": container_id,
                "file_path": str(archive_path),
                "size": backup_size,
                "created_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
                "steps": steps
            }
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "备份操作超时"}
    except Exception as e:
        log_service.error(f"容器备份失败: {container_id} - {str(e)}", 'backup')
        return {"success": False, "message": f"备份失败: {str(e)}"}

def get_all_backups_list() -> list:
    """获取所有备份列表"""
    try:
        return get_all_backups()
    except Exception as e:
        log_service.error(f"获取备份列表失败: {str(e)}", 'backup')
        return []

def get_backups_for_container(container_name: str) -> list:
    """获取指定容器的备份列表"""
    try:
        return get_backups_by_container(container_name)
    except Exception as e:
        log_service.error(f"获取容器备份列表失败: {container_name} - {str(e)}", 'backup')
        return []

def get_backup_by_id(backup_id: int) -> dict:
    """获取单个备份详情"""
    try:
        return db_get_backup_by_id(backup_id)
    except Exception as e:
        log_service.error(f"获取备份详情失败: {backup_id} - {str(e)}", 'backup')
        return None

def remove_backup(backup_id: int) -> Dict:
    """删除备份"""
    try:
        success, file_path = delete_backup_by_id(backup_id)
        
        if success and file_path and os.path.exists(file_path):
            os.remove(file_path)
        
        if success:
            log_service.warning(f"备份已删除: ID={backup_id}", 'backup')
            return {"success": True, "message": "备份删除成功"}
        else:
            return {"success": False, "message": "备份不存在"}
    except Exception as e:
        log_service.error(f"删除备份失败: {backup_id} - {str(e)}", 'backup')
        return {"success": False, "message": f"删除失败: {str(e)}"}

def restore_backup(backup_id: int) -> Dict:
    """恢复备份"""
    try:
        backup = get_backup_by_id(backup_id)
        if not backup:
            return {"success": False, "message": "备份不存在"}
        
        archive_path = backup.get('file_path', '')
        if not archive_path or not os.path.exists(archive_path):
            return {"success": False, "message": "备份文件不存在"}
        
        container_name = backup.get('container_name', '')
        
        restore_dir = BACKUPS_DIR / f"restore-{container_name}-{datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')}"
        restore_dir.mkdir(parents=True, exist_ok=True)
        
        result = subprocess.run(
            ["tar", "-xf", archive_path, "-C", str(restore_dir)],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode != 0:
            shutil.rmtree(restore_dir, ignore_errors=True)
            return {"success": False, "message": f"解压备份失败: {result.stderr}"}
        
        image_path = restore_dir / f"{container_name}-backup" / "image.tar"
        config_path = restore_dir / f"{container_name}-backup" / "container-config.json"
        
        if image_path.exists():
            result = subprocess.run(
                ["docker", "load", "-i", str(image_path)],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                shutil.rmtree(restore_dir, ignore_errors=True)
                return {"success": False, "message": f"加载镜像失败: {result.stderr}"}
            log_service.info(f"镜像加载成功: {container_name}", 'backup')
        
        shutil.rmtree(restore_dir, ignore_errors=True)
        
        log_service.success(f"容器备份恢复成功: {container_name}", 'backup')
        
        return {
            "success": True,
            "message": "备份恢复成功",
            "data": {
                "container_name": container_name
            }
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "恢复操作超时"}
    except Exception as e:
        log_service.error(f"恢复备份失败: {backup_id} - {str(e)}", 'backup')
        return {"success": False, "message": f"恢复失败: {str(e)}"}