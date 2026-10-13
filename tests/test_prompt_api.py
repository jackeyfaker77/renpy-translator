"""提示词 API 语义测试（直接调 handler + stub state）

覆盖：列表结构、全局/项目保存分层、空文本拒绝、恢复单条/全部、
无项目时项目层拒绝、保存返回警告（不阻断）。
"""
import asyncio

import pytest

import prompt_store
from server.api import prompts as prompts_api
from server.errors import ApiError


class _StubLogger:
    def info(self, *a, **kw):
        pass


class _StubState:
    """最小 state：run_sync 直调（同步语义）、无项目时 db=None"""

    def __init__(self, db=None):
        self.db = db
        self.logger = _StubLogger()

    async def run_sync(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def save_global_prompts(self, overrides):
        prompt_store.set_global_overrides(overrides)

    def save_project_prompts(self, overrides):
        self.db.set_meta('prompt_overrides',
                         __import__('json').dumps(overrides,
                                                  ensure_ascii=False))
        prompt_store.set_project_overrides(overrides)


class _StubDB:
    def __init__(self):
        self.meta = {}

    def set_meta(self, k, v):
        self.meta[k] = v

    def get_meta(self, k, default=''):
        return self.meta.get(k, default)


@pytest.fixture(autouse=True)
def _clean():
    prompt_store.reset_state()
    yield
    prompt_store.reset_state()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- 列表 ----

def test_list_structure():
    state = _StubState()
    data = _run(prompts_api.list_prompts(state))
    assert len(data['prompts']) == 27
    assert data['has_project'] is False
    assert '对话' in data['categories']
    p = next(x for x in data['prompts'] if x['id'] == 'name.user')
    assert p['default_text'] and p['source'] == 'default'
    assert p['project_override'] is None
    assert any(x['token'] == 'name' for x in p['placeholders'])


def test_list_shows_overrides_and_source():
    prompt_store.set_global_overrides({'name.user': 'G'})
    state = _StubState(db=_StubDB())
    prompt_store.set_project_overrides({'name.user': 'P'})
    data = _run(prompts_api.list_prompts(state))
    p = next(x for x in data['prompts'] if x['id'] == 'name.user')
    assert p['global_override'] == 'G'
    assert p['project_override'] == 'P'
    assert p['source'] == 'project'
    assert p['effective_text'] == 'P'


# ---- 保存 ----

def test_save_global():
    state = _StubState()
    out = _run(prompts_api.save_prompt(
        'name.user', prompts_api.PromptSaveIn(scope='global', text='你好 [[name]]'),
        state))
    assert out['ok'] and out['warnings'] == []
    assert prompt_store.effective('name.user')[1] == 'global'


def test_save_project_requires_open_project():
    with pytest.raises(ApiError) as e:
        _run(prompts_api.save_prompt(
            'name.user', prompts_api.PromptSaveIn(scope='project', text='P'),
            _StubState()))
    assert e.value.code == 'NEED_PROJECT'


def test_save_project_persists_meta():
    db = _StubDB()
    state = _StubState(db=db)
    _run(prompts_api.save_prompt(
        'name.user', prompts_api.PromptSaveIn(scope='project', text='P'),
        state))
    import json
    assert json.loads(db.meta['prompt_overrides']) == {'name.user': 'P'}
    assert prompt_store.effective('name.user') == ('P', 'project')


def test_save_empty_rejected():
    with pytest.raises(ApiError) as e:
        _run(prompts_api.save_prompt(
            'name.user', prompts_api.PromptSaveIn(scope='global', text='  '),
            _StubState()))
    assert e.value.code == 'EMPTY_TEXT'


def test_save_returns_warnings_not_block():
    """删占位符/锚点只警告，保存仍成功"""
    out = _run(prompts_api.save_prompt(
        'dialogue.batch_user',
        prompts_api.PromptSaveIn(scope='global', text='只有文字没有锚点'),
        _StubState()))
    assert out['ok']
    kinds = {w['kind'] for w in out['warnings']}
    assert 'missing_placeholder' in kinds
    assert 'missing_anchor' in kinds


def test_save_unknown_prompt():
    with pytest.raises(ApiError) as e:
        _run(prompts_api.save_prompt(
            'no.such', prompts_api.PromptSaveIn(scope='global', text='x'),
            _StubState()))
    assert e.value.code == 'NO_PROMPT'


# ---- 恢复默认 ----

def test_reset_one_both_layers():
    db = _StubDB()
    state = _StubState(db=db)
    prompt_store.set_global_overrides({'name.user': 'G'})
    prompt_store.set_project_overrides({'name.user': 'P'})
    _run(prompts_api.reset_prompt('name.user', None, state))
    assert prompt_store.effective('name.user')[1] == 'default'


def test_reset_one_single_scope():
    db = _StubDB()
    state = _StubState(db=db)
    prompt_store.set_global_overrides({'name.user': 'G'})
    prompt_store.set_project_overrides({'name.user': 'P'})
    _run(prompts_api.reset_prompt(
        'name.user', prompts_api.PromptResetIn(scope='project'), state))
    assert prompt_store.effective('name.user') == ('G', 'global')


def test_reset_all():
    db = _StubDB()
    state = _StubState(db=db)
    prompt_store.set_global_overrides({'name.user': 'G', 'ui.user': 'U'})
    prompt_store.set_project_overrides({'name.user': 'P'})
    _run(prompts_api.reset_all(state))
    assert prompt_store.effective('name.user')[1] == 'default'
    assert prompt_store.effective('ui.user')[1] == 'default'
