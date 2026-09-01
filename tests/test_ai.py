import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from backend.app import ai, ai_history, database
import test_first_login as auth_tests


YAML = 'services:\n  web:\n    image: nginx:alpine\n    ports:\n      - "8080:80"\n'
SECRET = 'sk-test-secret-never-return'
RealAsyncClient = httpx.AsyncClient


class AIEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        auth_tests.FirstLoginTests.setUpClass()

    def setUp(self):
        self.fixture = auth_tests.FirstLoginTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.client = self.fixture.client
        database.change_initial_password('admin', auth_tests.NEW_PASSWORD)
        self.admin = database.get_user_by_username('admin')
        token, _ = database.create_user_session(self.admin['id'])
        self.client.cookies.set('session_token', token)
        self.requests = []
        self.reply = {'choices': [{'finish_reason': 'stop', 'message': {'content': YAML}}]}
        self.status_code = 200
        self.responses = []
        ai._active_users.clear()

        def handle(request):
            self.requests.append(request)
            if self.responses:
                code, reply = self.responses.pop(0)
                return httpx.Response(code, json=reply)
            return httpx.Response(self.status_code, json=self.reply)

        def client_factory(**kwargs):
            self.assertFalse(kwargs['follow_redirects'])
            return RealAsyncClient(transport=httpx.MockTransport(handle), **kwargs)

        patcher = patch.object(ai.httpx, 'AsyncClient', side_effect=client_factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def configure(self, **overrides):
        data = {'enabled': True, 'base_url': 'https://ai.example.test/v1', 'model': 'test-model', 'api_key': SECRET}
        data.update(overrides)
        return self.client.put('/api/ai/config', json=data)

    def test_new_install_uses_openai_url_as_placeholder_only(self):
        response = self.client.get('/api/ai/config')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['base_url'], '')
        self.assertFalse(response.json()['data']['enabled'])
        self.assertNotIn('api.openai.com', response.text)

    def generate(self, **overrides):
        revision = self.client.get('/api/ai/config').json()['data']['revision']
        data = {'prompt': '增加 Web 服务', 'current_yaml': '', 'config_revision': revision}
        data.update(overrides)
        return self.client.post('/api/ai/generate-yaml', json=data)

    def chat(self, messages=None, **overrides):
        revision = self.client.get('/api/ai/config').json()['data']['revision']
        data = {'messages': messages if messages is not None else [{'role': 'user', 'content': '你好'}],
                'config_revision': revision}
        data.update(overrides)
        return self.client.post('/api/ai/chat', json=data)

    def test_chat_returns_full_conversation_without_yaml_extraction(self):
        self.configure()
        answer = '  这是方案说明。\n```yaml\n' + YAML + '```\n还要注意备份。\n'
        self.reply['choices'][0]['message']['content'] = answer
        messages = [{'role': 'user', 'content': '讨论一下备份'},
                    {'role': 'assistant', 'content': '可以先备份数据卷。'},
                    {'role': 'user', 'content': '请给个示例并解释'}]
        with patch.object(ai, 'validate_compose_output', side_effect=AssertionError('Chat must not validate YAML')):
            response = self.chat(messages)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['messages'], messages + [{'role': 'assistant', 'content': answer}])
        self.assertEqual(json.loads(self.requests[0].content)['messages'], messages)
        self.assertEqual(response.json()['data']['warning'], '')
        self.assertNotIn(SECRET, response.text)
        with database.db_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM deployments').fetchone()[0], 0)

    def test_conversation_persists_full_content_and_warnings_across_database_reinitialization(self):
        self.configure()
        conversation = self.client.post('/api/ai/conversations').json()['data']
        cid = conversation['id']
        self.assertEqual(conversation['messages'], [])
        answer = '<think>分析输入</think>\n```yaml\n' + YAML + '```\n完整说明。'
        self.reply = {'choices': [{'finish_reason': 'length', 'message': {'content': answer}}]}
        response = self.chat(conversation_id=cid)
        self.assertEqual(response.status_code, 200, response.text)
        expected = response.json()['data']['messages']
        # Startup migration must be idempotent and must not clear stored chat data.
        with patch('builtins.print'):
            database.init_db()
        self.client.cookies.clear()
        token, _ = database.create_user_session(self.admin['id'])
        self.client.cookies.set('session_token', token)
        loaded = self.client.get(f'/api/ai/conversations/{cid}')
        self.assertEqual(loaded.status_code, 200)
        self.assertIn('no-store', loaded.headers['cache-control'])
        saved = loaded.json()['data']
        self.assertEqual(saved['messages'], expected)
        self.assertEqual(saved['messages'][-1]['content'], answer)
        self.assertEqual(saved['warnings'], [{'index': 1, 'text': response.json()['data']['warning']}])
        self.assertFalse(saved['busy'])
        self.assertEqual(saved['draft'], '')
        self.assertNotIn(SECRET, loaded.text)
        new = self.client.post('/api/ai/conversations').json()['data']
        records = self.client.get('/api/ai/conversations').json()['data']
        self.assertEqual([record['id'] for record in records], [new['id'], cid])
        self.assertNotIn('messages', records[0], 'History list returns metadata only')
        response = self.chat(expected + [{'role': 'user', 'content': '继续'}], conversation_id=cid)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(json.loads(self.requests[-1].content)['messages'], expected + [{'role': 'user', 'content': '继续'}])

    def test_history_is_owned_and_requires_authenticated_admin(self):
        self.configure()
        cid = self.client.post('/api/ai/conversations').json()['data']['id']
        self.chat(conversation_id=cid)
        database.create_user('another-admin', 'another-admin-password')
        other = database.get_user_by_username('another-admin')
        with database.db_connection() as conn:
            conn.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (other['id'],))
        token, _ = database.create_user_session(other['id'])
        self.client.cookies.set('session_token', token)
        self.assertEqual(self.client.get('/api/ai/conversations').json()['data'], [])
        self.assertEqual(self.client.get(f'/api/ai/conversations/{cid}').status_code, 404)
        self.assertEqual(self.chat(conversation_id=cid).status_code, 404)
        self.assertEqual(len(self.requests), 1, 'Forbidden access never reaches provider')
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/ai/conversations').status_code, 401)
        self.assertEqual(self.client.post('/api/ai/conversations').status_code, 401)
        with database.db_connection() as conn:
            conn.execute('UPDATE users SET is_admin = 0 WHERE id = ?', (other['id'],))
        self.client.cookies.set('session_token', token)
        self.assertEqual(self.client.get(f'/api/ai/conversations/{cid}').status_code, 403)
        self.assertEqual(self.client.post('/api/ai/conversations').status_code, 403)

    def test_failed_request_preserves_submitted_prompt_and_allows_retry(self):
        self.configure()
        cid = self.client.post('/api/ai/conversations').json()['data']['id']
        first = self.chat(conversation_id=cid).json()['data']['messages']
        outgoing = first + [{'role': 'user', 'content': '不要丢失这条问题'}]
        self.status_code = 503
        response = self.chat(outgoing, conversation_id=cid)
        self.assertEqual(response.status_code, 502, response.text)
        saved = self.client.get(f'/api/ai/conversations/{cid}').json()['data']
        self.assertEqual(saved['messages'], first)
        self.assertEqual(saved['draft'], '不要丢失这条问题')
        self.assertFalse(saved['busy'])
        self.status_code = 200
        self.assertEqual(self.chat(outgoing, conversation_id=cid).status_code, 200)

    def test_stale_or_concurrent_history_cannot_overwrite_saved_messages(self):
        self.configure()
        cid = self.client.post('/api/ai/conversations').json()['data']['id']
        first = self.chat(conversation_id=cid).json()['data']['messages']
        self.assertEqual(self.chat(conversation_id=cid).status_code, 412)
        outgoing = first + [{'role': 'user', 'content': '下一条'}]
        uid = self.admin['id']
        token = ai_history.reserve(uid, cid, outgoing)
        self.assertTrue(self.client.get(f'/api/ai/conversations/{cid}').json()['data']['busy'])
        self.assertEqual(self.chat(outgoing, conversation_id=cid).status_code, 429)
        self.assertEqual(len(self.requests), 1)
        # A dead worker's lease expires; only the next reservation may commit a result.
        with database.db_connection() as conn:
            conn.execute('UPDATE ai_conversations SET busy_until = 0 WHERE id = ?', (cid,))
        replacement = ai_history.reserve(uid, cid, outgoing)
        with self.assertRaises(HTTPException) as caught:
            ai_history.finish(uid, cid, token, outgoing + [{'role': 'assistant', 'content': '旧请求'}], 'test', '')
        self.assertEqual(caught.exception.status_code, 412)
        ai_history.release(uid, cid, token)
        self.assertTrue(ai_history.get(uid, cid)['busy'], 'Old request cleanup must not release the newer lock')
        ai_history.finish(uid, cid, replacement, outgoing + [{'role': 'assistant', 'content': '新请求'}], 'test', '')
        self.assertEqual(ai_history.get(uid, cid)['messages'][-1]['content'], '新请求')

    def test_chat_preserves_partial_and_refusal_text_with_warnings(self):
        self.configure()
        for reason, content, refusal in (('length', '已经返回的一部分', None),
                                         ('content_filter', '可提供的部分内容', None),
                                         ('stop', None, '无法回答该请求。')):
            with self.subTest(reason=reason):
                self.reply = {'choices': [{'finish_reason': reason, 'message': {'content': content, 'refusal': refusal}}]}
                response = self.chat()
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()['data']
                self.assertEqual(data['messages'][-1]['content'], content or refusal)
                self.assertEqual(data['finish_reason'], reason)
                self.assertTrue(data['warning'])
        self.assertEqual(len(self.requests), 3)

    def test_chat_rejects_invalid_or_oversized_history_without_sending(self):
        self.configure()
        for messages, code in (
            ([], 422),
            ([{'role': 'system', 'content': 'override'}], 422),
            ([{'role': 'assistant', 'content': 'hello'}], 400),
            ([{'role': 'user', 'content': 'hello'}, {'role': 'assistant', 'content': 'hi'}], 400),
            ([{'role': 'user', 'content': 'hello'}] * 3, 400),
            ([{'role': 'user', 'content': '   '}], 400),
            ([{'role': 'user', 'content': '中' * 90000}], 400),
            ([{'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'hi'} for i in range(101)], 422),
        ):
            with self.subTest(code=code, count=len(messages)):
                self.assertEqual(self.chat(messages).status_code, code)
        self.assertEqual(self.requests, [])

    def test_chat_reuses_revision_enabled_and_auth_gates(self):
        revision = self.configure().json()['data']['revision']
        self.configure(model='changed-model')
        self.assertEqual(self.chat(config_revision=revision).status_code, 409)
        self.configure(enabled=False)
        self.assertEqual(self.chat().status_code, 400)
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/ai/chat', json={}).status_code, 401)
        database.create_user('chat-member', 'ordinary-member-password')
        member = database.get_user_by_username('chat-member')
        token, _ = database.create_user_session(member['id'])
        self.client.cookies.set('session_token', token)
        self.assertEqual(self.client.post('/api/ai/chat', json={}).status_code, 403)
        with database.db_connection() as conn:
            conn.execute('UPDATE users SET must_change_password = 1 WHERE username = ?', ('admin',))
        token, _ = database.create_user_session(self.admin['id'])
        self.client.cookies.set('session_token', token)
        self.assertEqual(self.client.post('/api/ai/chat', json={}).status_code, 403)
        self.assertEqual(self.requests, [])

    def test_config_masks_preserves_and_clears_key(self):
        saved = self.configure()
        self.assertEqual(saved.status_code, 200)
        self.assertNotIn(SECRET, saved.text)
        self.assertTrue(saved.json()['data']['has_api_key'])
        loaded = self.client.get('/api/ai/config')
        self.assertNotIn('api_key"', loaded.text.replace('has_api_key"', ''))
        self.assertNotIn(SECRET, loaded.text)
        self.assertEqual(self.configure(api_key='').status_code, 200)
        self.assertEqual(ai._load_config()['api_key'], SECRET)
        self.assertEqual(self.configure(api_key=None, clear_api_key=True).status_code, 200)
        self.assertFalse(ai._load_config()['api_key'])

    def test_address_change_requires_new_or_cleared_key(self):
        self.configure()
        response = self.configure(base_url='https://different.test/v1', api_key='')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ai._load_config()['base_url'], 'https://ai.example.test/v1')
        self.assertEqual(self.configure(base_url='https://different.test/v1', api_key='', clear_api_key=True).status_code, 200)

    def test_invalid_urls_are_rejected_and_full_endpoint_normalized(self):
        for url in ('file:///tmp/a', 'https://user:secret@example.test/v1', 'https://example.test/v1?key=secret',
                    'https://example.test/#fragment', 'https://example.test:wrong/v1'):
            with self.subTest(url=url):
                self.assertEqual(self.configure(base_url=url).status_code, 400)
        response = self.configure(base_url='https://ai.example.test/v1/chat/completions/')
        self.assertEqual(response.json()['data']['base_url'], 'https://ai.example.test/v1')
        self.assertEqual(self.requests, [])

    def test_generation_sends_only_explicit_context_and_does_not_save(self):
        self.configure()
        response = self.generate(current_yaml='services:\n  old:\n    image: busybox\n')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['yaml'], YAML)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(str(request.url), 'https://ai.example.test/v1/chat/completions')
        self.assertEqual(request.headers['authorization'], 'Bearer ' + SECRET)
        payload = json.loads(request.content)
        self.assertEqual(payload['model'], 'test-model')
        self.assertEqual(payload['max_completion_tokens'], 8192)
        self.assertFalse(payload['stream'])
        context = json.loads(payload['messages'][1]['content'])
        self.assertEqual(context['request'], '增加 Web 服务')
        self.assertIn('busybox', context['current_yaml'])
        self.assertNotIn(SECRET, json.dumps(payload))
        self.assertNotIn(SECRET, response.text)
        with database.db_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM deployments').fetchone()[0], 0)

    def test_test_connection_uses_saved_settings_and_no_file_context(self):
        self.configure(enabled=False)
        response = self.client.post('/api/ai/test')
        self.assertEqual(response.status_code, 200)
        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload['messages'], [{'role': 'user', 'content': 'Reply with OK only.'}])

    def test_disabled_or_changed_config_does_not_send_data(self):
        revision = self.configure().json()['data']['revision']
        self.configure(enabled=False)
        self.assertEqual(self.generate().status_code, 400)
        self.assertEqual(self.generate(config_revision=revision).status_code, 409)
        self.assertEqual(self.requests, [])

    def test_upstream_errors_do_not_expose_secrets_or_raw_response(self):
        self.configure()
        for code in (301, 400, 401, 403, 404, 429, 500):
            with self.subTest(code=code):
                self.status_code = code
                self.reply = {'error': {'message': SECRET}}
                response = self.generate()
                self.assertEqual(response.status_code, 502)
                self.assertNotIn(SECRET, response.text)
                self.assertEqual(ai._active_users, set())
        self.assertEqual(len(self.requests), 7)  # 没有自动重试或跟随跳转。

    def test_timeout_releases_request_slot(self):
        self.configure()
        with patch.object(ai, '_completion', AsyncMock(side_effect=httpx.ReadTimeout(SECRET))):
            response = self.generate()
        self.assertEqual(response.status_code, 504)
        self.assertNotIn(SECRET, response.text)
        self.assertEqual(ai._active_users, set())

    def test_explicit_unsupported_parameter_falls_back_once(self):
        self.configure()
        for status, rejection in (
            (400, {'error': {'param': 'max_completion_tokens', 'code': 'unsupported_parameter'}}),
            (400, {'error': {'message': "Unknown parameter: 'max_completion_tokens'"}}),
            (400, {'error': {'message': "'max_completion_tokens' is not supported for this model"}}),
            (422, {'detail': [{'loc': ['body', 'max_completion_tokens'], 'type': 'extra_forbidden'}]}),
        ):
            with self.subTest(rejection=rejection):
                self.requests.clear()
                self.responses = [(status, rejection), (200, self.reply)]
                response = self.generate()
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(self.requests), 2)
                first, second = [json.loads(request.content) for request in self.requests]
                self.assertEqual(first.pop('max_completion_tokens'), second.pop('max_tokens'))
                self.assertEqual(first, second)

    def test_fallback_failure_stops_and_never_exposes_upstream_body(self):
        self.configure()
        self.responses = [
            (400, {'error': {'param': 'max_completion_tokens', 'code': 'unsupported_parameter'}}),
            (400, {'error': {'param': 'max_tokens', 'message': SECRET + ' private-yaml-value'}}),
        ]
        with self.assertLogs(ai._logger, level='WARNING') as logs:
            response = self.generate(current_yaml='private-yaml-value', prompt='private-prompt-value')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(self.requests), 2)
        self.assertIn('max_tokens', response.json()['detail'])
        self.assertIn('AI_UPSTREAM_HTTP', response.text)
        self.assertIn('HTTP 400', response.text)
        for secret in (SECRET, 'private-yaml-value', 'private-prompt-value'):
            self.assertNotIn(secret, response.text + '\n'.join(logs.output))

    def test_unrelated_rejections_or_output_failures_are_not_retried(self):
        self.configure()
        for status, reply, diagnostic in (
            (400, {'error': {'param': 'max_completion_tokens', 'code': 'invalid_value',
                             'message': 'max_completion_tokens must be at most 4096'}}, 'AI_UPSTREAM_HTTP'),
            (400, {'error': {'message': 'Unsupported parameter: temperature; max_completion_tokens=8192'}}, 'AI_UPSTREAM_HTTP'),
            (500, {'error': {'param': 'max_completion_tokens', 'code': 'unsupported_parameter'}}, 'AI_UPSTREAM_HTTP'),
            (200, {'choices': [{'finish_reason': 'length', 'message': {'content': YAML}}]}, 'AI_OUTPUT_TRUNCATED'),
            (200, {'choices': [{'message': {'content': None, 'reasoning_content': YAML}}]}, 'AI_RESPONSE_INVALID'),
            (200, {'choices': [{'message': {'content': YAML, 'refusal': 'refused'}}]}, 'AI_OUTPUT_REFUSED'),
        ):
            with self.subTest(status=status, diagnostic=diagnostic):
                self.requests.clear()
                self.status_code, self.reply = status, reply
                with self.assertLogs(ai._logger, level='WARNING') as logs:
                    response = self.generate()
                self.assertEqual(response.status_code, 502)
                self.assertIn(diagnostic, response.text)
                self.assertIn(diagnostic, '\n'.join(logs.output))
                self.assertEqual(len(self.requests), 1)

    def test_non_json_upstream_and_network_error_have_safe_diagnostics(self):
        self.configure()
        transport = httpx.MockTransport(lambda request: httpx.Response(502, text='<html>' + SECRET + '</html>'))
        with patch.object(ai.httpx, 'AsyncClient', side_effect=lambda **kwargs: RealAsyncClient(transport=transport, **kwargs)):
            response = self.generate()
        self.assertIn('AI_UPSTREAM_HTTP', response.text)
        self.assertIn('HTTP 502', response.text)
        self.assertNotIn(SECRET, response.text)
        with patch.object(ai, '_completion', AsyncMock(side_effect=httpx.ConnectError(SECRET))):
            response = self.generate()
        self.assertIn('AI_CONNECTION_FAILED', response.text)
        self.assertNotIn(SECRET, response.text)
        self.assertEqual(ai._active_users, set())

    def test_truncated_malformed_and_invalid_yaml_are_rejected(self):
        self.configure()
        for reply in (
            {}, {'choices': [None]},
            {'choices': [{'finish_reason': 'length', 'message': {'content': YAML}}]},
            {'choices': [{'message': {'content': '抱歉，无法完成'}}]},
            {'choices': [{'message': {'content': 'services: ['}}]},
        ):
            with self.subTest(reply=reply):
                self.reply = reply
                self.assertEqual(self.generate().status_code, 502)

    def test_input_limits_and_busy_requests(self):
        self.configure()
        self.assertEqual(self.generate(prompt='   ').status_code, 400)
        self.assertEqual(self.generate(prompt='a' * 6001).status_code, 422)
        self.assertEqual(self.generate(current_yaml='中' * 50000).status_code, 400)
        ai._active_users.add(self.admin['id'])
        try:
            self.assertEqual(self.generate().status_code, 429)
        finally:
            ai._active_users.clear()
        self.assertEqual(self.requests, [])

    def test_all_ai_endpoints_require_initialized_admin(self):
        self.client.cookies.clear()
        for method, path, body in (('GET', '/api/ai/config', None), ('PUT', '/api/ai/config', {}),
                                   ('POST', '/api/ai/test', None), ('POST', '/api/ai/generate-yaml', {})):
            self.assertEqual(self.client.request(method, path, json=body).status_code, 401)
        database.create_user('member', 'test-member-password')
        member = database.get_user_by_username('member')
        token, _ = database.create_user_session(member['id'])
        self.client.cookies.set('session_token', token)
        self.assertEqual(self.client.get('/api/ai/config').status_code, 403)
        self.assertEqual(self.client.post('/api/ai/test').status_code, 403)
        self.assertEqual(self.client.put('/api/ai/config', json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/ai/generate-yaml', json={}).status_code, 403)
        with database.db_connection() as conn:
            conn.execute('UPDATE users SET must_change_password = 1 WHERE username = ?', ('admin',))
        token, _ = database.create_user_session(self.admin['id'])
        self.client.cookies.set('session_token', token)
        self.assertEqual(self.client.post('/api/ai/test').status_code, 403)
        self.assertEqual(self.requests, [])


class AIYamlValidationTests(unittest.TestCase):
    def test_plain_and_fenced_yaml(self):
        self.assertEqual(ai.validate_compose_output(YAML), YAML)
        self.assertEqual(ai.validate_compose_output('```yaml\n' + YAML + '```'), YAML)
        self.assertEqual(ai.validate_compose_output('这是配置：\n```yml\n' + YAML + '```\n请检查端口。'), YAML)

    def test_invalid_compose_and_unsafe_tags(self):
        for content in ('', 'services: {}', 'services: []', 'services:\n  web: hello',
                        'services:\n  web:\n    ports: [80]', '!!python/object/apply:os.system ["echo bad"]',
                        '```yaml\n' + YAML + '```\n```yaml\n' + YAML + '```'):
            with self.subTest(content=content), self.assertRaises(HTTPException):
                ai.validate_compose_output(content)
