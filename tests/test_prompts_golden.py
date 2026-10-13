"""提示词 golden 快照：钉住现有 builder 的完整输出

可配置化改造（prompt_store）前后，同输入必须产出逐字一致的提示词。
第一次运行生成 tests/golden/<name>.txt，之后与之对比。
"""
import json
from pathlib import Path

import pytest

from prompts import (
    build_analyze_only_prompt, build_batch_user_prompt, build_continue_prompt,
    build_merge_summaries_prompt, build_name_system_prompt,
    build_name_user_prompt, build_style_guide_prompt, build_system_prompt,
    build_translate_analyze_prompt, build_ui_system_prompt,
    build_ui_user_prompt, build_user_prompt, get_analysis_system_prompt,
)

GOLDEN_DIR = Path(__file__).parent / 'golden'

GLOSSARY = '人名对照表（供参考）：\n  Ricardo → 里卡多\n\n已有术语表（供参考）：\n\n【游戏术语】\n  Kingdom → 王国'
STYLE = '轻松日常，口语化，避免书面腔'
PROFILE = '[角色] 人物特征：\n- 说话风格：急躁、短句'


def _golden(name: str, actual: str):
    """与 tests/golden/<name>.txt 对比；不存在则生成并失败（提示重跑）"""
    GOLDEN_DIR.mkdir(exist_ok=True)
    path = GOLDEN_DIR / f'{name}.txt'
    if not path.exists():
        path.write_text(actual, encoding='utf-8')
        pytest.fail(f'golden 已生成: {path.name}，请重跑测试确认')
    expected = path.read_text(encoding='utf-8')
    assert actual == expected, f'输出与 golden 不一致: {name}'


# ---- prompts.py：对话 / UI / 人名 ----

def test_golden_dialogue_system_empty():
    _golden('dialogue_system_empty', build_system_prompt())


def test_golden_dialogue_system_full():
    _golden('dialogue_system_full',
            build_system_prompt(glossary_text=GLOSSARY,
                                character_profile=PROFILE, style_guide=STYLE))


def test_golden_dialogue_user_plain():
    _golden('dialogue_user_plain', build_user_prompt('Hello world.'))


def test_golden_dialogue_user_full():
    _golden('dialogue_user_full', build_user_prompt(
        'Hello there.',
        character='Alice',
        context_before=[
            {'original_text': 'Good morning.', 'translated_text': '早上好。',
             'character': 'Alice'},
            {'original_text': 'Who is there?', 'translated_text': '',
             'character': ''},
        ],
        context_after=[
            {'original_text': 'Bye.', 'translated_text': '再见。', 'character': 'Bob'},
        ]))


def test_golden_dialogue_batch():
    items = [
        {'original_text': 'Hello world.', 'character': 'Alice', 'label': 'start',
         'context_hint': 'greeting'},
        {'original_text': 'Bye.', 'character': '', 'label': '', 'hint': 'exit'},
    ]
    ctx = [{'original_text': 'Morning.', 'translated_text': '早上好。',
            'character': 'Alice'}]
    _golden('dialogue_batch', build_batch_user_prompt(items, ctx, 'dialogue'))


def test_golden_ui_system_empty():
    _golden('ui_system_empty', build_ui_system_prompt())


def test_golden_ui_system_full():
    _golden('ui_system_full', build_ui_system_prompt(glossary_text=GLOSSARY,
                                                     style_guide=STYLE))


def test_golden_ui_user():
    _golden('ui_user', build_ui_user_prompt('Start Game'))


def test_golden_name_system_empty():
    _golden('name_system_empty', build_name_system_prompt())


def test_golden_name_system_full():
    _golden('name_system_full', build_name_system_prompt(glossary_text=GLOSSARY))


def test_golden_name_user():
    _golden('name_user', build_name_user_prompt('Ricardo'))


# ---- prompts.py：人物分析 4 路径 ----

def test_golden_analyze_only():
    _golden('analyze_only',
            build_analyze_only_prompt('Ricardo', '"Hello there."', 0, 2))


def test_golden_translate_analyze_no_dict():
    _golden('translate_analyze_no_dict',
            build_translate_analyze_prompt('Ricardo', '"Hello there."', 0, 1, ''))


def test_golden_translate_analyze_with_dict():
    _golden('translate_analyze_with_dict',
            build_translate_analyze_prompt('Ricardo', '"Hello there."', 1, 2,
                                           GLOSSARY))


def test_golden_continue_no_summaries():
    _golden('continue_no_summaries',
            build_continue_prompt('Ricardo', '"More lines."', 1, 3, []))


def test_golden_continue_with_summaries():
    _golden('continue_with_summaries',
            build_continue_prompt('Ricardo', '"More lines."', 1, 3,
                                  ['性格急躁', '说话短促']))


def test_golden_merge_summaries():
    _golden('merge_summaries',
            build_merge_summaries_prompt('Ricardo', ['性格急躁', '说话短促']))


def test_golden_analysis_system():
    _golden('analysis_system', get_analysis_system_prompt())


def test_golden_style_guide():
    _golden('style_guide', build_style_guide_prompt('"Hello."'))


# ---- ai_screener：粗筛三变体 / 精审（模板已迁 prompt_store） ----

def test_golden_screener_coarse():
    from prompt_store import render
    items_json = json.dumps([{'id': 1, 'text': 'Start Game'}],
                            ensure_ascii=False)
    _golden('screener_coarse_a',
            render('screener.coarse_a', items_json=items_json))
    _golden('screener_coarse_b',
            render('screener.coarse_b', items_json=items_json))
    _golden('screener_coarse_c',
            render('screener.coarse_c', items_json=items_json))


def test_golden_screener_refine():
    from prompt_store import render
    _golden('screener_refine_system', render('screener.refine_system'))
    _golden('screener_anchor_section', render('screener.anchor_section'))


# ---- relation_graph / scene_summarizer / export_healer ----

def test_golden_relation():
    from prompt_store import render
    chars = json.dumps([{'name': 'Alice', 'cn': '', 'lines': 3, 'profile': ''}],
                       ensure_ascii=False)
    _golden('relation_prompt',
            render('relation.prompt', game='TestGame', chars_json=chars))
    _golden('relation_head',
            render('relation.head', game='TestGame', roster_json=chars,
                   batch_names_json=json.dumps(['Alice'], ensure_ascii=False)))


def test_golden_scene():
    from prompt_store import render
    scenes = json.dumps([{'id': 'start', 'labels': ['start'], 'first': 'Hi',
                          'speakers': ['Alice'], 'lines': 3, 'file': 'a.rpy'}],
                        ensure_ascii=False)
    _golden('scene_prompt', render('scene.summary', scenes_json=scenes))


def test_golden_export_fix():
    from prompt_store import render
    _golden('export_fix', render(
        'export.fix', err_file='game/tl/chinese/a.rpy', err_line=12,
        err_msg='SyntaxError', original='Hello.', current='你好。',
        context='    12│    "你好。"'))


# ---- translator 内联重试 hint（拼接文案契约） ----

RETRY_MUST_FIX = '\n\n【必须修正】上一版译文未通过校验：'
RETRY_BATCH_HINT = '上一版译文未通过校验，必须修正：'


def test_golden_retry_hints():
    """重试 hint 是拼接字面量，钉住模板段文案（Step 3 抽模板后不得变）"""
    _golden('retry_must_fix', RETRY_MUST_FIX + '占位符不一致')
    _golden('retry_batch_hint', RETRY_BATCH_HINT + '占位符不一致')
    # 模板段文案锚点：默认模板须含同款文案（token 可变，句式不变）
    from prompt_store import REGISTRY
    assert '【必须修正】上一版译文未通过校验：' in \
        REGISTRY['retry.must_fix'].default_text
    assert '上一版译文未通过校验，必须修正：' in \
        REGISTRY['retry.batch_hint'].default_text
