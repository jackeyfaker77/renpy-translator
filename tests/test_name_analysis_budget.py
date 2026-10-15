"""人物分析成本上限：抽样封顶、调用次数、不发 max_tokens、取消不落库。"""
import asyncio
import random

from services.name_translation import (
    NameTranslationService, format_style_signals, parse_profile,
    split_self_check,
)


class _StubLogger:
    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class _StubDB:
    def __init__(self, rows):
        self._rows = rows
        self.cn_saved = []
        self.profiles_saved = []

    def get_characters(self):
        return [{'variable': 'r', 'display_name': 'Ricardo', 'cn_name': '',
                 'profile_json': '', 'is_placeholder': False}]

    def get_glossary_for_prompt(self):
        return ''

    def get_characters_for_prompt(self):
        return ''

    def get_variable_map(self):
        return {'r': 'Ricardo'}

    def get_dialogues_by_character(self, var):
        return self._rows

    def get_all_dialogues(self):
        return self._rows

    def update_character_cn_name(self, display_name, cn_name, variable=None):
        self.cn_saved.append((display_name, cn_name, variable))

    def save_profile(self, display_name, profile, variable=None):
        self.profiles_saved.append((display_name, profile))


class _StubTranslator:
    """记录调用次数与 kwargs；输出带【自检】段的合法档案"""

    RESULT = ('【人名翻译】\n中文名：里卡多\n\n【人物分析】\n'
              '性格特点：急躁\n说话风格：短促\n口癖与口头禅：mate\n'
              '称谓习惯：my lord\n代表台词：Well bloody hell.\n'
              '语气词建议：多用「嘛」\n翻译建议：语气硬朗\n\n'
              '【自检】\n证据不足：无')

    def __init__(self, on_call=None):
        self.calls = []
        self.on_call = on_call

    def translate_name(self, name, glossary_text='', debug=False):
        return '里卡多'

    def analyze_text(self, prompt=None, **kwargs):
        self.calls.append({'prompt': prompt, 'kwargs': kwargs})
        if self.on_call:
            self.on_call(len(self.calls))
        return self.RESULT


def _rows(n):
    rng = random.Random(11)
    words = ('think should market today bread weather strange agree nothing '
             'situation maybe tomorrow better world night morning water fire').split()
    return [
        {'id': i, 'label': f'scene{i % 30}',
         'original_text': (' '.join(rng.choice(words) for _ in range(9))).capitalize()
         + (' mate.' if i % 40 == 0 else '.')}
        for i in range(n)
    ]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _service(db, translator):
    return NameTranslationService(
        db=db, translator=translator, translation_service=None,
        logger=_StubLogger(), max_context_k=8)


def test_large_character_calls_capped_at_three():
    """5000 句角色：Pass1 + Pass2 + merge 共 ≤3 次调用（旧流程 30+ 次）"""
    db, tr = _StubDB(_rows(5000)), _StubTranslator()
    svc = _service(db, tr)
    _run(svc.translate_and_analyze('Ricardo', variable='r', mode='both'))
    assert 1 <= len(tr.calls) <= 3
    assert len(db.profiles_saved) == 1
    assert db.cn_saved == [('Ricardo', '里卡多', 'r')]


def test_never_sends_max_tokens():
    db, tr = _StubDB(_rows(500)), _StubTranslator()
    svc = _service(db, tr)
    _run(svc.translate_and_analyze('Ricardo', variable='r', mode='analyze'))
    assert tr.calls
    for call in tr.calls:
        assert 'max_tokens' not in call['kwargs']


def test_small_character_single_call():
    db, tr = _StubDB(_rows(50)), _StubTranslator()
    svc = _service(db, tr)
    _run(svc.translate_and_analyze('Ricardo', variable='r', mode='analyze'))
    assert len(tr.calls) == 1
    assert len(db.profiles_saved) == 1


def test_cancel_discards_profile():
    """用户取消：已跑的 Pass1 结果不落库，避免半截档案整覆盖"""
    db = _StubDB(_rows(5000))

    def on_call(n):
        if n == 1:
            svc.stop()

    tr = _StubTranslator(on_call=on_call)
    svc = _service(db, tr)
    _run(svc.translate_and_analyze('Ricardo', variable='r', mode='analyze'))
    assert db.profiles_saved == []


def test_parse_profile_keeps_new_keys_and_strips_self_check():
    head, tail = split_self_check(_StubTranslator.RESULT)
    assert '【自检】' in tail and '【自检】' not in head
    profile = parse_profile(head)
    assert profile['口癖与口头禅'] == 'mate'
    assert profile['称谓习惯'] == 'my lord'
    assert profile['代表台词'] == 'Well bloody hell.'
    assert profile['语气词建议'] == '多用「嘛」'
    assert '证据不足' not in profile.get('翻译建议', '')


def test_format_style_signals_sections():
    text = format_style_signals(
        {'tics': [{'word': 'mate', 'count': 16, 'scenes': 8}],
         'addresses': [{'form': 'my lord', 'count': 16}],
         'punctuation': [{'form': '感叹号结尾', 'count': 12}]},
        {'words': 900, 'low_evidence': True, 'single_scene_tics': ['solo']})
    assert '口癖候选：mate（16句/8幕）' in text
    assert '称谓线索：my lord（16次）' in text
    assert '低于 3000 词' in text
    assert 'solo' in text
    assert format_style_signals({}, {'words': 0}) == ''
