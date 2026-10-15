"""line_sampling：去重、口癖候选、槽位配额、证据门控、可复现。

合成语料结构参考 DSH 调研原型 demo_sampling.py（results.md），
断言校准目标：稀缺口癖（0.5%~2%）在 300 句预算下的召回显著优于
均匀抽样（原型实测均匀 ~17%，槽位组合 ~80%）。
"""
import random

from line_sampling import (
    corpus_background, dedup_lines, evidence_flags, log_odds_distinctiveness,
    portfolio_select, rare_distinctive_words, sample_for_analysis,
    score_lines,
)

FILLER = (
    'think should market today bread weather strange lately agree nothing about '
    'situation now maybe tomorrow better day here always never people story world '
    'night morning water fire road house door window light dark quiet loud slow '
    'quick small large old young friend enemy question answer promise secret'
).split()


def _make_line(rng, n=9):
    return (' '.join(rng.choice(FILLER) for _ in range(n))).capitalize() + '.'


def _line(i, text, label=''):
    return {'id': i, 'original_text': text, 'label': label or f'scene{i % 20}'}


def _build_corpus(seed=42):
    """目标角色 800 句 + 背景 1200 句；注入低频口癖与称谓。"""
    rng = random.Random(seed)
    char, bg = [], []
    bloody = {30, 200, 400, 700}                                    # 0.5%
    mate = {50, 90, 130, 170, 210, 250, 290, 330,
            370, 410, 450, 490, 530, 570, 610, 650}                 # 2%
    lord = {60, 110, 160, 220, 280, 340, 420, 480,
            540, 600, 660, 720, 750, 770, 780, 790}                 # 2%
    n = 0
    for i in range(800):
        text = _make_line(rng)
        if i in bloody:
            text = f'Bloody hell, {text.lower().rstrip(".")} again.'
        elif i in mate:
            text = f'Come on mate, {text.lower().rstrip(".")} right now.'
        elif i in lord:
            text = f'Please my lord, {text.lower().rstrip(".")} at once.'
        char.append(_line(n, text))
        n += 1
    for i in range(1200):
        bg.append(_line(n, _make_line(rng)))
        n += 1
    return char, bg, {'bloody': bloody, 'mate': mate, 'lord': lord}


def test_exact_and_near_dedupe():
    lines = [_line(1, 'Yes.'), _line(2, 'yes!'), _line(3, 'Yes.'),
             _line(4, 'This is a completely different sentence altogether.')]
    kept = dedup_lines(lines)
    texts = sorted(l['original_text'] for l in kept)
    assert texts == ['This is a completely different sentence altogether.', 'Yes.']


def test_short_and_junk_lines_dropped_by_scoring():
    lines = [_line(1, '...'), _line(2, 'Hi'), _line(3, 'Okay'),
             _line(4, 'This sentence has enough tokens to be scored properly today.')]
    scored = score_lines(lines, {})
    assert [l['original_text'] for l, _ in scored] == \
        ['This sentence has enough tokens to be scored properly today.']


def test_rare_tic_detection_needs_repetition():
    rng = random.Random(1)
    # DF 口径：口癖要在自己台词里也稀有（<8% 行）才算候选，
    # 所以 5 句口癖需要 ~120 行总量才过 DF 门槛（4% < 8%）
    lines = [_line(i, f'Well bloody hell {_make_line(rng, 6).lower().rstrip(".")}', 'a')
             for i in range(5)]
    lines += [_line(100 + i, _make_line(rng), 'b') for i in range(120)]
    bg = corpus_background([_line(900 + i, _make_line(rng)) for i in range(200)])
    z = log_odds_distinctiveness(lines, bg)
    cands = rare_distinctive_words(z, lines)
    assert 'bloody' in cands
    # 只出现 1 次的词不配额
    lines.append(_line(200, f'A singular zebra {_make_line(rng, 6).lower().rstrip(".")}', 'c'))
    z2 = log_odds_distinctiveness(lines, bg)
    assert 'zebra' not in rare_distinctive_words(z2, lines)


def test_evidence_gate_flags():
    lines = [_line(i, 'Short line here with several tokens indeed.') for i in range(20)]
    flags = evidence_flags(lines, {'solo': {'scene0'}})
    assert flags['low_evidence'] and flags['single_scene_tics'] == ['solo']
    heavy = [_line(i, 'This is a sufficiently long line carrying real words for counting.')
             for i in range(400)]
    assert not evidence_flags(heavy, {})['low_evidence']


def test_portfolio_reproducible_and_capped():
    char, bg, _ = _build_corpus()
    r1 = sample_for_analysis(char, bg, budget=300, rng=random.Random(7))
    r2 = sample_for_analysis(char, bg, budget=300, rng=random.Random(7))
    assert [l['id'] for l in r1['selected']] == [l['id'] for l in r2['selected']]
    assert r1['stats']['capped'] and r1['stats']['selected'] <= 300
    assert r1['core'] and r1['support']


def test_small_character_keeps_all_lines():
    rng = random.Random(3)
    char = [_line(i, _make_line(rng)) for i in range(40)]
    result = sample_for_analysis(char, char, budget=300, rng=random.Random(0))
    assert not result['stats']['capped']
    assert len(result['selected']) == len(result['core']) == len(char)


def test_rare_tic_recall_beats_uniform_expectation():
    """合成语料回归：0.5%~2% 口癖在 300 预算下召回（均匀期望 ~15-20%）"""
    char, bg, marks = _build_corpus()
    result = sample_for_analysis(char, bg, budget=300, rng=random.Random(0))
    picked = {l['id'] for l in result['selected']}

    def recall(id_set):
        return len(id_set & picked) / len(id_set)

    assert recall(marks['bloody']) >= 0.5    # 4 句里至少 2 句（配额保 4，除非被去重）
    assert recall(marks['mate']) >= 0.3
    assert recall(marks['lord']) >= 0.3
    assert result['signals']['tics']  # 口癖候选进了 style_signals
    assert result['signals']['addresses']


def test_portfolio_slots_respect_budget():
    rng = random.Random(5)
    scored = [(_line(i, _make_line(rng)), float(i % 17)) for i in range(500)]
    core, support = portfolio_select(scored, budget=100, n_scenes=10,
                                     rare_cands={}, rng=random.Random(0))
    assert len(core) + len(support) <= 100
    assert len(core) >= 30  # 口癖+通用槽是主力
