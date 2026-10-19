"""MCP：精选注册表 / confirm 守卫 / 全量生成器命名与危险判定"""
import tempfile
from pathlib import Path

import pytest

from server.app import create_app
from server.mcp_service.generate import is_dangerous, tool_name
from server.mcp_service.curated import _require_confirm
from mcp.server.mcpserver.exceptions import ToolError


def _tmp_app():
    return create_app(Path(tempfile.mkdtemp()))


# ---- confirm 守卫 ----

def test_require_confirm_blocks_false():
    with pytest.raises(ToolError, match='confirm=true'):
        _require_confirm(False, '删除项目 x')


def test_require_confirm_allows_true():
    _require_confirm(True, '删除项目 x')  # 不抛


# ---- 全量命名 ----

def test_tool_name_samples():
    assert tool_name('get', '/api/session') == 'api_get_session'
    assert tool_name('post', '/api/current/texts/dialogue/translate-all') \
        == 'api_post_current_texts_dialogue_translate_all'
    assert tool_name('delete', '/api/projects/{name}') \
        == 'api_delete_projects_name'
    assert tool_name('put', '/api/settings/prefs') == 'api_put_settings_prefs'
    assert len(tool_name('post', '/api/' + 'x' * 100)) <= 64


# ---- 危险判定 ----

@pytest.mark.parametrize('method,path,expected', [
    ('GET', '/api/session', False),
    ('GET', '/api/projects', False),
    ('DELETE', '/api/projects/{name}', True),
    ('DELETE', '/api/configs/{name}', True),
    ('POST', '/api/shutdown', True),
    ('PUT', '/api/settings/data-dir', True),
    ('POST', '/api/current/texts/dialogue/clear', True),
    ('POST', '/api/prompts/reset', True),
    ('POST', '/api/current/texts/dialogue/replace', True),
    ('POST', '/api/current/texts/dialogue/translate-all', False),
    ('PATCH', '/api/current/texts/dialogue/{item_id}', False),
])
def test_is_dangerous_table(method, path, expected):
    assert is_dangerous(method, path) is expected


# ---- 精选注册表 vs openapi ----

def _curated_tool_map(app):
    """从已注册的精选工具里取 {name: description}（不依赖运行中的 MCP）"""
    from server.mcp_service.app import install_mcp
    mcp = install_mcp(app)
    # tool manager 内部注册表
    tools = mcp._tool_manager.list_tools()
    return {t.name: t for t in tools}


def test_curated_count_and_confirm():
    app = _tmp_app()
    tools = _curated_tool_map(app)
    assert len(tools) == 30
    assert 'confirm' in tools['delete_project'].parameters.get('properties', {})


def test_curated_rest_paths_exist_in_openapi():
    """精选工具映射的 REST 路径必须仍在 openapi 里（防路由改名漂移）"""
    app = _tmp_app()
    spec = app.openapi()
    paths = set(spec.get('paths', {}))
    expected = [
        '/api/session', '/api/projects', '/api/session/open',
        '/api/projects/create', '/api/projects/{name}',
        '/api/current/texts/{content_type}',
        '/api/current/texts/{content_type}/{item_id}/translate',
        '/api/current/texts/{content_type}/translate-all',
        '/api/current/texts/{content_type}/{item_id}',
        '/api/current/texts/{content_type}/failed-batches/retry',
        '/api/current/names', '/api/current/names/translate-all',
        '/api/current/glossary',
        '/api/current/embedded/scan',
        '/api/current/graph/relations',
        '/api/current/export/game', '/api/current/export/texts',
        '/api/current/import/from-project/preview',
        '/api/current/import/from-project/apply',
        '/api/scheduled',
        '/api/logs', '/api/logs/api',
        '/api/configs', '/api/prompts',
    ]
    for p in expected:
        assert p in paths, f'路由缺失: {p}'


# ---- 全量生成 ----

def test_full_generate_names_and_excludes():
    app = _tmp_app()
    from server.mcp_service.generate import build_full_tools
    tools, table = build_full_tools(app)
    names = {t.name for t in tools}
    assert 'api_get_session' in names
    assert 'api_delete_projects_name' in names
    # 排除：SSE / 上传 / 二进制 / 明文 key / spa
    assert not any(n.endswith('events') for n in names)
    assert 'api_get_configs_name_key' not in names
    assert not any('create_zip' in n or 'create_zip' in n for n in names)
    assert not any('multipart' in str(t.input_schema) for t in tools)
    # 危险注入 confirm
    assert table['api_delete_projects_name']['dangerous'] is True
    assert 'confirm' in next(
        t for t in tools if t.name == 'api_delete_projects_name'
    ).input_schema.get('properties', {})
    # GET 不注入
    assert 'confirm' not in next(
        t for t in tools if t.name == 'api_get_session'
    ).input_schema.get('properties', {})
    # 工具名唯一
    assert len(names) == len(tools)


# ---- token 相关纯逻辑（管理 API 形状） ----

def test_mcp_admin_roundtrip():
    from starlette.testclient import TestClient
    app = _tmp_app()
    with TestClient(app, base_url='http://127.0.0.1:7861') as client:
        r = client.get('/api/settings/mcp')
        assert r.status_code == 200
        body = r.json()
        assert body['enabled'] is False
        assert body['tool_mode'] == 'curated'
        assert body['has_token'] is False
        assert body['endpoint'].endswith('/mcp/')

        r = client.put('/api/settings/mcp',
                       json={'enabled': True, 'tool_mode': 'full'})
        assert r.json()['enabled'] is True
        assert r.json()['tool_mode'] == 'full'

        r = client.put('/api/settings/mcp', json={'tool_mode': 'nope'})
        assert r.status_code == 400

        tok = client.post('/api/settings/mcp/token').json()
        assert len(tok['token']) >= 32
        assert tok['has_token'] is True

        r = client.delete('/api/settings/mcp/token')
        assert r.json()['has_token'] is False
