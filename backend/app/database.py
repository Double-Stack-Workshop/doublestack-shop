import sqlite3
import os
from datetime import datetime, timezone, timedelta
import random
import string

DATABASE_PATH = "./data/app.db"
ADMIN_PASSWORD_HASH = None

from .logger import log_service

def get_utc8_now():
    """获取 UTC+8 时间"""
    return datetime.now(timezone(timedelta(hours=8)))

def get_utc8_now_str():
    """获取 UTC+8 时间字符串 (ISO 格式)"""
    return get_utc8_now().isoformat()

def init_db():
    global ADMIN_PASSWORD_HASH
    
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            email TEXT,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deployments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_name TEXT NOT NULL,
            file_name TEXT NOT NULL,
            container_id TEXT,
            container_name TEXT,
            status TEXT NOT NULL,
            message TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            url TEXT NOT NULL,
            branch TEXT NOT NULL,
            local_path TEXT,
            repo_dir_name TEXT NOT NULL,
            yml_files TEXT,
            last_sync TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            type TEXT DEFAULT 'system',
            details TEXT,
            timestamp TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images_cache (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            tag TEXT,
            repo_tags TEXT,
            size INTEGER,
            created_since TEXT,
            created_at TEXT,
            cached_at TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            container_id TEXT NOT NULL,
            container_name TEXT NOT NULL,
            name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            size INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
    ''')
    
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    
    if count == 0:
        admin_password = generate_strong_password()
        hashed_password = hash_password(admin_password)
        ADMIN_PASSWORD_HASH = hashed_password
        now = get_utc8_now_str()
        
        cursor.execute('''
            INSERT INTO users (username, password, email, is_admin, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
        ''', ('admin', hashed_password, 'admin@example.com', now, now))
        
        conn.commit()
        print(f"=== 初始管理员账号 ===")
        print(f"用户名: admin")
        print(f"密码: {admin_password}")
        print(f"=====================")
    else:
        cursor.execute('SELECT password FROM users WHERE username = ?', ('admin',))
        result = cursor.fetchone()
        if result:
            ADMIN_PASSWORD_HASH = result[0]
    
    conn.close()

def verify_admin_password(password):
    if not ADMIN_PASSWORD_HASH:
        return False
    return hash_password(password) == ADMIN_PASSWORD_HASH

def reset_admin_password(new_password):
    global ADMIN_PASSWORD_HASH
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        hashed_password = hash_password(new_password)
        now = get_utc8_now_str()
        
        cursor.execute('''
            UPDATE users SET password = ?, updated_at = ? WHERE username = ?
        ''', (hashed_password, now, 'admin'))
        
        conn.commit()
        ADMIN_PASSWORD_HASH = hashed_password
        
        log_service.warning(f"管理员密码已重置", 'system')
        
        return True
    finally:
        conn.close()

def generate_strong_password(length=16):
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    special = "."
    
    all_chars = uppercase + lowercase + digits + special
    
    password = [
        random.choice(uppercase),
        random.choice(lowercase),
        random.choice(digits),
        random.choice(special)
    ]
    
    password += [random.choice(all_chars) for _ in range(length - 4)]
    random.shuffle(password)
    
    return ''.join(password)

def hash_password(password):
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed_password):
    return hash_password(password) == hashed_password

def get_user_by_username(username):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()
    
    conn.close()
    
    if user:
        return {
            'id': user[0],
            'username': user[1],
            'password': user[2],
            'email': user[3],
            'is_admin': bool(user[4]),
            'created_at': user[5],
            'updated_at': user[6]
        }
    return None

def get_user_by_email(email):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
    user = cursor.fetchone()
    
    conn.close()
    
    if user:
        return {
            'id': user[0],
            'username': user[1],
            'password': user[2],
            'email': user[3],
            'is_admin': bool(user[4]),
            'created_at': user[5],
            'updated_at': user[6]
        }
    return None

def create_user(username, password, email=None, is_admin=False):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        hashed_password = hash_password(password)
        now = get_utc8_now_str()
        
        cursor.execute('''
            INSERT INTO users (username, password, email, is_admin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (username, hashed_password, email, int(is_admin), now, now))
        
        conn.commit()
        
        log_service.success(f"用户创建成功: {username} (管理员: {is_admin})", 'system')
        
        return True
    except sqlite3.IntegrityError:
        log_service.warning(f"用户创建失败: {username} - 用户已存在", 'system')
        return False
    finally:
        conn.close()

def update_user(username, password=None, email=None):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        updates = []
        params = []
        
        if password:
            updates.append("password = ?")
            params.append(hash_password(password))
        
        if email:
            updates.append("email = ?")
            params.append(email)
        
        updates.append("updated_at = ?")
        params.append(get_utc8_now_str())
        params.append(username)
        
        if updates:
            cursor.execute(f'''
                UPDATE users SET {", ".join(updates)} WHERE username = ?
            ''', params)
            
            conn.commit()
            
            log_service.info(f"用户信息已更新: {username} (密码: {password is not None}, 邮箱: {email is not None})", 'system')
            
            return True
        return False
    finally:
        conn.close()

def delete_user(username):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM users WHERE username = ?', (username,))
    conn.commit()
    
    success = cursor.rowcount > 0
    conn.close()
    
    if success:
        log_service.warning(f"用户已删除: {username}", 'system')
    else:
        log_service.warning(f"删除用户失败: {username} - 用户不存在", 'system')
    
    return success

def get_all_users():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users')
    users = cursor.fetchall()
    
    conn.close()
    
    return [{
        'id': user[0],
        'username': user[1],
        'password': user[2],
        'email': user[3],
        'is_admin': bool(user[4]),
        'created_at': user[5],
        'updated_at': user[6]
    } for user in users]

def add_deployment(repo_name, file_name, container_id=None, container_name=None, status='deploying', message=''):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        now = get_utc8_now_str()
        cursor.execute('''
            INSERT INTO deployments (repo_name, file_name, container_id, container_name, status, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (repo_name, file_name, container_id, container_name, status, message, now))
        
        conn.commit()
        return True
    finally:
        conn.close()

def get_all_deployments(limit=10):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM deployments ORDER BY created_at DESC LIMIT ?
    ''', (limit,))
    
    deployments = cursor.fetchall()
    conn.close()
    
    return [{
        'id': d[0],
        'repo_name': d[1],
        'file_name': d[2],
        'container_id': d[3],
        'container_name': d[4],
        'status': d[5],
        'message': d[6],
        'created_at': d[7]
    } for d in deployments]

def get_deployed_apps_count():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(DISTINCT file_name) FROM deployments WHERE status = ?', ('deployed',))
    count = cursor.fetchone()[0]
    
    conn.close()
    return count

def get_deployment_success_rate():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM deployments')
    total = cursor.fetchone()[0]
    
    if total == 0:
        conn.close()
        return 0
    
    cursor.execute('SELECT COUNT(*) FROM deployments WHERE status = ?', ('deployed',))
    success = cursor.fetchone()[0]
    
    conn.close()
    return round((success / total) * 100)

# ============ 仓库相关函数 ============

def get_all_repos_from_db():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM repos ORDER BY created_at DESC')
        repos = cursor.fetchall()
    except sqlite3.OperationalError:
        repos = []
    
    conn.close()
    
    result = []
    for repo in repos:
        yml_files = []
        if repo[6]:
            import json
            try:
                yml_files = json.loads(repo[6])
            except:
                pass
        
        result.append({
            'id': repo[0],
            'name': repo[1],
            'url': repo[2],
            'branch': repo[3],
            'local_path': repo[4],
            'repo_dir_name': repo[5],
            'yml_files': yml_files,
            'last_sync': repo[7],
            'status': repo[8],
            'created_at': repo[9],
            'updated_at': repo[10]
        })
    return result

def add_repo_to_db(name, url, branch, local_path, repo_dir_name, yml_files, last_sync, status):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    import json
    now = get_utc8_now_str()
    yml_files_json = json.dumps([{'name': f.name, 'path': f.path, 'content': f.content} for f in yml_files])
    
    try:
        cursor.execute('''
            INSERT INTO repos (name, url, branch, local_path, repo_dir_name, yml_files, last_sync, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, url, branch, local_path, repo_dir_name, yml_files_json, last_sync, status, now, now))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def update_repo_in_db(name, yml_files=None, last_sync=None, status=None):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    import json
    updates = []
    params = []
    
    if yml_files is not None:
        updates.append("yml_files = ?")
        params.append(json.dumps([{'name': f.name, 'path': f.path, 'content': f.content} for f in yml_files]))
    
    if last_sync is not None:
        updates.append("last_sync = ?")
        params.append(last_sync)
    
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    
    updates.append("updated_at = ?")
    params.append(get_utc8_now_str())
    params.append(name)
    
    if updates:
        cursor.execute(f'UPDATE repos SET {", ".join(updates)} WHERE name = ?', params)
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

def delete_repo_from_db(name):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM repos WHERE name = ?', (name,))
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

def get_repo_by_name_from_db(name):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM repos WHERE name = ?', (name,))
        repo = cursor.fetchone()
    except sqlite3.OperationalError:
        repo = None
    
    conn.close()
    
    if repo:
        import json
        yml_files = []
        if repo[6]:
            try:
                yml_files = json.loads(repo[6])
            except:
                pass
        
        return {
            'id': repo[0],
            'name': repo[1],
            'url': repo[2],
            'branch': repo[3],
            'local_path': repo[4],
            'repo_dir_name': repo[5],
            'yml_files': yml_files,
            'last_sync': repo[7],
            'status': repo[8],
            'created_at': repo[9],
            'updated_at': repo[10]
        }
    return None

# ============ 操作日志相关函数 ============

def add_operation_log(level, message, log_type='system', details=None):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    import json
    now = get_utc8_now_str()
    details_json = json.dumps(details) if details else None
    
    try:
        cursor.execute('''
            INSERT INTO operation_logs (level, message, type, details, timestamp)
            VALUES (?, ?, ?, ?, ?)
        ''', (level, message, log_type, details_json, now))
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    conn.close()

def get_operation_logs(level=None, log_type=None, limit=100, offset=0):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    query = 'SELECT * FROM operation_logs WHERE 1=1'
    params = []
    
    if level:
        query += ' AND level = ?'
        params.append(level)
    
    if log_type:
        query += ' AND type = ?'
        params.append(log_type)
    
    query += ' ORDER BY id DESC LIMIT ? OFFSET ?'
    params.extend([limit, offset])
    
    try:
        cursor.execute(query, params)
        logs = cursor.fetchall()
    except sqlite3.OperationalError:
        logs = []
    
    conn.close()
    
    import json
    result = []
    for log in logs:
        details = None
        if log[4]:
            try:
                details = json.loads(log[4])
            except:
                pass
        
        result.append({
            'id': log[0],
            'level': log[1],
            'message': log[2],
            'type': log[3],
            'details': details,
            'timestamp': log[5]
        })
    return result

def clear_operation_logs():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM operation_logs')
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    conn.close()

# ============ 设置相关函数 ============

def get_setting(key, default=None):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        result = cursor.fetchone()
    except sqlite3.OperationalError:
        result = None
    
    conn.close()
    
    if result:
        return result[0]
    return default

def set_setting(key, value):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    now = get_utc8_now_str()
    try:
        cursor.execute('''
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        ''', (key, value, now))
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    conn.close()

def get_proxy_config():
    return {
        "http_proxy": get_setting("http_proxy", ""),
        "https_proxy": get_setting("https_proxy", "")
    }

def set_proxy_config(http_proxy="", https_proxy=""):
    set_setting("http_proxy", http_proxy)
    set_setting("https_proxy", https_proxy)

# ============ 镜像缓存相关函数 ============

def get_images_cache():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM images_cache ORDER BY cached_at DESC')
        images = cursor.fetchall()
    except sqlite3.OperationalError:
        images = []
    
    conn.close()
    
    import json
    result = []
    for img in images:
        repo_tags = []
        if img[3]:
            try:
                repo_tags = json.loads(img[3])
            except:
                pass
        
        result.append({
            'id': img[0],
            'name': img[1],
            'tag': img[2],
            'repo_tags': repo_tags,
            'size': img[4],
            'created_since': img[5],
            'created_at': img[6],
            'cached_at': img[7]
        })
    return result

def update_images_cache(images):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    import json
    now = get_utc8_now_str()
    
    try:
        cursor.execute('DELETE FROM images_cache')
        
        for img in images:
            repo_tags = json.dumps(img.get('repo_tags', []))
            cursor.execute('''
                INSERT INTO images_cache (id, name, tag, repo_tags, size, created_since, created_at, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (img['id'], img['name'], img['tag'], repo_tags, img.get('size', 0), 
                  img.get('created_since', ''), img.get('created_at', ''), now))
        
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    conn.close()

def clear_images_cache():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM images_cache')
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    conn.close()

# ============ 备份相关函数 ============

def add_backup(container_id, container_name, name, file_path, size=0, status='success'):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        now = get_utc8_now_str()
        cursor.execute('''
            INSERT INTO backups (container_id, container_name, name, file_path, size, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (container_id, container_name, name, file_path, size, status, now))
        
        conn.commit()
        backup_id = cursor.lastrowid
        return backup_id
    finally:
        conn.close()

def get_all_backups():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM backups ORDER BY created_at DESC')
        backups = cursor.fetchall()
    except sqlite3.OperationalError:
        backups = []
    
    conn.close()
    
    return [{
        'id': b[0],
        'container_id': b[1],
        'container_name': b[2],
        'name': b[3],
        'file_path': b[4],
        'size': b[5],
        'status': b[6],
        'created_at': b[7]
    } for b in backups]

def get_backups_by_container(container_name):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM backups WHERE container_name = ? ORDER BY created_at DESC', (container_name,))
        backups = cursor.fetchall()
    except sqlite3.OperationalError:
        backups = []
    
    conn.close()
    
    return [{
        'id': b[0],
        'container_id': b[1],
        'container_name': b[2],
        'name': b[3],
        'file_path': b[4],
        'size': b[5],
        'status': b[6],
        'created_at': b[7]
    } for b in backups]

def delete_backup_by_id(backup_id):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT file_path FROM backups WHERE id = ?', (backup_id,))
        result = cursor.fetchone()
        file_path = result[0] if result else None
        
        cursor.execute('DELETE FROM backups WHERE id = ?', (backup_id,))
        conn.commit()
        
        success = cursor.rowcount > 0
        return success, file_path
    finally:
        conn.close()

def get_backup_by_id(backup_id):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM backups WHERE id = ?', (backup_id,))
        backup = cursor.fetchone()
    except sqlite3.OperationalError:
        backup = None
    
    conn.close()
    
    if backup:
        return {
            'id': backup[0],
            'container_id': backup[1],
            'container_name': backup[2],
            'name': backup[3],
            'file_path': backup[4],
            'size': backup[5],
            'status': backup[6],
            'created_at': backup[7]
        }
    return None

def update_backup_status(backup_id, status, size=0):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        updates = []
        params = []
        
        updates.append("status = ?")
        params.append(status)
        
        if size > 0:
            updates.append("size = ?")
            params.append(size)
        
        params.append(backup_id)
        
        cursor.execute(f'UPDATE backups SET {", ".join(updates)} WHERE id = ?', params)
        conn.commit()
        
        return cursor.rowcount > 0
    finally:
        conn.close()