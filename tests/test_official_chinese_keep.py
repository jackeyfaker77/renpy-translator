"""保留官中链路测试：语言目录归一、重复块去重、建项三动作、更新继承

背景：检测到官中时此前只有「删除/取消」两选项。新增「保留官中」——
在官方译文基础上继续翻译：
- normalize_official_chinese：非 chinese 的中文目录（schinese/zh…）
  改名为 chinese（SDK 只生成 chinese、导出与运行时也只认 chinese）
- dedupe_translations：Ren'Py 同源重生成时 identifier 失配会把已译块
  追加一条未译副本——同 (file, 原文) 存在已译行时丢弃未译行
- 建项 confirm 三动作 delete/keep/cancel，keep 不删官方文件、
  官方译文经解析直接入库为已译
- merge_translations：旧条目未译而新解析带官中译文时继承官中
"""
import asyncio
from pathlib import Path

import pytest

from database import ProjectDatabase
from logger import TranslationLogger
from project_manager import ProjectManager
from services.project_creation import (
    ProjectCreator, dedupe_translations, detect_official_chinese,
    normalize_official_chinese, parse_tl_dir,
)


# ---- normalize_official_chinese ----

def _make_tl(tmp_path, *lang_dirs, with_chinese=False):
    tl_root = tmp_path / 'game' / 'tl'
    tl_root.mkdir(parents=True)
    if with_chinese:
        (tl_root / 'chinese').mkdir()
        (tl_root / 'chinese' / 'a.rpy').write_text('x', encoding='utf-8')
    for name in lang_dirs:
        d = tl_root / name
        d.mkdir()
        (d / 'a.rpy').write_text('x', encoding='utf-8')
    return tmp_path


def test_normalize_renames_schinese(tmp_path):
    """官方在 schinese/：改名为 chinese，SDK 追加与运行时才能读到"""
    work = _make_tl(tmp_path, 'schinese')

    assert normalize_official_chinese(work) == 'schinese'
    assert (work / 'game' / 'tl' / 'chinese' / 'a.rpy').exists()
    assert not (work / 'game' / 'tl' / 'schinese').exists()


def test_normalize_chinese_noop(tmp_path):
    """官方本就在 chinese/：无需改名"""
    work = _make_tl(tmp_path, with_chinese=True)

    assert normalize_official_chinese(work) is None
    assert (work / 'game' / 'tl' / 'chinese').exists()


def test_normalize_conflict_keeps_both(tmp_path):
    """chinese 与其他中文目录并存：不动任何目录（避免覆盖），返回 None"""
    work = _make_tl(tmp_path, 'zh', with_chinese=True)

    assert normalize_official_chinese(work) is None
    assert (work / 'game' / 'tl' / 'chinese' / 'a.rpy').exists()
    assert (work / 'game' / 'tl' / 'zh' / 'a.rpy').exists()


def test_normalize_ignores_none_lang(tmp_path):
    """tl/None（base 字符串）不是官中，不碰"""
    work = _make_tl(tmp_path, 'None')

    assert normalize_official_chinese(work) is None
    assert (work / 'game' / 'tl' / 'None').exists()


def test_normalize_no_tl_dir(tmp_path):
    (tmp_path / 'game').mkdir()
    assert normalize_official_chinese(tmp_path) is None


def test_simplified_chinese_imports_by_id_without_renaming(tmp_path):
    """保留游戏原生语言名，给 SDK 的 chinese 模板填入对应官中译文。"""
    tl = tmp_path / 'game' / 'tl'
    primary = tl / 'chinese'
    official = tl / 'SimplifiedChinese'
    primary.mkdir(parents=True)
    official.mkdir()
    (primary / 'story.rpy').write_text(
        '# game/story.rpy:1\n'
        'translate chinese start_12345678:\n\n'
        '    # nar "Hello [name]."\n'
        '    nar "Hello [name]."\n'
        '# game/story.rpy:2\n'
        'translate chinese start_87654321:\n\n'
        '    # nar "New line."\n'
        '    nar "New line."\n'
        'translate chinese strings:\n'
        '    # game/story.rpy:3\n'
        '    old "Start"\n'
        '    new ""\n', encoding='utf-8')
    (official / 'story.rpy').write_text(
        '# game/story.rpy:1\n'
        'translate SimplifiedChinese start_12345678:\n\n'
        '    # nar "Hello [name]."\n'
        '    nar "你好，[name]。"\n'
        '# game/story.rpy:2\n'
        'translate SimplifiedChinese changed_87654321:\n\n'
        '    # nar "New line."\n'
        '    nar "旧版本译文。"\n'
        'translate SimplifiedChinese strings:\n'
        '    # game/story.rpy:3\n'
        '    old "Start"\n'
        '    new "开始"\n', encoding='utf-8')

    assert detect_official_chinese(tmp_path) == 2
    assert normalize_official_chinese(tmp_path) is None
    assert official.exists()
    result = parse_tl_dir(primary, tmp_path, None)
    by_original = {r['original_text']: r for r in result['dialogues']}
    assert by_original['Hello [name].']['translated_text'] == '你好，[name]。'
    assert by_original['Hello [name].']['is_translated'] is True
    assert by_original['New line.']['is_translated'] is False
    ui = {r['original_text']: r for r in result['ui_texts']}
    assert ui['Start']['translated_text'] == '开始'


# ---- dedupe_translations ----

def _row(file, line, original, translated=''):
    return {'file_path': file, 'line_number': line, 'original_text': original,
            'translated_text': translated, 'is_translated': bool(translated)}


def test_dedupe_drops_empty_twin_when_translated():
    """已译行存在时丢弃同 (file,原文) 的未译副本（renpy 重生成失配追加）"""
    result = dedupe_translations({'dialogues': [
        _row('game/script.rpy', 39, 'Hello.', '你好。'),
        _row('game/script.rpy', 39, 'Hello.', ''),
    ], 'ui_texts': []})

    rows = result['dialogues']
    assert len(rows) == 1
    assert rows[0]['translated_text'] == '你好。'


def test_dedupe_keeps_all_empty_twins():
    """都是未译（原文重复的两处真实台词）：都保留"""
    result = dedupe_translations({'dialogues': [
        _row('game/script.rpy', 10, 'OK.'),
        _row('game/script.rpy', 99, 'OK.'),
    ], 'ui_texts': []})

    assert len(result['dialogues']) == 2


def test_dedupe_keeps_both_translated_twins():
    """两行都已译（原文重复且各有译文）：都保留"""
    result = dedupe_translations({'dialogues': [
        _row('game/a.rpy', 1, 'OK.', '好的。'),
        _row('game/a.rpy', 2, 'OK.', '行。'),
    ], 'ui_texts': []})

    assert len(result['dialogues']) == 2


def test_dedupe_applies_to_ui_texts():
    """strings 同样去重（官方 + 重生成追加的未译副本）"""
    result = dedupe_translations({'dialogues': [], 'ui_texts': [
        _row('game/options.rpy', 20, 'Menu', '菜单'),
        _row('game/options.rpy', 20, 'Menu', ''),
    ]})

    assert len(result['ui_texts']) == 1
    assert result['ui_texts'][0]['translated_text'] == '菜单'


def test_dedupe_different_file_not_merged():
    """不同文件同原文互不影响"""
    result = dedupe_translations({'dialogues': [
        _row('game/a.rpy', 1, 'OK.', '好的。'),
        _row('game/b.rpy', 1, 'OK.', ''),
    ], 'ui_texts': []})

    assert len(result['dialogues']) == 2


# ---- merge_translations：更新时继承新包官中 ----

def test_merge_carries_new_official_when_old_empty():
    """旧条目未译、新解析带官中译文 → 继承官中（不算新增待译）"""
    from services.project_update import merge_translations
    old = [{'id': 1, 'character': '', 'original_text': 'Hello.',
            'translated_text': '', 'is_translated': False}]
    new = [{'file_path': 'game/script.rpy', 'line_number': 1, 'label': '',
            'character': '', 'original_text': 'Hello.',
            'translated_text': '你好。', 'is_translated': True}]

    merged = merge_translations(old, [], {'dialogues': new, 'ui_texts': []})

    row = merged['dialogues'][0]
    assert row['translated_text'] == '你好。'
    assert row['is_translated'] is True
    assert merged['stats']['carried'] == 1
    assert merged['stats']['new'] == 0


def test_merge_old_translation_wins_over_official():
    """旧条目已译：用户译文优先于新包官中"""
    from services.project_update import merge_translations
    old = [{'id': 1, 'character': '', 'original_text': 'Hello.',
            'translated_text': '我的旧译。', 'is_translated': True}]
    new = [{'file_path': 'game/script.rpy', 'line_number': 1, 'label': '',
            'character': '', 'original_text': 'Hello.',
            'translated_text': '官中。', 'is_translated': True}]

    merged = merge_translations(old, [], {'dialogues': new, 'ui_texts': []})

    assert merged['dialogues'][0]['translated_text'] == '我的旧译。'
    assert merged['stats']['carried'] == 1


# ---- 建项三动作集成（无 SDK：get_sdk_path 返回空跳过模板生成） ----

OFFICIAL_SCRIPT = '''\
# game/scripts/script.rpy:1
translate chinese start_8cba0c84:

    # "Hello world."
    "你好世界"
'''


def _make_mac_project(tmp_path):
    """.app 布局建项源：官方中文在 autorun/game/tl/chinese"""
    app = tmp_path / 'KingdomOfMarionettes.app'
    autorun = app / 'Contents' / 'Resources' / 'autorun'
    (autorun / 'game' / 'scripts').mkdir(parents=True)
    (autorun / 'game' / 'scripts' / 'script.rpy').write_text(
        'label start:\n    "Hello world."\n', encoding='utf-8')
    (autorun / 'game' / 'tl' / 'chinese').mkdir(parents=True)
    (autorun / 'game' / 'tl' / 'chinese' / 'script.rpy').write_text(
        OFFICIAL_SCRIPT, encoding='utf-8')
    (autorun / 'renpy').mkdir()
    (app / 'Contents' / 'Info.plist').parent.mkdir(parents=True, exist_ok=True)
    (app / 'Contents' / 'Info.plist').write_text('<plist/>', encoding='utf-8')
    return app


async def _create(app, action, project_name, tmp_path):
    """跑一次 create()，confirm 返回指定动作；返回 (结果, 项目目录, 日志行)"""
    pm = ProjectManager(str(tmp_path / 'projects'))
    logger = TranslationLogger()
    logs = []

    async def confirm(count):
        logs.append(count)
        return action

    creator = ProjectCreator(pm, logger, get_sdk_path=lambda gd=None: '')
    result = await creator.create(
        project_name, str(app),
        progress=lambda p, t: None,
        confirm_official_chinese=confirm)
    return result, pm.project_dir(project_name), logs


def test_create_keep_official_translated(tmp_path):
    """keep：官方文件保留 + 官方译文入库为已译 + .app 壳已快照"""
    app = _make_mac_project(tmp_path)

    result, project_dir, prompts = asyncio.run(
        _create(app, 'keep', 'keep-game', tmp_path))

    assert result['success'], result
    assert prompts == [1], '应触发一次官中确认'
    # create 只动项目副本，源包只读；keep 下项目副本保留官方中文
    # （game_work_dir=project_dir/game 下镜像 autorun 的 game/ 结构）
    official = (project_dir / 'game' / 'game' / 'tl' / 'chinese' / 'script.rpy')
    assert official.exists(), 'keep 不得删除官方中文'
    # 壳快照：骨架在、工程根不在
    shell = project_dir / 'app-shell'
    assert (shell / 'Contents' / 'Info.plist').exists()
    assert not (shell / 'Contents' / 'Resources' / 'autorun').exists()
    # 官方译文直接入库为已译
    from database import ProjectDatabase
    db = ProjectDatabase(str(project_dir / 'project.db'))
    counts = db.get_dialogue_count()
    db.close()
    assert counts['translated'] == 1, counts


def test_create_delete_removes_official(tmp_path):
    """delete：行为与旧版一致——删官中、重新生成（无 SDK 则空库）"""
    app = _make_mac_project(tmp_path)

    result, project_dir, prompts = asyncio.run(
        _create(app, 'delete', 'delete-game', tmp_path))

    assert result['success'], result
    assert prompts == [1]
    assert not (project_dir / 'game' / 'game' / 'tl' / 'chinese').exists(), \
        'delete 应移除项目副本的官方中文目录'
    db = ProjectDatabase(str(project_dir / 'project.db'))
    counts = db.get_dialogue_count()
    db.close()
    assert counts['total'] == 0, counts


def test_create_cancel_rolls_back(tmp_path):
    """cancel：项目整体回滚删除（含已快照的壳）"""
    app = _make_mac_project(tmp_path)
    pm = ProjectManager(str(tmp_path / 'projects'))

    async def confirm(count):
        return 'cancel'

    creator = ProjectCreator(pm, TranslationLogger(),
                             get_sdk_path=lambda gd=None: '')
    result = asyncio.run(creator.create(
        'cancel-game', str(app), progress=lambda p, t: None,
        confirm_official_chinese=confirm))

    assert result == {'cancelled': True}
    assert not pm.project_dir('cancel-game').exists()
