import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import services


class LocalYmlTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repos_directory = Path(self.temporary_directory.name) / "repos"
        self.repos_patch = patch.object(services, "REPOS_DIR", self.repos_directory)
        self.repos_patch.start()

    def tearDown(self):
        self.repos_patch.stop()
        self.temporary_directory.cleanup()

    def test_create_read_update_and_list_local_yml(self):
        created = services.create_local_yml("compose.yml", "services:\n  web: {}\n")

        self.assertEqual(created["file_name"], "compose.yml")
        self.assertEqual(
            (self.repos_directory / "local" / "compose.yml").read_text(encoding="utf-8"),
            "services:\n  web: {}\n",
        )
        self.assertEqual(services.list_local_yml_files(), [{"name": "compose.yml", "path": "compose.yml"}])
        self.assertEqual(services.get_local_yml_content("compose.yml")["content"], "services:\n  web: {}\n")

        updated = services.update_local_yml("compose.yml", "应用.yaml", "services:\n  app: {}\n")

        self.assertEqual(updated["file_name"], "应用.yaml")
        self.assertFalse((self.repos_directory / "local" / "compose.yml").exists())
        self.assertEqual(
            (self.repos_directory / "local" / "应用.yaml").read_text(encoding="utf-8"),
            "services:\n  app: {}\n",
        )

    def test_local_yml_name_and_size_are_restricted(self):
        invalid_names = ("", "../compose.yml", "folder/compose.yml", "compose.txt", "bad?.yml")
        for name in invalid_names:
            with self.subTest(name=name), self.assertRaises(ValueError):
                services.create_local_yml(name, "services:\n")

        with self.assertRaises(ValueError):
            services.create_local_yml("large.yml", "中" * 200_000)

    def test_create_and_rename_do_not_overwrite_existing_files(self):
        services.create_local_yml("one.yml", "one")
        with self.assertRaises(FileExistsError):
            services.create_local_yml("one.yml", "changed")
        self.assertEqual(services.get_local_yml_content("one.yml")["content"], "one")

        services.create_local_yml("two.yml", "two")
        with self.assertRaises(FileExistsError):
            services.update_local_yml("one.yml", "two.yml", "changed")
        self.assertEqual(services.get_local_yml_content("one.yml")["content"], "one")
        self.assertEqual(services.get_local_yml_content("two.yml")["content"], "two")

    def test_local_files_are_available_to_deployment_resolver(self):
        services.create_local_yml("compose.yml", "services:\n  web: {}\n")

        repo = services._local_deployment_repo()

        self.assertEqual(repo.name, "local")
        self.assertEqual(repo.repo_dir_name, "local")
        self.assertEqual(repo.yml_files[0].name, "compose.yml")
        self.assertEqual(repo.yml_files[0].content, "services:\n  web: {}\n")

if __name__ == "__main__":
    unittest.main()
