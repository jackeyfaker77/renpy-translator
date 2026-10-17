"""prompt_match：按批内容匹配注入的匹配规则"""
from prompt_match import match_entries


def _entries(*ens):
    return [{'key': i, 'en': en} for i, en in enumerate(ens)]


def test_exact_word_hit():
    hits = match_entries(['We should go to the Kingdom now.'], _entries('kingdom', 'dragon'))
    assert [h['en'] for h in hits] == ['kingdom']


def test_case_insensitive():
    hits = match_entries(['HELLO there, KINGDOM.'], _entries('kingdom'))
    assert [h['en'] for h in hits] == ['kingdom']


def test_boundary_infix_rejects_subword():
    # teammate 里藏着 mate，但不是独立词——不得命中
    hits = match_entries(['The teammate waved.'], _entries('mate'))
    assert hits == []


def test_fuzzy_plural_suffix_hits():
    # mates 是 mate 的边界包含（复数后缀）——模糊命中
    hits = match_entries(['Two mates waved at us.'], _entries('mate'))
    assert [h['en'] for h in hits] == ['mate']


def test_multiword_phrase():
    hits = match_entries(['He said "yes my lord" twice.'], _entries('my lord'))
    assert [h['en'] for h in hits] == ['my lord']


def test_short_entry_skips_fuzzy():
    # 归一化后 2 字符的短条目只认词面命中：'ab' 在 'cab abd' 里只是
    # 边界内嵌（cab/abd 都含 ab），模糊被短条目守卫跳过
    hits = match_entries(['cab abd here'], _entries('ab'))
    assert hits == []


def test_empty_batch_or_entries():
    assert match_entries([], _entries('kingdom')) == []
    assert match_entries(['kingdom here'], []) == []
    assert match_entries(['', '  '], _entries('kingdom')) == []


def test_cross_line_no_false_phrase():
    # 'my' 在一行、'lord' 在另一行：跨行拼接不应命中短语（词面命中会中
    # 单词级——my+lord 词面全中是允许的宽松行为；短语边界包含不应跨行）
    hits = match_entries(['my sword', 'the lord said'], _entries('my sword lord'))
    # 词面：my sword lord 三个词都在 → 精确命中（宽松）；不测跨行短语
    assert [h['en'] for h in hits] == ['my sword lord']
