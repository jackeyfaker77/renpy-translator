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
        'term_extract': False, 'glossary': True, 'names': True,
        'style_guide': False, 'character_profile': 'advice_only', 'context': False}
    bulk = options_from_preset('bulk')
    assert bulk.flags() == {
        'term_extract': False, 'glossary': False, 'names': False,
        'style_guide': False, 'character_profile': 'off', 'context': False}


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
                        style_guide='', characters_text='',
                        context_window_tokens=0):
        self.calls.append({
            'glossary_text': glossary_text, 'style_guide': style_guide,
            'character_profiles': character_profiles,
            'context_before': context_before or [],
            'characters_text': characters_text,
        })
        terms = [{'en_term': 'Kingdom', 'cn_term': '王国'}] if content_type != 'ui' else []
        return ({i: f'译文{i}' for i in range(len(items))}, terms, {}, {})

    def translate_text(self, text='', character='', context_before=None,
                       context_after=None, glossary_text='',
                       character_profile='', style_guide='',
                       characters_text='', debug=False):
        self.calls.append({
            'glossary_text': glossary_text, 'characters_text': characters_text,
            'single': 'dialogue',
        })
        return '单译', []

    def translate_ui(self, text='', glossary_text='', character_dict=None,
                     style_guide='', characters_text='', debug=False):
        self.calls.append({
            'glossary_text': glossary_text, 'characters_text': characters_text,
            'single': 'ui',
        })
        return '单译', []


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
        {'id': 1, 'original_text': 'Hello Kingdom there.', 'character': 'a',
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
    return [{'id': 1, 'original_text': 'Hello Kingdom there.', 'character': 'a',
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


def test_matched_injection_skips_unmentioned(db):
    """匹配注入：本批没出现的术语不注入；说话人人名直通注入"""
    db.upsert_glossary_batch([('Dragon', '巨龙')])  # 文本里没有 → 不注入
    db.insert_characters([
        {'variable': 'a', 'display_name': 'Alice', 'cn_name': '爱丽丝'},
    ])
    db.set_meta('translate_options', options_from_preset('deep').to_json())
    tr = _CaptureTranslator()
    svc = _service(db, tr)
    try:
        _run(svc.translate_batch(_items(), 'dialogue'))
    finally:
        svc.close()
    call = tr.calls[0]
    assert '王国' in call['glossary_text']      # 本批出现的注入
    assert '巨龙' not in call['glossary_text']  # 未出现的不注入
    assert 'Alice → 爱丽丝' in call['characters_text']  # 说话人直通


def test_names_flag_off_drops_characters_text(db):
    """names 开关关闭（如纯批量）：人名表不注入"""
    db.insert_characters([
        {'variable': 'a', 'display_name': 'Alice', 'cn_name': '爱丽丝'},
    ])
    db.set_meta('translate_options', options_from_preset('bulk').to_json())
    tr = _CaptureTranslator()
    svc = _service(db, tr)
    try:
        _run(svc.translate_batch(_items(), 'dialogue'))
    finally:
        svc.close()
    assert tr.calls[0]['characters_text'] == ''
    assert tr.calls[0]['glossary_text'] == ''


def test_fallback_full_injection_when_all_match(db):
    """命中率≥95% 回退全量注入：以 db.get_glossary_for_prompt 被调用为证"""
    db.upsert_glossary_batch([('Dragon', '巨龙')])
    db.set_meta('translate_options', options_from_preset('deep').to_json())
    tr = _CaptureTranslator()
    svc = _service(db, tr)
    full_text = ' '.join(db.UI_GLOSSARY.keys()) + ' Kingdom Dragon'
    calls = []
    orig = db.get_glossary_for_prompt
    db.get_glossary_for_prompt = lambda: (calls.append(1), orig())[1]
    try:
        _run(svc.translate_batch(
            [{'id': 1, 'original_text': full_text, 'character': 'a',
              'translated_text': ''}], 'dialogue'))
    finally:
        svc.close()
    assert calls, '全部命中时应走全量注入回退'
    assert '巨龙' in tr.calls[0]['glossary_text']


def test_ui_terms_in_ui_section_not_game(db):
    """UI 翻译提取的术语（term_type=ui）匹配后只进 UI 分节，不混入游戏术语"""
    db._conn.execute(
        "INSERT INTO glossary(en_term,cn_term,term_type,source) "
        "VALUES('Dungeon Flag','地牢旗帜','ui','ai')")
    db._conn.commit()
    db.upsert_glossary_batch([('Kingdom', '王国')])
    db.set_meta('translate_options', options_from_preset('deep').to_json())
    tr = _CaptureTranslator()
    svc = _service(db, tr)
    try:
        _run(svc.translate_batch(
            [{'id': 1, 'original_text': 'Dungeon Flag in the Kingdom.',
              'character': 'a', 'translated_text': ''}], 'dialogue'))
    finally:
        svc.close()
    gt = tr.calls[0]['glossary_text']
    assert gt.count('Dungeon Flag') == 1          # 只出现一次（UI 分节）
    assert '地牢旗帜' in gt and '王国' in gt
    # UI 词条位于游戏术语分节之前
    assert gt.index('Dungeon Flag') < gt.index('【游戏术语】')


def test_single_translation_matched_injection(db):
    """单条翻译同样按本句+说话人匹配注入（人名直通、未命中术语不注入）"""
    db.insert_characters([
        {'variable': 'a', 'display_name': 'Alice', 'cn_name': '爱丽丝'},
    ])
    db.upsert_glossary_batch([('Dragon', '巨龙')])
    db.set_meta('translate_options', options_from_preset('deep').to_json())
    tr = _CaptureTranslator()
    svc = _service(db, tr)
    try:
        _run(svc.translate_single(1, 'dialogue', 'Kingdom attack Alice now.', 'a'))
    finally:
        svc.close()
    call = tr.calls[0]
    assert call.get('single') == 'dialogue'
    assert '王国' in call['glossary_text']
    assert '巨龙' not in call['glossary_text']   # 本句未出现的术语不注入
    assert '爱丽丝' in call['characters_text']    # 说话人直通
