"""OpenCode Go（opencode.ai）x-opencode-session 注入

网关 2026-09 起强制要求稳定会话头，否则 400 MissingSessionID。
仅 opencode.ai 域名注入；同一 LLMClient 生命周期内 ID 稳定。
"""
from llm_client import LLMClient, _needs_opencode_session
from translator import TranslationConfig


def _cfg(api_base: str) -> TranslationConfig:
    return TranslationConfig(api_key='test-key', api_base=api_base)


def test_needs_session域名匹配():
    assert _needs_opencode_session('https://opencode.ai/v1')
    assert _needs_opencode_session('https://Opencode.AI/v1')
    assert _needs_opencode_session('https://api.opencode.ai/anthropic')
    assert not _needs_opencode_session('https://api.deepseek.com')
    assert not _needs_opencode_session('https://api.openai.com/v1')
    assert not _needs_opencode_session('')
    assert not _needs_opencode_session(None)


def test_opencode_injects_stable_header():
    llm = LLMClient(_cfg('https://opencode.ai/v1'))
    headers = dict(llm.client.default_headers)
    sid = headers.get('x-opencode-session')
    assert sid and sid.startswith('ses_') and len(sid) > 10
    # 换配置再换回：仍是同一会话 ID（缓存亲和）
    llm.update_config(_cfg('https://api.deepseek.com'))
    assert 'x-opencode-session' not in dict(llm.client.default_headers)
    llm.update_config(_cfg('https://opencode.ai/v1'))
    assert dict(llm.client.default_headers)['x-opencode-session'] == sid


def test_other_providers_no_header():
    llm = LLMClient(_cfg('https://api.deepseek.com'))
    assert 'x-opencode-session' not in dict(llm.client.default_headers)
    llm2 = LLMClient(_cfg('https://api.openai.com/v1'))
    assert 'x-opencode-session' not in dict(llm2.client.default_headers)


def test_clients_get_distinct_sessions():
    a = LLMClient(_cfg('https://opencode.ai/v1'))
    b = LLMClient(_cfg('https://opencode.ai/v1'))
    ha = dict(a.client.default_headers)['x-opencode-session']
    hb = dict(b.client.default_headers)['x-opencode-session']
    assert ha != hb
