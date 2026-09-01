import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app import database, services
from backend.app.schemas import RepoInfo


class RepoDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = root / 'repos.json'
        self.compose_dir = root / 'repos' / 'compose'
        self.compose_dir.mkdir(parents=True)
        self.compose_file = self.compose_dir / 'compose.yml'
        self.compose_file.write_text('services: {}', encoding='utf-8')
        self.script_dir = root / 'scripts'
        self.script_dir.mkdir()
        self.script_file = self.script_dir / 'install.sh'
        self.script_file.write_text('#!/bin/sh\necho test', encoding='utf-8')
        self.entries = [
            {'name': 'compose', 'repo_url': 'https://example.test/compose', 'repo_type': 'compose'},
            {'name': '脚本仓库', 'repo_url': 'https://example.test/scripts', 'repo_type': 'script'},
        ]
        self.config.write_text(json.dumps(self.entries), encoding='utf-8')
        self.repos = [RepoInfo(
            name=entry['name'], url=entry['repo_url'], repo_type=entry['repo_type'],
            branch='main', local_path='', yml_files=[], status='active',
        ) for entry in self.entries]
        for target, attribute, value in (
            (database, 'DATABASE_PATH', str(root / 'app.db')),
            (services, 'REPOS_JSON_PATH', self.config),
            (services, 'REPO_SYNC_STATE_PATH', root / 'sync.json'),
            (services, 'REPOS_DIR', root / 'repos'),
            (services, 'SCRIPTS_DIR', self.script_dir),
            (services, 'SCRIPT_REPOS_STATE_PATH', root / 'script-state.json'),
            (services, 'repos_db', self.repos),
            (services, '_repos_loaded', True),
            (services, 'log_service', Mock()),
        ):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        with patch('builtins.print'):
            database.init_db()
        database.set_setting('current_repo', 'compose')

    def test_delete_current_repo_persists_and_keeps_local_files(self):
        repo = self.repos[0]
        services._record_repo_sync_time(repo.url, repo.branch, repo.local_path, repo.repo_type)
        self.assertTrue(services.delete_repo('compose'))
        self.assertEqual(services.get_current_repo(), '')
        self.assertEqual([r['name'] for r in services.load_repos_config()], ['脚本仓库'])
        self.assertEqual(services._load_repo_sync_state(), {})
        self.assertTrue(self.compose_file.exists())
        self.assertTrue(self.script_file.exists())
        services.repos_db = []
        services._repos_loaded = False
        self.assertEqual([r['name'] for r in services.get_all_repos()], ['脚本仓库'])

    def test_delete_all_repos_stays_empty_after_reload(self):
        self.assertTrue(services.delete_repo('脚本仓库'))
        self.assertEqual(services.get_current_repo(), 'compose')
        self.assertTrue(services.delete_repo('compose'))
        services.repos_db = []
        services._repos_loaded = False
        self.assertEqual(services.get_all_repos(), [])
        self.assertEqual(services.load_repos_config(), [])
        self.assertTrue(self.script_file.exists())

    def test_write_failure_does_not_remove_repo_or_current_setting(self):
        original = self.config.read_text(encoding='utf-8')
        with patch.object(services, 'save_repos_config', return_value=False):
            with self.assertRaises(OSError):
                services.delete_repo('compose')
        self.assertEqual(len(services.repos_db), 2)
        self.assertEqual(services.get_current_repo(), 'compose')
        self.assertEqual(self.config.read_text(encoding='utf-8'), original)

    def test_invalid_config_is_not_overwritten_by_delete(self):
        self.config.write_text('invalid-json', encoding='utf-8')
        with patch('builtins.print'), self.assertRaises(OSError):
            services.delete_repo('compose')
        self.assertEqual(len(services.repos_db), 2)
        self.assertEqual(self.config.read_text(encoding='utf-8'), 'invalid-json')

    def test_missing_repo_is_not_success(self):
        self.assertFalse(services.delete_repo('missing'))
        self.assertEqual(len(services.repos_db), 2)

    def test_syncing_repo_cannot_be_deleted_but_other_repo_can(self):
        syncing_repo = self.repos[1]

        def during_sync(*_args):
            self.assertEqual(syncing_repo.status, 'syncing')
            with self.assertRaises(ValueError):
                services.delete_repo('脚本仓库')
            self.assertTrue(services.delete_repo('compose'))
            return {'success': True, 'path': str(self.script_dir)}

        with patch.object(services, 'clone_or_pull_repo', side_effect=during_sync), \
                patch.object(services, 'scan_repo_files', return_value=[]):
            self.assertTrue(services.sync_repo('脚本仓库')['success'])
        self.assertEqual([repo.name for repo in services.repos_db], ['脚本仓库'])
        self.assertEqual(syncing_repo.status, 'active')

    def test_failed_sync_allows_subsequent_deletion(self):
        with patch.object(services, 'clone_or_pull_repo', side_effect=RuntimeError('sync failed')):
            with self.assertRaises(RuntimeError):
                services.sync_repo('compose')
        self.assertEqual(self.repos[0].status, 'error')
        self.assertTrue(services.delete_repo('compose'))
