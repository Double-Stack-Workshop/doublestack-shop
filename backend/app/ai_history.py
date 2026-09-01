"""按用户隔离的对话记录；不保存服务密钥，不持有跨网络请求的数据库锁。"""
import json
import sqlite3
import time
import uuid

from fastapi import HTTPException

from .database import db_connection, get_utc8_now


def _now():
    return get_utc8_now().strftime('%Y-%m-%d %H:%M:%S.%f')


def _row(conn, user_id, conversation_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM ai_conversations WHERE id = ? AND user_id = ?',
                       (conversation_id, user_id)).fetchone()
    if row is None:
        raise HTTPException(404, '对话不存在')
    return row


def _data(row):
    return {
        'id': row['id'], 'title': row['title'], 'messages': json.loads(row['messages']),
        'warnings': json.loads(row['warnings']), 'model': row['model'], 'draft': row['draft'],
        'busy': row['busy_until'] > time.time(), 'updated_at': row['updated_at'],
    }


def create(user_id):
    conversation_id = uuid.uuid4().hex
    now = _now()
    with db_connection() as conn:
        conn.execute('INSERT INTO ai_conversations (id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)',
                     (conversation_id, user_id, now, now))
        return _data(_row(conn, user_id, conversation_id))


def list_for_user(user_id):
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            'SELECT id, title, updated_at FROM ai_conversations WHERE user_id = ? ORDER BY updated_at DESC, id',
            (user_id,))]


def get(user_id, conversation_id):
    with db_connection() as conn:
        return _data(_row(conn, user_id, conversation_id))


def reserve(user_id, conversation_id, messages):
    token = uuid.uuid4().hex
    with db_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = _row(conn, user_id, conversation_id)
        if row['busy_until'] > time.time():
            raise HTTPException(429, '此对话仍在生成，请稍后重新打开历史记录')
        if json.loads(row['messages']) != messages[:-1]:
            raise HTTPException(412, '对话已在其他页面更新，请重新加载后发送')
        title = row['title'] if row['messages'] != '[]' else ' '.join(messages[-1]['content'].split())[:40]
        conn.execute('''UPDATE ai_conversations SET draft = ?, title = ?, request_token = ?, busy_until = ?, updated_at = ?
                        WHERE id = ? AND user_id = ?''',
                     (messages[-1]['content'], title, token, time.time() + 180, _now(), conversation_id, user_id))
    return token


def finish(user_id, conversation_id, token, messages, model, warning):
    with db_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = _row(conn, user_id, conversation_id)
        if row['request_token'] != token:
            raise HTTPException(412, '此请求已过期，请重新打开历史记录查看最新对话')
        warnings = json.loads(row['warnings'])
        if warning:
            warnings.append({'index': len(messages) - 1, 'text': warning})
        conn.execute('''UPDATE ai_conversations SET messages = ?, warnings = ?, model = ?, draft = '',
                        request_token = '', busy_until = 0, updated_at = ? WHERE id = ? AND user_id = ?''',
                     (json.dumps(messages, ensure_ascii=False), json.dumps(warnings, ensure_ascii=False), model,
                      _now(), conversation_id, user_id))
        return _data(_row(conn, user_id, conversation_id))


def release(user_id, conversation_id, token):
    # 失败只解除生成锁，保留已提交问题，刷新后可恢复。
    with db_connection() as conn:
        conn.execute('''UPDATE ai_conversations SET request_token = '', busy_until = 0
                        WHERE id = ? AND user_id = ? AND request_token = ?''', (conversation_id, user_id, token))
