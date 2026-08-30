import json
import tempfile
import unittest
from pathlib import Path

from backend.app import services


class ConfigSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repos_path = services.REPOS_JSON_PATH
        self.recommend_path = services.RECOMMEND_JSON_PATH
        self.initial_repos_path = services.INITIAL_REPOS_JSON_PATH
        self.initial_recommend_path = services.INITIAL_RECOMMEND_JSON_PATH
        self.repo_sync_state_path = services.REPO_SYNC_STATE_PATH
        services.REPOS_JSON_PATH = Path(self.temp_dir.name) / "repos.json"
        services.RECOMMEND_JSON_PATH = Path(self.temp_dir.name) / "recommend.json"
        services.INITIAL_REPOS_JSON_PATH = Path(self.temp_dir.name) / "initial-repos.json"
        services.INITIAL_RECOMMEND_JSON_PATH = Path(self.temp_dir.name) / "initial-recommend.json"
        services.REPO_SYNC_STATE_PATH = Path(self.temp_dir.name) / "repo-sync-state.json"

    def tearDown(self):
        services.REPOS_JSON_PATH = self.repos_path
        services.RECOMMEND_JSON_PATH = self.recommend_path
        services.INITIAL_REPOS_JSON_PATH = self.initial_repos_path
        services.INITIAL_RECOMMEND_JSON_PATH = self.initial_recommend_path
        services.REPO_SYNC_STATE_PATH = self.repo_sync_state_path
        self.temp_dir.cleanup()

    def test_repositories_are_loaded_only_from_json(self):
        expected = [{"name": "自定义仓库", "repo_url": "https://example.test/repo.git"}]
        services.INITIAL_REPOS_JSON_PATH.write_text(json.dumps(expected), encoding="utf-8")
        self.assertEqual(services.load_repos_config(), expected)
        self.assertTrue(services.REPOS_JSON_PATH.exists())

        current = [{"name": "运行时仓库", "repo_url": "https://example.test/current.git"}]
        services.REPOS_JSON_PATH.write_text(json.dumps(current), encoding="utf-8")
        self.assertEqual(services.load_repos_config(), current)

    def test_recommendations_are_loaded_only_from_json(self):
        expected = {
            "_tutorial_base_url": "https://example.test/tutorial/",
            "demo": {"title": "自定义推荐", "tutorial": "demo"},
        }
        services.INITIAL_RECOMMEND_JSON_PATH.write_text(json.dumps(expected), encoding="utf-8")
        result = services.load_recommend_config()
        self.assertEqual(result["demo"]["title"], "自定义推荐")
        self.assertEqual(result["demo"]["tutorial"], "https://example.test/tutorial/demo")
        self.assertTrue(services.RECOMMEND_JSON_PATH.exists())

    def test_repository_sync_time_is_persisted(self):
        timestamp = services._record_repo_sync_time(
            "https://example.test/repo.git", "main", "compose", "compose"
        )
        self.assertEqual(
            services._get_repo_sync_time(
                "https://example.test/repo.git",
                "main",
                "compose",
                "compose",
                Path(self.temp_dir.name),
            ),
            timestamp,
        )
