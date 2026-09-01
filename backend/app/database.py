import sqlite3
import os
from datetime import datetime, timezone, timedelta
import string
import secrets
import hashlib
import hmac
import bcrypt
import json
from contextlib import contextmanager

DATABASE_PATH = "./data/app.db"

from .logger import log_service


@contextmanager
def db_connection():
    """统一 SQLite 连接配置；异常时回滚，结束时始终关闭连接。"""
    conn = sqlite3.connect(DATABASE_PATH, timeout=15)
    try:
        yield conn
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_json(value, default, context):
    """读取数据库 JSON 字段；损坏数据不会中断页面，同时留下可追踪日志。"""
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        log_service.warning(f"忽略损坏的 JSON 数据 ({context}): {exc}", 'system')
        return default

def get_utc8_now():
    """获取 UTC+8 时间"""
    return datetime.now(timezone(timedelta(hours=8)))

def get_utc8_now_str():
    """获取 UTC+8 时间字符串 (ISO 格式)"""
    return get_utc8_now().isoformat()

def init_db():
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
            updated_at TEXT NOT NULL,
            must_change_password INTEGER NOT NULL DEFAULT 0
        )
    ''')
    columns = {row[1] for row in cursor.execute('PRAGMA table_info(users)')}
    if 'must_change_password' not in columns:
        cursor.execute('ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0')
        # 旧版没有记录是否完成初始改密；保留密码，要求 admin 在升级后确认更新一次。
        cursor.execute("UPDATE users SET must_change_password = 1 WHERE username = 'admin' AND is_admin = 1")
    
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
    
    # 仓库配置运行时存储在 data/repos.json；清理旧版未使用的 SQLite 表。
    cursor.execute('DROP TABLE IF EXISTS repos')
    
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
        CREATE TABLE IF NOT EXISTS ai_conversations (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '新对话',
            messages TEXT NOT NULL DEFAULT '[]',
            warnings TEXT NOT NULL DEFAULT '[]',
            model TEXT NOT NULL DEFAULT '',
            draft TEXT NOT NULL DEFAULT '',
            request_token TEXT NOT NULL DEFAULT '',
            busy_until REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ai_conversations_owner ON ai_conversations(user_id, updated_at)')
    
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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    
    if count == 0:
        admin_password = generate_strong_password()
        hashed_password = hash_password(admin_password)
        now = get_utc8_now_str()
        
        cursor.execute('''
            INSERT INTO users (username, password, email, is_admin, created_at, updated_at, must_change_password)
            VALUES (?, ?, ?, 1, ?, ?, 1)
        ''', ('admin', hashed_password, 'admin@example.com', now, now))
        
        conn.commit()
        print("=== 初始管理员账号 ===")
        print("用户名: admin")
        print(f"密码: {admin_password}")
        print("首次登录必须修改管理员密码后才能使用系统。")
        print("=====================")
    conn.commit()
    conn.close()

def verify_admin_password(password):
    # 每次读取数据库，避免改密后缓存仍接受旧密码；初始密码不能授权注册或重置。
    admin = get_user_by_username('admin')
    return bool(admin and admin['is_admin'] and not admin['must_change_password']
                and verify_password(password, admin['password']))

def reset_admin_password(new_password):
    with db_connection() as conn:
        cursor = conn.cursor()
        hashed_password = hash_password(new_password)
        now = get_utc8_now_str()
        
        cursor.execute('''
            UPDATE users SET password = ?, updated_at = ? WHERE username = ?
        ''', (hashed_password, now, 'admin'))
        cursor.execute('''
            DELETE FROM user_sessions WHERE user_id = (
                SELECT id FROM users WHERE username = ?
            )
        ''', ('admin',))
        
    log_service.warning("管理员密码已重置", 'system')
    return True

def generate_strong_password(length=16):
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    special = "."
    
    all_chars = uppercase + lowercase + digits + special
    
    password = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
        secrets.choice(special)
    ]
    
    password += [secrets.choice(all_chars) for _ in range(length - 4)]
    secrets.SystemRandom().shuffle(password)
    
    return ''.join(password)

def hash_password(password):
    """使用 bcrypt 保存密码。"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password, hashed_password):
    """兼容验证旧 SHA-256 哈希；成功登录后由调用方升级。"""
    try:
        if str(hashed_password).startswith('$2'):
            return bcrypt.checkpw(password.encode(), hashed_password.encode())
        algorithm, raw_iterations, salt_hex, expected_hex = hashed_password.split('$', 3)
        if algorithm != 'pbkdf2_sha256':
            return False
        digest = hashlib.pbkdf2_hmac(
            'sha256', password.encode(), bytes.fromhex(salt_hex), int(raw_iterations)
        )
        return hmac.compare_digest(digest.hex(), expected_hex)
    except (AttributeError, TypeError, ValueError):
        # v2.0.7 及更早版本使用无盐 SHA-256；仅保留迁移期兼容。
        return hmac.compare_digest(
            hashlib.sha256(password.encode()).hexdigest(), str(hashed_password)
        )


def password_hash_needs_upgrade(password_hash):
    return not str(password_hash).startswith('$2')


def create_user_session(user_id, days=7):
    """创建可撤销的随机会话，并只在数据库保存令牌哈希。"""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = get_utc8_now()
    expires_at = now + timedelta(days=days)

    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM user_sessions WHERE expires_at <= ?', (now.isoformat(),))
        cursor.execute(
            '''INSERT INTO user_sessions (token_hash, user_id, expires_at, created_at)
               VALUES (?, ?, ?, ?)''',
            (token_hash, user_id, expires_at.isoformat(), now.isoformat()),
        )
    return token, int(days * 24 * 60 * 60)


def get_user_by_session(token):
    """根据会话令牌取得当前用户；无效或过期会话返回 None。"""
    if not token:
        return None

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM user_sessions WHERE expires_at <= ?', (get_utc8_now_str(),))
        cursor.execute(
            '''SELECT u.id, u.username, u.email, u.is_admin, u.created_at, u.updated_at, u.must_change_password
               FROM user_sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ?''',
            (token_hash, get_utc8_now_str()),
        )
        user = cursor.fetchone()

    if not user:
        return None
    return {
        'id': user[0], 'username': user[1], 'email': user[2],
        'is_admin': bool(user[3]), 'created_at': user[4], 'updated_at': user[5],
        'must_change_password': bool(user[6]),
    }


def delete_user_session(token):
    if not token:
        return
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db_connection() as conn:
        conn.execute('DELETE FROM user_sessions WHERE token_hash = ?', (token_hash,))

def get_user_by_username(username):
    with db_connection() as conn:
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    
    if user:
        return {
            'id': user[0],
            'username': user[1],
            'password': user[2],
            'email': user[3],
            'is_admin': bool(user[4]),
            'created_at': user[5],
            'updated_at': user[6],
            'must_change_password': bool(user[7]),
        }
    return None

def get_user_by_email(email):
    with db_connection() as conn:
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    
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
    try:
        hashed_password = hash_password(password)
        now = get_utc8_now_str()
        with db_connection() as conn:
            conn.execute('''
                INSERT INTO users (username, password, email, is_admin, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (username, hashed_password, email, int(is_admin), now, now))
        
        log_service.success(f"用户创建成功: {username} (管理员: {is_admin})", 'system')
        
        return True
    except sqlite3.IntegrityError:
        log_service.warning(f"用户创建失败: {username} - 用户已存在", 'system')
        return False

def update_user(username, password=None, email=None):
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
        
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f'''
                UPDATE users SET {", ".join(updates)} WHERE username = ?
            ''', params)
        changed = cursor.rowcount > 0
        if password:
            cursor.execute('''DELETE FROM user_sessions WHERE user_id = (
                SELECT id FROM users WHERE username = ?
            )''', (username,))
    if changed:
        log_service.info(f"用户信息已更新: {username} (密码: {password is not None}, 邮箱: {email is not None})", 'system')
    return changed


def change_initial_password(username, new_password):
    """已登录管理员完成首次改密，原子解除限制并撤销全部会话。"""
    if len(new_password) < 8 or len(new_password.encode('utf-8')) > 72:
        raise ValueError('新密码至少为 8 位，且 UTF-8 编码不能超过 72 字节')
    user = get_user_by_username(username)
    if not user or not user['is_admin'] or not user['must_change_password']:
        raise ValueError('该账号无需首次修改管理员密码')
    if verify_password(new_password, user['password']):
        raise ValueError('新密码不能与当前密码相同')
    hashed_password = hash_password(new_password)
    with db_connection() as conn:
        cursor = conn.execute('''
            UPDATE users SET password = ?, must_change_password = 0, updated_at = ?
            WHERE id = ? AND password = ? AND must_change_password = 1 AND is_admin = 1
        ''', (hashed_password, get_utc8_now_str(), user['id'], user['password']))
        if cursor.rowcount != 1:
            raise ValueError('密码已发生变化，请重新登录')
        conn.execute('DELETE FROM user_sessions WHERE user_id = ?', (user['id'],))
    log_service.success(f"用户已修改密码: {username}", 'auth')

def delete_user(username):
    with db_connection() as conn:
        cursor = conn.execute('DELETE FROM users WHERE username = ?', (username,))
        success = cursor.rowcount > 0
    
    if success:
        log_service.warning(f"用户已删除: {username}", 'system')
    else:
        log_service.warning(f"删除用户失败: {username} - 用户不存在", 'system')
    
    return success

def get_all_users():
    with db_connection() as conn:
        users = conn.execute('SELECT * FROM users').fetchall()
    
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
    with db_connection() as conn:
        cursor = conn.cursor()
        now = get_utc8_now_str()
        cursor.execute('''
            INSERT INTO deployments (repo_name, file_name, container_id, container_name, status, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (repo_name, file_name, container_id, container_name, status, message, now))
    return True

def get_all_deployments(limit=10):
    with db_connection() as conn:
        deployments = conn.execute(
            'SELECT * FROM deployments ORDER BY created_at DESC LIMIT ?', (limit,)
        ).fetchall()
    
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
    with db_connection() as conn:
        count = conn.execute(
            'SELECT COUNT(DISTINCT file_name) FROM deployments WHERE status = ?', ('deployed',)
        ).fetchone()[0]
    return count

def get_deployment_success_rate():
    with db_connection() as conn:
        total = conn.execute('SELECT COUNT(*) FROM deployments').fetchone()[0]
        if total == 0:
            return 0
        success = conn.execute(
            'SELECT COUNT(*) FROM deployments WHERE status = ?', ('deployed',)
        ).fetchone()[0]
    return round((success / total) * 100)

# ============ 操作日志相关函数 ============

def add_operation_log(level, message, log_type='system', details=None):
    now = get_utc8_now_str()
    details_json = json.dumps(details) if details else None
    try:
        with db_connection() as conn:
            conn.execute('''
                INSERT INTO operation_logs (level, message, type, details, timestamp)
                VALUES (?, ?, ?, ?, ?)
            ''', (level, message, log_type, details_json, now))
    except sqlite3.Error as exc:
        # 日志写入不能反向中断业务请求，但必须保留诊断信息。
        print(f"写入操作日志失败: {exc}")

def get_operation_logs(level=None, log_type=None, limit=100, offset=0):
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
        with db_connection() as conn:
            logs = conn.execute(query, params).fetchall()
    except sqlite3.Error as exc:
        print(f"读取操作日志失败: {exc}")
        logs = []
    result = []
    for log in logs:
        details = load_json(log[4], None, f"operation_log:{log[0]}")
        
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
    try:
        with db_connection() as conn:
            conn.execute('DELETE FROM operation_logs')
    except sqlite3.Error as exc:
        print(f"清空操作日志失败: {exc}")

# ============ 设置相关函数 ============

def get_setting(key, default=None):
    try:
        with db_connection() as conn:
            result = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    except sqlite3.Error as exc:
        print(f"读取设置 {key} 失败: {exc}")
        result = None
    
    if result:
        return result[0]
    return default

def set_setting(key, value):
    now = get_utc8_now_str()
    try:
        with db_connection() as conn:
            conn.execute('''
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            ''', (key, value, now))
    except sqlite3.Error as exc:
        print(f"保存设置 {key} 失败: {exc}")

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
    try:
        with db_connection() as conn:
            images = conn.execute('SELECT * FROM images_cache ORDER BY cached_at DESC').fetchall()
    except sqlite3.OperationalError:
        images = []
    result = []
    for img in images:
        repo_tags = load_json(img[3], [], f"image_cache:{img[0]}")
        
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
    now = get_utc8_now_str()
    try:
        with db_connection() as conn:
            conn.execute('DELETE FROM images_cache')
            for img in images:
                conn.execute('''
                    INSERT INTO images_cache (id, name, tag, repo_tags, size, created_since, created_at, cached_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (img['id'], img['name'], img['tag'], json.dumps(img.get('repo_tags', [])),
                      img.get('size', 0), img.get('created_since', ''), img.get('created_at', ''), now))
    except sqlite3.OperationalError:
        log_service.warning("镜像缓存表不可用，跳过本次缓存更新", 'system')

def clear_images_cache():
    try:
        with db_connection() as conn:
            conn.execute('DELETE FROM images_cache')
    except sqlite3.OperationalError:
        log_service.warning("镜像缓存表不可用，跳过清理", 'system')

# ============ 备份相关函数 ============

def add_backup(container_id, container_name, name, file_path, size=0, status='success'):
    with db_connection() as conn:
        cursor = conn.cursor()
        now = get_utc8_now_str()
        cursor.execute('''
            INSERT INTO backups (container_id, container_name, name, file_path, size, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (container_id, container_name, name, file_path, size, status, now))
        
        backup_id = cursor.lastrowid
    return backup_id

def get_all_backups():
    try:
        with db_connection() as conn:
            backups = conn.execute('SELECT * FROM backups ORDER BY created_at DESC').fetchall()
    except sqlite3.OperationalError:
        backups = []
    
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
    try:
        with db_connection() as conn:
            backups = conn.execute(
                'SELECT * FROM backups WHERE container_name = ? ORDER BY created_at DESC', (container_name,)
            ).fetchall()
    except sqlite3.OperationalError:
        backups = []
    
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
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT file_path FROM backups WHERE id = ?', (backup_id,))
        result = cursor.fetchone()
        file_path = result[0] if result else None
        
        cursor.execute('DELETE FROM backups WHERE id = ?', (backup_id,))
        success = cursor.rowcount > 0
    return success, file_path

def get_backup_by_id(backup_id):
    try:
        with db_connection() as conn:
            backup = conn.execute('SELECT * FROM backups WHERE id = ?', (backup_id,)).fetchone()
    except sqlite3.OperationalError:
        backup = None
    
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
    with db_connection() as conn:
        cursor = conn.cursor()
        updates = []
        params = []
        
        updates.append("status = ?")
        params.append(status)
        
        if size > 0:
            updates.append("size = ?")
            params.append(size)
        
        params.append(backup_id)
        
        cursor.execute(f'UPDATE backups SET {", ".join(updates)} WHERE id = ?', params)
        success = cursor.rowcount > 0
    return success
