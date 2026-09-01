import builtins
import os
import tempfile
import unittest

from backend.app import database


class DatabaseSecurityTests(unittest.TestCase):
    def setUp(self):
        self.file = tempfile.NamedTemporaryFile(delete=False)
        self.file.close()
        self.original_path = database.DATABASE_PATH
        database.DATABASE_PATH = self.file.name
        original_print = builtins.print
        builtins.print = lambda *_args, **_kwargs: None
        try:
            database.init_db()
        finally:
            builtins.print = original_print

    def tearDown(self):
        database.DATABASE_PATH = self.original_path
        os.unlink(self.file.name)

    def test_new_password_hash_uses_salt_and_verifies(self):
        first = database.hash_password("correct horse battery staple")
        second = database.hash_password("correct horse battery staple")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("$2"))
        self.assertTrue(database.verify_password("correct horse battery staple", first))
        self.assertFalse(database.verify_password("wrong password", first))

    def test_session_can_be_revoked(self):
        admin = database.get_user_by_username("admin")
        token, _ = database.create_user_session(admin["id"])
        self.assertEqual(database.get_user_by_session(token)["username"], "admin")
        database.delete_user_session(token)
        self.assertIsNone(database.get_user_by_session(token))

    def test_username_login_time_and_avatar_are_persisted(self):
        admin = database.get_user_by_username("admin")
        token, _ = database.create_user_session(admin["id"])
        login_time = database.record_user_login(admin["id"])
        self.assertTrue(database.update_user("admin", new_username="owner"))
        self.assertEqual(database.get_user_by_session(token)["username"], "owner")
        self.assertEqual(database.get_user_by_username("owner")["last_login_at"], login_time)
        self.assertTrue(database.set_user_avatar("owner", "avatar.png"))
        self.assertEqual(database.get_user_by_username("owner")["avatar_filename"], "avatar.png")

    def test_username_cannot_be_renamed_to_an_existing_account(self):
        self.assertTrue(database.create_user("member", "Member.Password123!"))
        with self.assertRaisesRegex(ValueError, "用户名已存在"):
            database.update_user("admin", new_username="member")

    def test_settings_round_trip(self):
        database.set_setting("http_proxy", "http://proxy.example:8080")
        self.assertEqual(database.get_setting("http_proxy"), "http://proxy.example:8080")

    def test_deployment_cache_and_backup_round_trip(self):
        self.assertTrue(database.add_deployment("demo", "compose.yml", status="deployed"))
        self.assertEqual(database.get_deployed_apps_count(), 1)

        database.update_images_cache([{"id": "sha256:demo", "name": "demo", "tag": "latest"}])
        self.assertEqual(database.get_images_cache()[0]["repo_tags"], [])
        backup_id = database.add_backup("container", "demo", "demo.tar", "/backup/demo.tar")
        self.assertEqual(database.get_backup_by_id(backup_id)["name"], "demo.tar")
        self.assertTrue(database.update_backup_status(backup_id, "success", 42))
        self.assertTrue(database.delete_backup_by_id(backup_id)[0])
