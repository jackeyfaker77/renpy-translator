"""API 轨迹日志：开关矩阵、失败记录、DB 持久化/裁剪/翻页、不泄露 key。"""
import json
from types import SimpleNamespace

import pytest

from llm_client import LLMClient
from server.state import AppState, _api_json_safe

META = {'ok': True, 'duration_ms': 12, 'attempt': 1, 'error': ''}


@pytest.fixture
def state(tmp_path):
    return AppState(tmp_path)


def _entries(state, **kw):
    return state.app_db.list_api_log_entries(**kw)


def test_off_by_default(state):
    state._on_api_log({'model': 'm'}, {'usage': {}}, 'dialogue', META)
    # 开关关：内存与 DB 都不记
    with state._api_log_lock:
        assert len(state.api_log_entries) == 0
    assert _entries(state) == []


def test_setting_switch_records_entries(state):
    state.app_db.set_setting('api_log', '1')
    state._on_api_log({'model': 'm', 'messages': [{'role': 'user', 'content': 'hi'}]},
                      {'usage': {'prompt_tokens': 3}}, 'dialogue', META)
    e = _entries(state)[0]
    assert e['task_type'] == 'dialogue' and e['model'] == 'm'
    assert e['usage']['prompt_tokens'] == 3
    assert e['ok'] and e['duration_ms'] == 12
    assert 'api_key' not in e['request']
    # 持久化：重开库仍在（不依赖内存/进程）
    state.app_db.close()
    from server.appdb import AppDatabase
    db2 = AppDatabase(state.root / 'data' / 'app.db')
    try:
        assert len(db2.list_api_log_entries()) == 1
    finally:
        db2.close()


def test_env_var_forces_on_even_if_setting_off(state, monkeypatch):
    monkeypatch.setenv('API_LOG', '1')
    state._on_api_log({'model': 'm'}, {}, 'test', META)
    assert len(_entries(state)) == 1


def test_failure_entry_records_error(state):
    state.app_db.set_setting('api_log', '1')
    state._on_api_log({'model': 'm'}, {'error': '请求超时'}, 'dialogue',
                      {'ok': False, 'duration_ms': 9, 'attempt': 3,
                       'error': '请求超时'})
    e = _entries(state)[0]
    assert not e['ok'] and e['error'] == '请求超时' and e['attempt'] == 3


def test_filter_and_pagination(state):
    state.app_db.set_setting('api_log', '1')
    for i in range(5):
        state._on_api_log({'model': f'm{i}'}, {}, 'dialogue' if i % 2 else 'name', META)
    entries = _entries(state, limit=10)
    assert [e['model'] for e in entries] == ['m0', 'm1', 'm2', 'm3', 'm4']
    only_name = _entries(state, limit=10, task_type='name')
    assert [e['model'] for e in only_name] == ['m0', 'm2', 'm4']
    page = _entries(state, limit=2, before_id=entries[3]['id'])
    # 向前翻页 = before_id 之前最近的 N 条（紧邻的更早历史）
    assert [e['model'] for e in page] == ['m1', 'm2']
    tail = _entries(state, limit=2)
    assert [e['model'] for e in tail] == ['m3', 'm4']


def test_prune_keeps_recent(state):
    state.app_db.set_setting('api_log', '1')
    keep = state.app_db.API_LOG_KEEP
    for i in range(keep + 30):
        state._on_api_log({'model': f'm{i}'}, {}, 'dialogue', META)
    rows = _entries(state, limit=1000)
    # 懒裁剪：每20条一裁，上界 = keep + 裁剪周期
    assert len(rows) <= keep + state.app_db._API_LOG_PRUNE_EVERY
    assert rows[-1]['model'] == f'm{keep + 29}'  # 最新保留


def test_legacy_jsonl_import_duration_null(state):
    """旧 jsonl 一次性导入：耗时置 NULL（迁移产物不可信）、防重导"""
    logs = state.root / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    rec = {'id': 7, 'ts': '2026-10-18 10:00:00', 'duration_ms': 0,
           'ok': True, 'attempt': 1, 'error': '', 'task_type': 'dialogue',
           'model': 'm', 'usage': {}, 'request': {'model': 'm'}, 'response': {}}
    (logs / 'api.log.jsonl').write_text(json.dumps(rec) + '\n', encoding='utf-8')
    state._import_legacy_api_jsonl()
    rows = _entries(state)
    assert len(rows) == 1 and rows[0]['duration_ms'] is None
    # 防重导：再跑一次不重复
    state._import_legacy_api_jsonl()
    assert len(_entries(state)) == 1


def test_api_json_safe_deep_converts():
    class _Msg:
        def model_dump(self):
            return {'role': 'tool', 'content': 'x'}

    out = _api_json_safe({'messages': [_Msg(), 'plain'], 'n': 1})
    assert out == {'messages': [{'role': 'tool', 'content': 'x'}, 'plain'], 'n': 1}


# ---- llm_client：成功/失败都回调 meta；不发 max_tokens ----

class _FakeCompletions:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs_seen = []

    def create(self, **kwargs):
        self.kwargs_seen.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _client(callback, completions):
    cfg = SimpleNamespace(api_key='x', api_base='http://localhost', model='m',
                          temperature=None, max_tokens=None, timeout=1)
    c = LLMClient(cfg, api_log_callback=callback)
    c.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions))
    return c


def _response():
    return SimpleNamespace(
        model_dump=lambda: {'usage': {'prompt_tokens': 10}},
        choices=[SimpleNamespace(
            message=SimpleNamespace(content='ok', tool_calls=None))])


def test_success_logs_meta_and_omits_max_tokens():
    logged = []
    comp = _FakeCompletions(response=_response())
    c = _client(lambda *a: logged.append(a), comp)
    out = c.chat_completion([{'role': 'user', 'content': 'hi'}], task_type='dialogue')
    assert out == 'ok'
    req, resp, task_type, meta = logged[0]
    assert meta['ok'] is True and meta['attempt'] == 1 and 'max_tokens' not in req
    assert 'max_tokens' not in comp.kwargs_seen[0]
    assert resp['usage']['prompt_tokens'] == 10


def test_retry_exhaustion_logs_failure(monkeypatch):
    import openai
    from llm_client import time as llm_time
    monkeypatch.setattr(llm_time, 'sleep', lambda _s: None)
    logged = []
    err = openai.APIConnectionError(request=None)
    comp = _FakeCompletions(error=err)
    c = _client(lambda *a: logged.append(a), comp)
    with pytest.raises(Exception, match='API 调用失败'):
        c.chat_completion([{'role': 'user', 'content': 'hi'}], task_type='test')
    assert len(comp.kwargs_seen) == LLMClient.MAX_RETRIES
    req, resp, task_type, meta = logged[-1]
    assert not meta['ok'] and meta['attempt'] == LLMClient.MAX_RETRIES
    assert 'error' in resp and meta['error']
