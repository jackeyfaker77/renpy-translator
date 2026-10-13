"""macOS .app 壳：定位、快照、导出套壳、打包执行位

- find_app_root：resolve_game_root 的产出是 .app 内
  Contents/Resources/autorun 时，返回该 .app 根；其余返回 None
- sync_app_shell：把 .app 除工程根外的骨架（Info.plist/MacOS 启动器/
  Resources/lib 运行时）快照进 projects/<名>/app-shell/；无壳来源清除旧壳
- assemble_app_shell：导出收尾把工程落进壳的 Contents/Resources/autorun，
  zip 根变为 Xxx.app（解压可双击）
- zip_directory：Windows 磁盘无执行位，打包时对 .sh/.py 与
  Contents/MacOS/ 强制 0755，否则解压后 .app「无法打开」
"""
import zipfile
from pathlib import Path

from services.game_export import assemble_app_shell, zip_directory
from services.game_pipeline import find_app_root, sync_app_shell


def _make_app(tmp_path, name='KingdomOfMarionettes.app'):
    """构造 .app：Contents/{Info.plist,MacOS/启动器,Resources/{autorun,lib,图标}}"""
    app = tmp_path / name
    autorun = app / 'Contents' / 'Resources' / 'autorun'
    (autorun / 'game' / 'scripts').mkdir(parents=True)
    (autorun / 'game' / 'scripts' / 'script.rpy').write_text(
        'label start:\n    "hi"\n', encoding='utf-8')
    (autorun / 'renpy').mkdir()
    (app / 'Contents' / 'MacOS').mkdir(parents=True)
    (app / 'Contents' / 'MacOS' / 'KingdomOfMarionettes').write_bytes(b'\x00')
    (app / 'Contents' / 'Info.plist').write_text(
        '<plist/>', encoding='utf-8')
    (app / 'Contents' / 'Resources' / 'lib' / 'python3.11').mkdir(
        parents=True)
    (app / 'Contents' / 'Resources' / 'lib' / 'python3.11' / 'os.py').write_text(
        '', encoding='utf-8')
    (app / 'Contents' / 'Resources' / 'icon.icns').write_bytes(b'\x00')
    return app


# ---- find_app_root ----

def test_find_app_root_from_autorun(tmp_path):
    app = _make_app(tmp_path)
    autorun = app / 'Contents' / 'Resources' / 'autorun'

    assert find_app_root(autorun) == app


def test_find_app_root_plain_project_none(tmp_path):
    """PC 工程根（名字不是 .app 下的 autorun）→ None"""
    proj = tmp_path / 'Game-2.0-pc'
    (proj / 'game').mkdir(parents=True)

    assert find_app_root(proj) is None


def test_find_app_root_requires_app_suffix(tmp_path):
    """同为 autorun 形状但父目录不叫 .app → None（不误套壳）"""
    other = tmp_path / 'extracted' / 'Contents' / 'Resources' / 'autorun'
    (other / 'game').mkdir(parents=True)

    assert find_app_root(other) is None


# ---- sync_app_shell ----

def test_sync_snapshots_shell_without_autorun(tmp_path):
    app = _make_app(tmp_path)
    project = tmp_path / 'proj'
    project.mkdir()

    assert sync_app_shell(project, app) is True

    shell = project / 'app-shell'
    assert (shell / 'Contents' / 'Info.plist').exists()
    assert (shell / 'Contents' / 'MacOS' / 'KingdomOfMarionettes').exists()
    assert (shell / 'Contents' / 'Resources' / 'lib' / 'python3.11'
            / 'os.py').exists()
    # 工程根随 game/ 走，壳里不得带（否则导出套壳后 autorun 重复）
    assert not (shell / 'Contents' / 'Resources' / 'autorun').exists()


def test_sync_replaces_stale_shell(tmp_path):
    """旧壳（含过时文件）被新快照整体替换"""
    app = _make_app(tmp_path)
    project = tmp_path / 'proj'
    project.mkdir()
    stale = project / 'app-shell' / 'Contents' / 'stale.txt'
    stale.parent.mkdir(parents=True)
    stale.write_text('old', encoding='utf-8')

    sync_app_shell(project, app)

    assert not stale.exists()
    assert (project / 'app-shell' / 'Contents' / 'Info.plist').exists()


def test_sync_none_clears_shell(tmp_path):
    """非 mac 来源（app_root=None）：清除旧壳，不留过时快照"""
    project = tmp_path / 'proj'
    old = project / 'app-shell' / 'Contents' / 'x'
    old.parent.mkdir(parents=True)
    old.write_text('x', encoding='utf-8')

    assert sync_app_shell(project, None) is False
    assert not (project / 'app-shell').exists()


# ---- assemble_app_shell ----

def test_assemble_moves_work_into_autorun(tmp_path):
    shell_src = tmp_path / 'shell-src'
    (shell_src / 'Contents' / 'MacOS').mkdir(parents=True)
    (shell_src / 'Contents' / 'MacOS' / 'launcher').write_bytes(b'\x00')
    (shell_src / 'Contents' / 'Info.plist').write_text('<plist/>',
                                                       encoding='utf-8')
    work = tmp_path / 'out'
    (work / 'game' / 'tl' / 'chinese').mkdir(parents=True)
    (work / 'game' / 'script.rpy').write_text('label start: pass',
                                               encoding='utf-8')

    app_out = assemble_app_shell(work, shell_src, 'MyGame.app')

    assert app_out == tmp_path / 'MyGame.app'
    assert (app_out / 'Contents' / 'Info.plist').exists()
    assert (app_out / 'Contents' / 'MacOS' / 'launcher').exists()
    assert (app_out / 'Contents' / 'Resources' / 'autorun' / 'game'
            / 'script.rpy').exists()
    assert (app_out / 'Contents' / 'Resources' / 'autorun' / 'game'
            / 'tl' / 'chinese').exists()
    assert not work.exists()  # 工作目录整体移入壳


# ---- zip_directory 执行位 ----

def _zip_names_exec(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return {i.filename: (i.external_attr >> 16) & 0o111
                for i in zf.infolist()}


def test_zip_marks_macos_and_scripts_executable(tmp_path):
    """Contents/MacOS 启动器与 .sh/.py 带执行位；数据文件不带"""
    src = tmp_path / 'MyGame.app'
    (src / 'Contents' / 'MacOS').mkdir(parents=True)
    (src / 'Contents' / 'MacOS' / 'KingdomOfMarionettes').write_bytes(b'\x00')
    autorun = src / 'Contents' / 'Resources' / 'autorun'
    (autorun).mkdir(parents=True)
    (autorun / 'KingdomOfMarionettes.sh').write_text('#!/bin/sh', encoding='utf-8')
    (autorun / 'KingdomOfMarionettes.py').write_text('x=1', encoding='utf-8')
    (src / 'Contents' / 'Resources' / 'autorun' / 'game').mkdir()
    game_rpy = (src / 'Contents' / 'Resources' / 'autorun' / 'game'
                / 'script.rpy')
    game_rpy.write_text('label start: pass', encoding='utf-8')

    zip_path = tmp_path / 'out.zip'
    zip_directory(src, zip_path)

    modes = _zip_names_exec(zip_path)
    def mode_of(suffix):
        hits = [m for n, m in modes.items() if n.endswith(suffix)]
        assert len(hits) == 1, (suffix, modes)
        return hits[0]

    assert mode_of('MacOS/KingdomOfMarionettes') & 0o111, '启动器必须可执行'
    assert mode_of('KingdomOfMarionettes.sh') & 0o111, '.sh 必须可执行'
    assert mode_of('KingdomOfMarionettes.py') & 0o111, '.py 必须可执行'
    assert mode_of('game/script.rpy') == 0, '数据/脚本源文件不加执行位'
