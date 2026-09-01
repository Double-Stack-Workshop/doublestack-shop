import hashlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import database, services


INITIAL_PASSWORD = 'Initial.Admin123!'
NEW_PASSWORD = 'Changed.Admin456!'


class FirstLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 测试真实路由和鉴权；仅隔离宿主终端与容器内静态文件路径。
        root = Path(__file__).resolve().parents[1]
        cls.terminal = Mock()
        terminal_module = types.ModuleType('backend.app.terminal')
        terminal_module.terminal_manager = cls.terminal
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {'backend.app.terminal': terminal_module}))
            stack.enter_context(patch.object(database, 'init_db'))
            stack.enter_context(patch('fastapi.staticfiles.StaticFiles', side_effect=lambda **_kwargs: StaticFiles(directory=root / 'frontend/src')))
            spec = importlib.util.spec_from_file_location('backend.app._auth_test_main', root / 'backend/app/main.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls.app = module.app
            cls.routes_module = sys.modules['backend.app.routes']
            cls.login_attempts = cls.routes_module._login_attempts

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(database, 'DATABASE_PATH', str(Path(self.temp.name) / 'app.db'))
        self.path_patch.start()
        with patch.object(database, 'generate_strong_password', return_value=INITIAL_PASSWORD), patch('builtins.print'):
            database.init_db()
        self.login_attempts.clear()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.path_patch.stop()
        self.temp.cleanup()

    def login(self, password=INITIAL_PASSWORD):
        return self.client.post('/api/login', json={'username': 'admin', 'password': password})

    def change_password(self, new_password=NEW_PASSWORD):
        return self.client.post('/api/change-password', json={
            'new_password': new_password,
        })

    def test_initial_login_blocks_business_and_terminal_but_allows_logout(self):
        result = self.login().json()
        self.assertTrue(result['data']['is_admin'])
        self.assertTrue(result['data']['must_change_password'])
        self.assertTrue(self.client.get('/api/me').json()['data']['must_change_password'])
        for path in ('/api/users', '/api/dashboard/stats', '/api/containers', '/api/logs'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()['code'], 'PASSWORD_CHANGE_REQUIRED')
        self.assertEqual(self.client.put('/api/users/admin', json={'password': NEW_PASSWORD}).status_code, 403)
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect('/api/ws/terminal'):
                pass
        self.assertEqual(caught.exception.code, 1008)
        self.terminal.create_host_terminal.assert_not_called()
        self.assertTrue(self.client.post('/api/logout').json()['success'])
        self.assertEqual(self.client.get('/api/me').status_code, 401)

    def test_admin_can_manage_local_yml_after_initial_password_change(self):
        self.login()
        self.assertTrue(self.change_password().json()['success'])
        self.assertTrue(self.login(NEW_PASSWORD).json()['success'])
        local_repos = Path(self.temp.name) / 'repos'
        with patch.object(services, 'REPOS_DIR', local_repos):
            created = self.client.post('/api/local-yml', json={
                'file_name': 'compose.yml', 'content': 'services:\n',
            })
            self.assertEqual(created.status_code, 200)
            self.assertEqual(self.client.get('/api/local-yml').json()[0]['name'], 'compose.yml')
            renamed = self.client.put('/api/local-yml/compose.yml', json={
                'file_name': 'renamed.yaml', 'content': 'services:\n  app: {}\n',
            })
            self.assertEqual(renamed.status_code, 200)
            self.assertEqual(renamed.json()['file_name'], 'renamed.yaml')
            self.assertEqual(self.client.get('/api/local-yml/compose.yml').status_code, 404)
            self.assertEqual(
                self.client.get('/api/local-yml/renamed.yaml').json()['content'],
                'services:\n  app: {}\n',
            )

    def test_initial_password_cannot_authorize_public_endpoints(self):
        register = self.client.post('/api/register', json={
            'username': 'bypass', 'password': NEW_PASSWORD, 'admin_password': INITIAL_PASSWORD,
        })
        self.assertFalse(register.json()['success'])
        self.assertIsNone(database.get_user_by_username('bypass'))
        reset = self.client.post('/api/users/forgot-password', json={
            'admin_password': INITIAL_PASSWORD, 'new_password': NEW_PASSWORD,
        })
        self.assertFalse(reset.json()['success'])
        self.assertTrue(database.get_user_by_username('admin')['must_change_password'])

    def test_change_requires_session_and_valid_distinct_password(self):
        self.assertEqual(self.change_password().status_code, 401)
        self.login()
        for new_password in (INITIAL_PASSWORD, 'short', '密' * 25):
            with self.subTest(new_password=new_password):
                self.assertEqual(self.change_password(new_password).status_code, 400)
                self.assertTrue(self.client.get('/api/me').json()['data']['must_change_password'])

    def test_success_revokes_all_sessions_and_survives_restart(self):
        self.login()
        admin = database.get_user_by_username('admin')
        old_token = self.client.cookies.get('session_token')
        other_token, _ = database.create_user_session(admin['id'])
        self.assertTrue(self.change_password().json()['success'])
        self.assertIsNone(self.client.cookies.get('session_token'))
        self.assertIsNone(database.get_user_by_session(old_token))
        self.assertIsNone(database.get_user_by_session(other_token))
        self.assertFalse(self.login().json()['success'])
        self.assertFalse(database.verify_admin_password(INITIAL_PASSWORD))
        self.assertTrue(database.verify_admin_password(NEW_PASSWORD))
        with patch('builtins.print') as output:
            database.init_db()
        output.assert_not_called()
        self.assertFalse(self.login(NEW_PASSWORD).json()['data']['must_change_password'])
        self.assertEqual(self.client.get('/api/users').status_code, 200)
        result = self.client.post('/api/register', json={
            'username': 'member', 'password': NEW_PASSWORD, 'admin_password': NEW_PASSWORD,
        })
        self.assertTrue(result.json()['success'])
        self.assertFalse(database.get_user_by_username('member')['must_change_password'])

    def test_password_hash_upgrade_does_not_remove_initial_restriction(self):
        with database.db_connection() as conn:
            conn.execute('UPDATE users SET password = ? WHERE username = ?',
                         (hashlib.sha256(INITIAL_PASSWORD.encode()).hexdigest(), 'admin'))
        self.assertTrue(self.login().json()['data']['must_change_password'])
        admin = database.get_user_by_username('admin')
        self.assertTrue(admin['password'].startswith('$2'))
        self.assertTrue(admin['must_change_password'])
        self.assertEqual(self.client.get('/api/users').status_code, 403)

    def test_invalid_password_change_attempts_are_limited(self):
        self.login()
        for _ in range(5):
            self.assertEqual(self.change_password(new_password=INITIAL_PASSWORD).status_code, 400)
        self.assertEqual(self.change_password().status_code, 429)

    def test_completed_admin_cannot_reuse_initial_password_endpoint(self):
        self.login()
        self.assertEqual(self.change_password().status_code, 200)
        self.login(NEW_PASSWORD)
        self.assertEqual(self.change_password('Another.Admin789!').status_code, 403)
        self.assertTrue(database.verify_admin_password(NEW_PASSWORD))
        with self.assertRaises(ValueError):
            database.change_initial_password('admin', 'Another.Admin789!')

    def test_regular_user_cannot_use_initial_password_endpoint(self):
        database.create_user('member', NEW_PASSWORD)
        self.client.post('/api/login', json={'username': 'member', 'password': NEW_PASSWORD})
        self.assertEqual(self.change_password('Another.Member789!').status_code, 403)
        member = database.get_user_by_username('member')
        self.assertTrue(database.verify_password(NEW_PASSWORD, member['password']))

    def test_admin_password_verification_uses_current_database_value(self):
        self.login()
        self.change_password()
        database.update_user('admin', password='Another.Admin789!')
        self.assertFalse(database.verify_admin_password(NEW_PASSWORD))
        self.assertTrue(database.verify_admin_password('Another.Admin789!'))

    def test_admin_can_rename_self_and_last_login_is_returned(self):
        self.login()
        self.change_password()
        login = self.login(NEW_PASSWORD)
        self.assertIsNotNone(login.json()['data']['last_login_at'])
        response = self.client.put('/api/users/admin', json={'username': 'owner'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/me').json()['data']['username'], 'owner')
        users = self.client.get('/api/users').json()
        self.assertEqual(users[0]['username'], 'owner')
        self.assertIsNotNone(users[0]['last_login_at'])
        self.assertTrue(database.verify_admin_password(NEW_PASSWORD))

    def test_created_user_can_receive_an_uploaded_avatar(self):
        self.login()
        self.change_password()
        self.login(NEW_PASSWORD)
        self.assertEqual(self.client.post('/api/users', json={
            'username': 'member', 'password': 'Member.Password123!',
        }).status_code, 200)
        avatar_dir = Path(self.temp.name) / 'avatars'
        avatar_dir.mkdir()
        with patch.object(self.routes_module, '_AVATAR_DIR', avatar_dir):
            uploaded = self.client.post('/api/users/member/avatar', files={
                'file': ('avatar.png', b'\x89PNG\r\n\x1a\nimage-data', 'image/png'),
            })
            self.assertEqual(uploaded.status_code, 200)
            avatar = self.client.get('/api/users/member/avatar')
            self.assertEqual(avatar.status_code, 200)
            self.assertEqual(avatar.content, b'\x89PNG\r\n\x1a\nimage-data')


class LegacyAccountMigrationTests(unittest.TestCase):
    def test_existing_accounts_are_preserved_and_admin_is_flagged_only_once(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'legacy.db')
            password_hash = database.hash_password(INITIAL_PASSWORD)
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('''CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL, email TEXT, is_admin INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )''')
                conn.executemany('INSERT INTO users VALUES (?, ?, ?, NULL, ?, ?, ?)', [
                    (1, 'admin', password_hash, 1, 'old', 'old'),
                    (2, 'member', password_hash, 0, 'old', 'old'),
                ])
                conn.commit()
            with patch.object(database, 'DATABASE_PATH', path), patch('builtins.print'):
                database.init_db()
                self.assertEqual(database.get_user_by_username('admin')['password'], password_hash)
                self.assertTrue(database.get_user_by_username('admin')['must_change_password'])
                self.assertFalse(database.get_user_by_username('member')['must_change_password'])
                database.init_db()
                self.assertTrue(database.get_user_by_username('admin')['must_change_password'])
                database.change_initial_password('admin', NEW_PASSWORD)
                database.init_db()
                self.assertFalse(database.get_user_by_username('admin')['must_change_password'])
                self.assertEqual(len(database.get_all_users()), 2)

    def test_nonempty_database_does_not_gain_a_new_default_account(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(database, 'DATABASE_PATH', os.path.join(temp, 'app.db')), patch('builtins.print'):
                database.init_db()
                database.create_user('owner', NEW_PASSWORD, is_admin=True)
                database.delete_user('admin')
                database.init_db()
                self.assertIsNone(database.get_user_by_username('admin'))
                self.assertEqual(len(database.get_all_users()), 1)
