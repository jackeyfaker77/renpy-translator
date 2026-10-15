"""跨项目导入：源库读取、分组预览、digest 防错配、类型白名单。"""
import pytest

from database import ProjectDatabase
from services.text_import import (
    apply_plan, build_cross_project_plan, read_translated_pairs,
)


@pytest.fixture
def dbs(tmp_path):
    src = ProjectDatabase(str(tmp_path / 'src.db'))
    src.connect()
    src.insert_dialogues([
        {'original_text': 'Hello, [player_name]!', 'translated_text': '你好！',
         'is_translated': True},
        {'original_text': 'Good morning.', 'translated_text': '早上好。',
         'is_translated': True},
        {'original_text': 'See you tomorrow.', 'translated_text': '明天见。',
         'is_translated': True},
        {'original_text': 'Untranslated source line.', 'translated_text': '',
         'is_translated': False},
    ])
    src.insert_ui_texts([
        {'original_text': 'Start Game', 'translated_text': '开始游戏', 'is_translated': True},
    ])
    src.insert_characters([
        {'variable': 'r', 'display_name': 'Ricardo', 'cn_name': '里卡多'},
        {'variable': 'x', 'display_name': 'Nobody', 'cn_name': ''},
    ])
    tgt = ProjectDatabase(str(tmp_path / 'tgt.db'))
    tgt.connect()
    tgt.insert_dialogues([
        # 归一化可匹配（变量名不同）+ 当前未译 → direct
        {'original_text': 'Hello, [mc_name]!', 'translated_text': '', 'is_translated': False},
        # 译文不同 → conflict
        {'original_text': 'Good morning.', 'translated_text': '早安。', 'is_translated': True},
        # 模糊相似（改词）→ review
        {'original_text': 'See you tomorrow evening.', 'translated_text': '', 'is_translated': False},
        # 无匹配
        {'original_text': 'Completely unrelated line.', 'translated_text': '', 'is_translated': False},
    ])
    tgt.insert_ui_texts([
        {'original_text': 'Start Game', 'translated_text': '开始游戏', 'is_translated': True},
    ])
    tgt.insert_characters([
        {'variable': 'r', 'display_name': 'Ricardo', 'cn_name': ''},
    ])
    yield src, tgt
    src.close()
    tgt.close()


def test_read_pairs_rejects_glossary(dbs):
    src, _tgt = dbs
    with pytest.raises(ValueError, match='仅支持'):
        read_translated_pairs(src, ['glossary'])
    pairs = read_translated_pairs(src, ['dialogue', 'ui', 'names'])
    assert dict(pairs['dialogue'])['Hello, [player_name]!'] == '你好！'
    # 未译条目不进源对
    assert 'Untranslated source line.' not in dict(pairs['dialogue'])
    assert dict(pairs['ui']) == {'Start Game': '开始游戏'}
    assert dict(pairs['names']) == {'Ricardo': '里卡多'}


def test_preview_groups(dbs):
    src, tgt = dbs
    pairs = read_translated_pairs(src, ['dialogue', 'ui', 'names'])
    plan = build_cross_project_plan(tgt, pairs, ['dialogue', 'ui', 'names'])

    direct = {(r['type'], r['original']): r for r in plan['direct']}
    assert ('dialogue', 'Hello, [mc_name]!') in direct
    assert direct[('dialogue', 'Hello, [mc_name]!')]['incoming'] == '你好！'
    # names：未译名 + 精确匹配 → direct
    assert ('names', 'Ricardo') in direct

    conflict = {(r['type'], r['original']): r for r in plan['conflict']}
    assert conflict[('dialogue', 'Good morning.')]['current'] == '早安。'
    assert conflict[('dialogue', 'Good morning.')]['incoming'] == '早上好。'

    assert plan['review'] and plan['review'][0]['kind'] == 'fuzzy'
    assert plan['review'][0]['score'] < 0.95
    assert plan['same'].get('ui') == 1   # Start Game 译文一致跳过
    assert plan['digest']


def test_digest_changes_with_state(dbs):
    src, tgt = dbs
    pairs = read_translated_pairs(src, ['dialogue'])
    p1 = build_cross_project_plan(tgt, pairs, ['dialogue'])
    # 预览后目标库被改动 → digest 失效（apply 必须重新预览）
    tgt.update_dialogue(
        next(r['id'] for r in tgt.get_all_dialogues()
             if r['original_text'] == 'Good morning.'), '改过了')
    p2 = build_cross_project_plan(tgt, pairs, ['dialogue'])
    assert p1['digest'] != p2['digest']


def test_apply_writes_selected(dbs):
    src, tgt = dbs
    pairs = read_translated_pairs(src, ['dialogue'])
    plan = build_cross_project_plan(tgt, pairs, ['dialogue'])
    direct = plan['direct']
    assert direct
    counts = apply_plan(tgt, [{'type': r['type'], 'key': r['key'],
                               'translated': r['incoming']} for r in direct])
    assert counts['dialogue'] == len(direct)
    saved = {r['original_text']: r['translated_text'] for r in tgt.get_all_dialogues()}
    assert saved['Hello, [mc_name]!'] == '你好！'
    assert saved['Good morning.'] == '早安。'  # conflict 未选则不动
