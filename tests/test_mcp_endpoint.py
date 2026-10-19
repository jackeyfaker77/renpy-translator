"""MCP Streamable HTTP 端点集成：握手 / tools / 鉴权 / 危险守卫"""
import json
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from server.app import create_app

HEADERS = {
    'content-type': 'application/json',
    'accept': 'application/json, text/event-stream',
}


@pytest.fixture
def tmp_app():
    return create_app(Path(tempfile.mkdtemp()))


def _parse(r):
    text = r.text
    if 'data:' in text:
        for line in text.splitlines():
            if line.startswith('data: '):
                return json.loads(line[6:])
    try:
        return r.json()
    except ValueError:
        return {'raw': text[:200]}


def _rpc(client, sid, method, params=None, id_=1, headers_extra=None):
    headers = dict(HEADERS)
    if sid:
        headers['mcp-session-id'] = sid
    if headers_extra:
        headers.update(headers_extra)
    body = {'jsonrpc': '2.0', 'id': id_, 'method': method}
    if params is not None:
        body['params'] = params
    r = client.post('/mcp/', headers=headers, json=body)
    return r, r.headers.get('mcp-session-id') or sid, _parse(r)


def _session(client, enabled=True):
    client.put('/api/settings/mcp', json={'enabled': enabled})
    r, sid, _ = _rpc(client, None, 'initialize', {
        'protocolVersion': '2025-06-18', 'capabilities': {},
        'clientInfo': {'name': 'pytest', 'version': '0'}})
    assert r.status_code == 200, r.text
    headers = dict(HEADERS, **{'mcp-session-id': sid})
    client.post('/mcp/', headers=headers,
                json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    return sid


def test_disabled_returns_404(tmp_app):
    with TestClient(tmp_app, base_url='http://127.0.0.1:7861') as client:
        r, _, data = _rpc(client, None, 'initialize', {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 't', 'version': '0'}})
        assert r.status_code == 404
        assert data['error']['code'] == 'MCP_DISABLED'


def test_redirect_without_slash(tmp_app):
    with TestClient(tmp_app, base_url='http://127.0.0.1:7861',
                    follow_redirects=False) as client:
        client.put('/api/settings/mcp', json={'enabled': True})
        r = client.get('/mcp')
        assert r.status_code == 307
        assert r.headers['location'] == '/mcp/'


def test_token_auth(tmp_app):
    with TestClient(tmp_app, base_url='http://127.0.0.1:7861') as client:
        client.put('/api/settings/mcp', json={'enabled': True})
        token = client.post('/api/settings/mcp/token').json()['token']
        init = {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 't', 'version': '0'}}
        r, _, data = _rpc(client, None, 'initialize', init)
        assert r.status_code == 401
        r, _, _ = _rpc(client, None, 'initialize', init,
                       headers_extra={'authorization': f'Bearer {token}'})
        assert r.status_code == 200
        # /api 不受 token 影响
        assert client.get('/api/health').status_code == 200


def test_evil_origin_rejected(tmp_app):
    with TestClient(tmp_app, base_url='http://127.0.0.1:7861') as client:
        client.put('/api/settings/mcp', json={'enabled': True})
        r, _, _ = _rpc(client, None, 'initialize', {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 't', 'version': '0'}},
            headers_extra={'origin': 'http://evil.test'})
        assert r.status_code == 403


def test_curated_list_and_call(tmp_app):
    with TestClient(tmp_app, base_url='http://127.0.0.1:7861') as client:
        sid = _session(client)
        _, sid, tools = _rpc(client, sid, 'tools/list', {}, id_=2)
        names = [t['name'] for t in tools['result']['tools']]
        assert len(names) == 30
        assert 'wait_job' in names

        _, sid, res = _rpc(client, sid, 'tools/call', {
            'name': 'get_session', 'arguments': {}}, id_=3)
        assert res['result']['isError'] is False
        payload = json.loads(res['result']['content'][0]['text'])
        assert 'current_project' in payload

        # 危险工具无 confirm → isError
        _, sid, res = _rpc(client, sid, 'tools/call', {
            'name': 'delete_project',
            'arguments': {'name': 'nope'}}, id_=4)
        assert res['result']['isError'] is True

        # 未开项目调 require_project 类工具 → 工具错误（409 信封）
        _, sid, res = _rpc(client, sid, 'tools/call', {
            'name': 'list_texts',
            'arguments': {'content_type': 'dialogue'}}, id_=5)
        assert res['result']['isError'] is True
        assert 'NO_PROJECT' in res['result']['content'][0]['text']


def test_full_mode_toggle(tmp_app):
    with TestClient(tmp_app, base_url='http://127.0.0.1:7861') as client:
        sid = _session(client)
        client.put('/api/settings/mcp', json={'tool_mode': 'full'})
        _, sid, tools = _rpc(client, sid, 'tools/list', {}, id_=2)
        names = {t['name'] for t in tools['result']['tools']}
        assert 'api_get_session' in names
        assert 'wait_job' in names
        assert 'get_session' not in names  # 不叠精选
        assert any(n.startswith('api_') for n in names)

        _, sid, res = _rpc(client, sid, 'tools/call', {
            'name': 'api_get_session', 'arguments': {}}, id_=3)
        assert res['result']['isError'] is False

        # 全量危险：无 confirm 被拒
        _, sid, res = _rpc(client, sid, 'tools/call', {
            'name': 'api_delete_projects_name',
            'arguments': {'name': 'x'}}, id_=4)
        assert res['result']['isError'] is True


def test_wait_job_snapshot(tmp_app):
    with TestClient(tmp_app, base_url='http://127.0.0.1:7861') as client:
        sid = _session(client)
        # 注入一个假 running job
        state = tmp_app.state.app_state
        jid = state.app_db.create_job('test', '假任务', {})
        state.app_db.add_event(jid, 'log', {'text': 'hello'})
        _, sid, res = _rpc(client, sid, 'tools/call', {
            'name': 'wait_job',
            'arguments': {'job_id': jid, 'timeout_seconds': 0}}, id_=2)
        assert res['result']['isError'] is False
        payload = json.loads(res['result']['content'][0]['text'])
        assert payload['status'] == 'running'
        assert payload['timed_out'] is True
        assert any('hello' in str(x) for x in payload.get('recent_logs', []))
