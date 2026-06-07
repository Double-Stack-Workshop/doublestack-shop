import sqlite3
import os
from datetime import datetime
import random
import string

DATABASE_PATH = "./data/users.db"
ADMIN_PASSWORD_HASH = None

from .logger import log_service

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
    
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    
    if count == 0:
        admin_password = generate_strong_password()
        hashed_password = hash_password(admin_password)
        ADMIN_PASSWORD_HASH = hashed_password
        now = datetime.now().isoformat()
        
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
        now = datetime.now().isoformat()
        
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
        now = datetime.now().isoformat()
        
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
        params.append(datetime.now().isoformat())
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
        now = datetime.utcnow().isoformat() + 'Z'
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