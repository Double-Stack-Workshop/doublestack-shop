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
        services.REPOS_JSON_PATH = Path(self.temp_dir.name) / "repos.json"
        services.RECOMMEND_JSON_PATH = Path(self.temp_dir.name) / "recommend.json"

    def tearDown(self):
        services.REPOS_JSON_PATH = self.repos_path
        services.RECOMMEND_JSON_PATH = self.recommend_path
        self.temp_dir.cleanup()

    def test_repositories_are_loaded_only_from_json(self):
        self.assertEqual(services.load_repos_config(), [])

        expected = [{"name": "自定义仓库", "repo_url": "https://example.test/repo.git"}]
        services.REPOS_JSON_PATH.write_text(json.dumps(expected), encoding="utf-8")
        self.assertEqual(services.load_repos_config(), expected)

    def test_recommendations_are_loaded_only_from_json(self):
        self.assertEqual(services.load_recommend_config(), {})

        expected = {
            "_tutorial_base_url": "https://example.test/tutorial/",
            "demo": {"title": "自定义推荐", "tutorial": "demo"},
        }
        services.RECOMMEND_JSON_PATH.write_text(json.dumps(expected), encoding="utf-8")
        result = services.load_recommend_config()
        self.assertEqual(result["demo"]["title"], "自定义推荐")
        self.assertEqual(result["demo"]["tutorial"], "https://example.test/tutorial/demo")
