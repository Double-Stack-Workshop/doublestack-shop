import subprocess
import os
from pathlib import Path
from typing import List, Dict, Optional
from .schemas import YmlFile, RepoInfo

REPOS_DIR = Path("./repos")
REPOS_DIR.mkdir(exist_ok=True)

repos_db: List[RepoInfo] = []

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

def scan_yml_files(repo_dir: Path) -> List[YmlFile]:
    yml_files = []
    
    for yml_path in repo_dir.rglob("*.yml"):
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
            
    for yml_path in repo_dir.rglob("*.yaml"):
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
    repo_name = name if name else get_repo_name_from_url(repo_url)
    
    for repo in repos_db:
        if repo.name == repo_name:
            return {"success": False, "message": "仓库已存在", "status": "error"}
    
    result = clone_or_pull_repo(repo_url, branch, local_path)
    
    if not result["success"]:
        return result
    
    repo_dir = Path(result["path"])
    yml_files = scan_yml_files(repo_dir)
    
    repo_info = RepoInfo(
        name=repo_name,
        url=repo_url,
        branch=branch,
        local_path=local_path,
        yml_files=yml_files,
        last_sync="刚刚",
        status="active"
    )
    repos_db.append(repo_info)
    
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
    for i, repo in enumerate(repos_db):
        if repo.name == repo_name:
            result = clone_or_pull_repo(repo.url, repo.branch, repo.local_path)
            
            if result["success"]:
                repo_dir = Path(result["path"])
                repo.yml_files = scan_yml_files(repo_dir)
                repo.status = "active"
                repo.last_sync = "刚刚"
                repos_db[i] = repo
                
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
                return result
    
    return {"success": False, "message": "仓库不存在", "status": "error"}

def get_repo(repo_name: str) -> Optional[Dict]:
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
    for repo in repos_db:
        if repo.name == repo_name:
            repo_dir = REPOS_DIR / repo_name
            for yml_file in repo.yml_files:
                if yml_file.path == file_path or yml_file.name == file_path:
                    file_full_path = repo_dir / yml_file.path
                    if file_full_path.exists():
                        import os
                        mtime = file_full_path.stat().st_mtime
                        from datetime import datetime
                        last_modified = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
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
    for repo in repos_db:
        if repo.name == repo_name:
            return [
                {"name": f.name, "path": f.path}
                for f in repo.yml_files
            ]
    return None

def save_file_content(repo_name: str, file_name: str, content: str) -> bool:
    for repo in repos_db:
        if repo.name == repo_name:
            repo_dir = REPOS_DIR / repo_name
            for i, yml_file in enumerate(repo.yml_files):
                if yml_file.name == file_name:
                    file_path = repo_dir / yml_file.path
                    try:
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(content)
                        repo.yml_files[i].content = content
                        return True
                    except Exception as e:
                        print(f"保存文件失败: {e}")
                        return False
    return False

def delete_repo(repo_name: str) -> bool:
    for i, repo in enumerate(repos_db):
        if repo.name == repo_name:
            repos_db.pop(i)
            return True
    return False

def deploy_yml(repo_name: str, file_path: str) -> Optional[Dict]:
    from .database import add_deployment
    
    for repo in repos_db:
        if repo.name == repo_name:
            for yml_file in repo.yml_files:
                if yml_file.path == file_path or yml_file.name == file_path:
                    repo_dir = REPOS_DIR / repo_name
                    yml_full_path = repo_dir / yml_file.path
                    
                    try:
                        result = subprocess.run(
                            ["docker-compose", "-f", str(yml_full_path), "up", "-d"],
                            capture_output=True,
                            text=True,
                            timeout=120
                        )
                        
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
                            
                            return {
                                "success": True,
                                "message": f"部署成功: {yml_file.name}",
                                "data": {
                                    "repo_name": repo_name,
                                    "file_name": yml_file.name,
                                    "file_path": yml_file.path,
                                    "status": "deployed",
                                    "output": result.stdout,
                                    "container_id": container_id,
                                    "container_name": container_name
                                }
                            }
                        else:
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
                import datetime
                started_at = datetime.datetime.fromisoformat(data['State']['StartedAt'].replace('Z', '+00:00'))
                now = datetime.datetime.now(datetime.timezone.utc)
                delta = now - started_at
                
                days = delta.days
                hours = delta.seconds // 3600
                minutes = (delta.seconds % 3600) // 60
                
                if days > 0:
                    uptime = f'{days}天{hours}小时'
                elif hours > 0:
                    uptime = f'{hours}小时{minutes}分钟'
                else:
                    uptime = f'{minutes}分钟'
            
            ports = []
            if 'Ports' in data['NetworkSettings']:
                for port in data['NetworkSettings']['Ports']:
                    if port['PublicPort']:
                        ports.append(f"{port['PublicPort']}->{port['PrivatePort']}/{port['Type']}")
            
            return {
                'id': data['Id'],
                'name': data['Name'].lstrip('/'),
                'image': data['Config']['Image'],
                'state': state,
                'status': data['State']['Status'],
                'uptime': uptime,
                'ports': ports,
                'created_at': data['Created'],
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
        return result.returncode == 0
    except subprocess.TimeoutExpired:
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
        return result.returncode == 0
    except subprocess.TimeoutExpired:
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
        return result.returncode == 0
    except subprocess.TimeoutExpired:
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
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except FileNotFoundError:
        return False
    except Exception:
        return False

def get_all_images() -> list:
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
            return images
        else:
            return []
    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        return []
    except Exception:
        return []

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
            return {"success": True, "message": "镜像删除成功"}
        else:
            return {"success": False, "message": f"删除失败: {result.stderr}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "删除操作超时"}
    except FileNotFoundError:
        return {"success": False, "message": "Docker命令不可用"}
    except Exception as e:
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
            return {"success": True, "message": f"镜像拉取成功: {image_name}"}
        else:
            return {"success": False, "message": f"拉取失败: {result.stderr}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "拉取操作超时"}
    except FileNotFoundError:
        return {"success": False, "message": "Docker命令不可用"}
    except Exception as e:
        return {"success": False, "message": f"拉取失败: {str(e)}"}

import requests

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