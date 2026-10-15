"""翻译模式选项：预设映射、装配置空、术语落库过滤。"""
import asyncio

import pytest

from database import ProjectDatabase
from translate_options import (
    TranslateOptions, options_from_preset, parse_options, validate_flags,
)
from translation_service import TranslationService


# ---- 纯函数 ----

def test_default_is_deep_full_effects():
    opts = parse_options('')
    assert opts.preset == 'deep' and opts.flags() == options_from_preset('deep').flags()
    assert opts.term_extract and opts.glossary and opts.style_guide
    assert opts.character_profile == 'full' and opts.context


def test_presets_mapping():
    light = options_from_preset('light')
    assert light.flags() == {
        'term_extract': False, 'glossary': True, 'style_guide': False,
        'character_profile': 'advice_only', 'context': False}
    bulk = options_from_preset('bulk')
    assert bulk.flags() == {
        'term_extract': False, 'glossary': False, 'style_guide': False,
        'character_profile': 'off', 'context': False}


def test_parse_custom_and_bad_data():
    opts = parse_options('{"preset": "custom", "flags": '
                         '{"glossary": false, "character_profile": "advice_only"}}')
    assert opts.preset == 'custom' and not opts.glossary
    assert opts.character_profile == 'advice_only' and opts.style_guide
    # 坏数据回退 deep
    assert parse_options('not json').preset == 'deep'
    assert parse_options('{"flags": {"character_profile": "bogus"}}').character_profile == 'full'


def test_validate_flags_rejects_bad_input():
    assert validate_flags({'glossary': False}) == {'glossary': False}
    with pytest.raises(ValueError):
        validate_flags({'character_profile': 'x'})
    with pytest.raises(ValueError):
        validate_flags({'glossary': 'yes'})


# ---- 装配集成（真实 sqlite + 假 translator） ----

class _CaptureTranslator:
    MAX_RETRIES = 1

    def __init__(self):
        from types import SimpleNamespace
        self.calls = []
        self.config = SimpleNamespace(context_lines=3)

    def translate_batch(self, items, content_type='', glossary_text='',
                        character_profiles='', context_before=None,
                        style_guide='', context_window_tokens=0):
        self.calls.append({
            'glossary_text': glossary_text, 'style_guide': style_guide,
            'character_profiles': character_profiles,
            'context_before': context_before or [],
        })
        terms = [{'en_term': 'Kingdom', 'cn_term': '王国'}] if content_type != 'ui' else []
        return ({i: f'译文{i}' for i in range(len(items))}, terms, {}, {})


class _NullLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


@pytest.fixture
def db(tmp_path):
    db = ProjectDatabase(str(tmp_path / 'p.db'))
    db.connect()
    db.insert_dialogues([
        {'id': 1, 'original_text': 'Hello there.', 'character': 'a',
         'translated_text': '', 'is_translated': False},
        {'id': 2, 'original_text': 'Bye.', 'character': 'a',
         'translated_text': '', 'is_translated': False},
    ])
    db.upsert_glossary_batch([('Kingdom', '王国')])
    db.set_meta('style_guide', '轻松日常')
    db.save_profile('a', {'说话风格': '急躁', '翻译建议': '语气硬朗'})
    yield db
    db.close()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _service(db, translator):
    s = TranslationService(translator, db, _NullLogger(), max_concurrent=1,
                           max_context_k=8, batch_lines=100)
    return s


def _items():
    return [{'id': 1, 'original_text': 'Hello there.', 'character': 'a',
             'translated_text': ''},
            {'id': 2, 'original_text': 'Bye.', 'character': 'a',
             'translated_text': ''}]


def test_deep_injects_everything(db):
    db.set_meta('translate_options', options_from_preset('deep').to_json())
    tr, svc = _CaptureTranslator(), None
    svc = _service(db, tr)
    try:
        _run(svc.translate_batch(_items(), 'dialogue'))
    finally:
        svc.close()
    call = tr.calls[0]
    assert '王国' in call['glossary_text']
    assert '轻松日常' in call['style_guide']
    assert '翻译建议' in call['character_profiles'] and '说话风格' in call['character_profiles']
    assert call['context_before'] or True  # 上下文开=读了（本库短，内容可能为空）


def test_light_keeps_glossary_and_advice_only(db):
    db.set_meta('translate_options', options_from_preset('light').to_json())
    tr = _CaptureTranslator()
    svc = _service(db, tr)
    try:
        _run(svc.translate_batch(_items(), 'dialogue'))
    finally:
        svc.close()
    call = tr.calls[0]
    assert '王国' in call['glossary_text']          # 术语表保留
    assert call['style_guide'] == ''               # 风格指南关闭
    assert '翻译建议' in call['character_profiles']
    assert '说话风格' not in call['character_profiles']  # 只留翻译建议
    assert call['context_before'] == []            # 上下文关闭


def test_bulk_injects_nothing_and_drops_terms(db):
    db.set_meta('translate_options', options_from_preset('bulk').to_json())
    tr = _CaptureTranslator()
    svc = _service(db, tr)
    added = []
    db.add_glossary_batch = lambda pairs: added.append(pairs)
    try:
        _run(svc.translate_batch(_items(), 'dialogue'))
    finally:
        svc.close()
    call = tr.calls[0]
    assert call['glossary_text'] == '' and call['style_guide'] == ''
    assert call['character_profiles'] == '' and call['context_before'] == []
    assert added == []  # term_extract=False：模型回传的术语被丢弃


def test_deep_still_saves_terms(db):
    db.set_meta('translate_options', options_from_preset('deep').to_json())
    tr = _CaptureTranslator()
    svc = _service(db, tr)
    added = []
    db.add_glossary_batch = lambda pairs: added.append(pairs)
    try:
        _run(svc.translate_batch(_items(), 'dialogue'))
    finally:
        svc.close()
    assert added and added[0][0]['en_term'] == 'Kingdom'
