"""text_fuzzy：归一化、分层阈值、短句保护、倒排预筛不漏候选。"""
from text_fuzzy import (
    AUTO_THRESH, match_texts, normalize_original, similarity,
)


def test_normalize_absorbs_markup_vars_and_style():
    a = normalize_original('Hello,  [player_name]! {i}Good{/i} morning…')
    b = normalize_original('Hello, [mc_name]! Good morning...')
    assert a == b
    assert normalize_original('“Hi” there') == normalize_original('"Hi" there')
    assert normalize_original('Bye $hero_name') == normalize_original('Bye [x]')


def test_exact_and_normalized_matches_are_auto():
    targets = [
        {'key': 1, 'original': 'Hello, [player_name]!', 'translated': ''},
        {'key': 2, 'original': 'Good morning.', 'translated': '早上好'},
        {'key': 3, 'original': 'See you tomorrow.', 'translated': ''},
    ]
    sources = [
        {'key': 90, 'original': 'Hello, [mc_name]!', 'translated': '你好，[mc_name]！'},
        {'key': 91, 'original': 'Good morning.', 'translated': '早上好。'},
        {'key': 92, 'original': 'See you next week.', 'translated': '下周见。'},
    ]
    m = match_texts(targets, sources)
    auto = {r['target_key']: r for r in m['auto']}
    assert auto[1]['kind'] == 'normalized' and auto[1]['source_key'] == 90
    assert auto[2]['kind'] == 'exact'
    # 明显不同（tomorrow vs next week）落进疑似区或不匹配，不得进 auto
    assert 3 not in auto


def test_fuzzy_band_and_below_floor():
    targets = [
        {'key': 1, 'original': 'I cannot believe this is happening right now.',
         'translated': ''},
        {'key': 2, 'original': 'The weather is nice today, shall we go out?',
         'translated': ''},
    ]
    sources = [
        # 与 1 中等相似（改了两个词）→ 疑似区（人工筛选）
        {'key': 90, 'original': 'I cannot believe that was happening right now.',
         'translated': '真不敢相信。'},
        # 与 2 完全不相干 → 不匹配
        {'key': 91, 'original': 'He never listens to anyone in this house.',
         'translated': ''},
    ]
    m = match_texts(targets, sources)
    assert all(r['target_key'] != 1 for r in m['auto'])
    fuzzy = {r['target_key']: r for r in m['fuzzy']}
    assert 1 in fuzzy and fuzzy[1]['score'] >= 0.75
    assert 2 not in fuzzy


def test_short_lines_only_exact_normalized():
    targets = [{'key': 1, 'original': 'Yes.', 'translated': ''}]
    sources = [{'key': 90, 'original': 'Yes!', 'translated': '是。'}]
    m = match_texts(targets, sources)
    assert m['fuzzy'] == [] and m['unmatched'] == 1
    # 归一化全等（大小写/空白差异）仍算
    targets2 = [{'key': 2, 'original': 'Yes.  ', 'translated': ''}]
    sources2 = [{'key': 91, 'original': 'yes.', 'translated': '是。'}]
    assert match_texts(targets2, sources2)['auto'][0]['score'] == 1.0


def test_prefilter_does_not_miss_real_candidates():
    # 大量无关源 + 一个真实近似句：倒排预筛不得把它漏掉
    targets = [{'key': 1,
                'original': 'Please my lord, give us your order at once.',
                'translated': ''}]
    sources = [{'key': i, 'original': f'Unrelated filler sentence number {i} '
                                      f'about ordinary topics here.',
                'translated': ''} for i in range(200)]
    sources.append({'key': 999,
                    'original': 'Please my lord, give us your orders at once!',
                    'translated': '请下令。'})
    m = match_texts(targets, sources)
    hit = m['auto'] + m['fuzzy']
    assert hit and hit[0]['source_key'] == 999 and hit[0]['score'] >= 0.75


def test_similarity_bounds():
    assert similarity('', 'abc') == 0.0
    assert similarity('abc', 'abc') == 1.0
    assert 0.0 < similarity('hello world', 'hello there') < 1.0
    assert AUTO_THRESH == 0.95
