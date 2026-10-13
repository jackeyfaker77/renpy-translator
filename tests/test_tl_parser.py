"""动态说话人游戏解析测试：带点说话人台词入库

背景：Lab Rats 2 的主角台词全是 `mc.name "..."` 带点说话人形式，
tl_parser 原正则只认单词变量导致其 20% 台词被静默丢弃。
"""
import pytest
from pathlib import Path

from renpy_parser import RenpyParser
from tl_parser import parse_translation_files

TL_CONTENT = '''\
# 来自 Ren'Py 模板结构：location 注释 + 原语注释 + 重放行成对

translate chinese test_label_12345678:

    # game/scripts/foo.rpy:10
    # mom "Hello there."
    mom "Hello there."

# game/scripts/foo.rpy:11
    # mc.name "Hey [lily.title]."
    mc.name "Hey [lily.title]."

# game/scripts/foo.rpy:12
    # "Narration line."
    "Narration line."

# game/scripts/foo.rpy:13
    # "Janitor" "Hey, who's making all that noise?"
    "Janitor" "Hey, who's making all that noise?"

# game/scripts/foo.rpy:14
    # the_group[0] "We say hi together."
    the_group[0] "We say hi together."

# game/scripts/foo.rpy:15
    # "Narration with args." (what_color="#8c8")
    "Narration with args." (what_color="#8c8")

translate chinese strings:

    # game/scripts/foo.rpy:20
    old "Menu Choice"
    new ""
'''


@pytest.fixture
def tl_project(tmp_path):
    """构造含三种说话人形式的 tl 模板目录与游戏目录

    目录层级须与真实项目一致：<root>/game/tl/chinese/…，tl_parser 依
    relative_to 剥离 tl/<语言> 前缀还原源脚本路径
    """
    tl_dir = tmp_path / 'game' / 'tl' / 'chinese' / 'scripts'
    tl_dir.mkdir(parents=True)
    (tl_dir / 'foo.rpy').write_text(TL_CONTENT, encoding='utf-8')
    return parse_translation_files(
        tmp_path / 'game' / 'tl' / 'chinese', tmp_path)


def test_dotted_speaker_parsed(tl_project):
    """mc.name 等带点说话人入库并归一为根标识符 mc"""
    by_char = {(d['character'], d['original_text']) for d in tl_project['dialogues']}
    assert ('mc', 'Hey [lily.title].') in by_char
    assert ('mom', 'Hello there.') in by_char
    assert ('', 'Narration line.') in by_char


def test_quoted_speaker_parsed(tl_project):
    """字符串字面量说话人（"Janitor" "..."，动态角色名）整句入库，
    去引号归一——原实现被旁白模式误吞成 original_text='Janitor' 的
    垃圾旁白，真实台词完全丢失"""
    by_char = {(d['character'], d['original_text']) for d in tl_project['dialogues']}
    assert ('Janitor', "Hey, who's making all that noise?") in by_char
    # 不得产出误吞的垃圾旁白
    assert ('', 'Janitor') not in by_char


def test_subscript_speaker_parsed(tl_project):
    """下标说话人 the_group[0] 入库并归一为根变量 the_group"""
    by_char = {(d['character'], d['original_text']) for d in tl_project['dialogues']}
    assert ('the_group', 'We say hi together.') in by_char


def test_narration_with_say_args(tl_project):
    """带 say 参数的旁白：参数里的引号不得混进原文"""
    by_char = {(d['character'], d['original_text']) for d in tl_project['dialogues']}
    assert ('', 'Narration with args.') in by_char


def test_say_line_number_from_comment(tl_project):
    mc = next(d for d in tl_project['dialogues'] if d['character'] == 'mc')
    assert mc['line_number'] == 11
    assert mc['file_path'].endswith(str(Path('game') / 'scripts' / 'foo.rpy'))


def test_subdir_file_has_no_lang_layer(tl_project):
    """tl 子目录文件的 file_path 不应多一层语言段（回归：Lab Rats 2
    曾有 5112 条指向不存在的 game\\chinese\\…）"""
    mc = next(d for d in tl_project['dialogues'] if d['character'] == 'mc')
    assert str(Path('game', 'chinese')) not in mc['file_path']


def test_strings_still_go_to_ui(tl_project):
    assert len(tl_project['ui_texts']) == 1
    assert tl_project['ui_texts'][0]['original_text'] == 'Menu Choice'


# ---- 工厂函数构造的角色名提取(Lab Rats 2 型游戏) ----

def test_factory_person_name_extracted():
    """lily = create_random_person(name = "Lily") 提取出显示名"""
    p = RenpyParser()
    chars = p.extract_characters(
        'lily = create_random_person(name = "Lily", age = 19)', 'x.rpy')
    assert [(c.variable, c.name) for c in chars] == [('lily', 'Lily')]


def test_factory_positional_name():
    """x = Person("Lily", ...) 位置参数形式同样提取"""
    p = RenpyParser()
    chars = p.extract_characters(
        'x = Person("Lily", "Smith", 19)', 'x.rpy')
    assert [(c.variable, c.name) for c in chars] == [('x', 'Lily')]


def test_factory_name_not_marked_dynamic():
    """工厂提取的名字不是动态名，不应被 [name] 包裹"""
    p = RenpyParser()
    chars = p.extract_characters(
        'mom = create_random_person(name = "Jennifer")', 'x.rpy')
    assert chars[0].name == 'Jennifer'


def test_nameless_character_constructor():
    """mc = MainCharacter(...) 玩家命名主角：display_name 留空——
    名字运行时才有，AI 不翻译空名（避免把 mc 译成"麦克"），只做分析"""
    p = RenpyParser()
    chars = p.extract_characters(
        'mc = MainCharacter(bedroom, character_name, last_name)', 'x.rpy')
    assert [(c.variable, c.name) for c in chars] == [('mc', '')]


def test_generic_variable_not_character():
    """普通变量赋值（非角色构造）不误判为角色"""
    p = RenpyParser()
    chars = p.extract_characters(
        'the_person = some_list[0]\nwinner = "Alice"\n', 'x.rpy')
    assert chars == []


def test_character_define_variants():
    """官方与社区常见的 Character 定义变体都识别"""
    for line in (
        'define e = Character("Eileen")',
        'default e = Character("Eileen")',
        'e = renpy.Character("Eileen")',
        '$ e = Character("Eileen")',
        'store.e = Character("Eileen")',
        'define e = DynamicCharacter("persistent.name")',
    ):
        # 每行新实例：RenpyParser 按变量名跨文件去重
        chars = RenpyParser().extract_characters(line, 'x.rpy')
        assert len(chars) == 1 and chars[0].variable == 'e', line


# ---- 官方中文（已译）文件解析：保留官中功能 ----

OFFICIAL_CONTENT = '''\
# game/script.rpy:39
translate chinese start_8cba0c84:

    # "Hello world."
    "你好世界"

# game/script.rpy:40
translate chinese start_9dcaab85:

    # "Second line."
    "第二行。"

# game/script.rpy:41
translate chinese start_aaed3db5:

    # mc.name "Third line."
    mc.name "第三行。"

# game/script.rpy:42
translate chinese start_0c268043:

    # "Untranslated line."
    "Untranslated line."

translate chinese strings:

    # game/options.rpy:20
    old "Menu Choice"
    new "菜单选择"

    # game/options.rpy:21
    old "Empty New"
    new ""
'''


def _parse_official(tmp_path):
    tl = tmp_path / 'game' / 'tl' / 'chinese'
    tl.mkdir(parents=True)
    (tl / 'script.rpy').write_text(OFFICIAL_CONTENT, encoding='utf-8')
    from tl_parser import parse_translation_files
    return parse_translation_files(tmp_path / 'game' / 'tl' / 'chinese', tmp_path)


def test_official_dialogue_translation_extracted(tmp_path):
    """官中文件代码行是译文：必须提取为已译（此前解析丢弃译文、全部入库为待译）"""
    result = _parse_official(tmp_path)
    by_text = {d['original_text']: d for d in result['dialogues']}
    assert by_text['Hello world.']['translated_text'] == '你好世界'
    assert by_text['Hello world.']['is_translated'] is True


def test_official_dotted_speaker_translation(tmp_path):
    """带点说话人的官中代码行同样提取译文"""
    result = _parse_official(tmp_path)
    d = next(d for d in result['dialogues'] if d['original_text'] == 'Third line.')
    assert d['translated_text'] == '第三行。'
    assert d['is_translated'] is True
    assert d['character'] == 'mc'


def test_untranslated_stays_untranslated(tmp_path):
    """代码行与注释原文一致 = 未翻译（SDK 模板回归）"""
    result = _parse_official(tmp_path)
    d = next(d for d in result['dialogues'] if d['original_text'] == 'Untranslated line.')
    assert d['is_translated'] is False
    assert d['translated_text'] == ''


def test_official_strings_new_extracted(tmp_path):
    """strings 块 new 行是译文：必须提取（此前一律空串）"""
    result = _parse_official(tmp_path)
    u = next(u for u in result['ui_texts'] if u['original_text'] == 'Menu Choice')
    assert u['translated_text'] == '菜单选择'
    assert u['is_translated'] is True


def test_strings_empty_new_untranslated(tmp_path):
    """new 为空 = 未翻译"""
    result = _parse_official(tmp_path)
    u = next(u for u in result['ui_texts'] if u['original_text'] == 'Empty New')
    assert u['is_translated'] is False
    assert u['translated_text'] == ''
