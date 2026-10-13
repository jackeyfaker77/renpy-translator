"""引擎版本探测测试（防 8.x 游戏误判成 7.x 拿错 SDK）

背景（bug）：Motherless-0.26.1.1 导入报「SDK 生成翻译文件失败: 生成失败
(返回码: 1)」，SDK 输出 f-string invalid syntax 与 nearrect is not a
keyword——游戏是 Ren'Py 8.x 构建（script_version.txt = (8, 0, 1)），
但其自带 renpy/__init__.py 是 7.5/8.0 共用源码：if PY2: (7, 5, 3) /
else: (8, 0, 3) 两支字面量。旧探测取第一支命中 PY2 支，把 8.x 游戏
判成 7.x，resolve_sdk_path 选中 7.4.11 SDK（Python 2.7）去 parse
f-string/nearrect 等 8.x 语法，直接解析失败。

契约（判定顺序）：
1. SDK 目录名 renpy-X.Y.Z-sdk——安装器命名契约（fullmatch），
   8.x 新版 __init__.py 版本动态计算、双分支字面量对 SDK 都不可靠
2. game/script_version.txt（构建时 distribute.rpy 写入的版本元组）
3. renpy/__init__.py 字面量——双分支时按发行包自带运行时取支：
   lib/py3-*/python3.x/pythonlib2.x 或 .sh 的 PYTHON="py3"
   → else（8.x）支；lib/py2-* → PY2（7.x）支；认不出取 PY2 支
4. 仅 lib/ 运行时可判（源码与版本元组被剥离）→ 按大版本合成
   (7, 0, 0)/(8, 0, 0)，minor/patch 无意义、仅供 SDK 大版本路由
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from sdk_manager import detect_engine_version  # noqa: E402

# 7.5/8.0 共用源码形态：两支 version_tuple 字面量
_DUAL_INIT = (
    'from renpy.compat import PY2\n'
    '\n'
    'if PY2:\n'
    '\n'
    '    # The tuple giving the version number.\n'
    '    version_tuple = (7, 5, 3, vc_version)\n'
    '\n'
    '    # The name of this version.\n'
    '    version_name = "Heck\'s Getting Frosty"\n'
    '\n'
    'else:\n'
    '\n'
    '    # The tuple giving the version number.\n'
    '    version_tuple = (8, 0, 3, vc_version)\n'
    '\n'
    '    # The name of this version.\n'
    '    version_name = "Heck Freezes Over"\n'
)

_SINGLE_INIT = (
    '# The tuple giving the version number.\n'
    'version_tuple = (7, 4, 9, vc_version)\n'
)

_SH_PY3 = (
    '#!/bin/sh\n'
    'PYTHON="py3"\n'
)


def _make(tmp_path, init_text=None, script_version=None, lib_dirs=(),
          sh_text=None):
    """构造最小游戏/SDK 目录布局"""
    if init_text is not None:
        (tmp_path / 'renpy').mkdir(exist_ok=True)
        (tmp_path / 'renpy' / '__init__.py').write_text(
            init_text, encoding='utf-8')
    if script_version is not None:
        (tmp_path / 'game').mkdir(exist_ok=True)
        (tmp_path / 'game' / 'script_version.txt').write_text(
            script_version, encoding='utf-8')
    if lib_dirs:
        (tmp_path / 'lib').mkdir(exist_ok=True)
        for d in lib_dirs:
            (tmp_path / 'lib' / d).mkdir()
    if sh_text is not None:
        (tmp_path / 'launcher.sh').write_text(sh_text, encoding='utf-8')
    return tmp_path


def test_script_version_wins(tmp_path):
    """game/script_version.txt 最可靠：优先于 __init__.py 字面量"""
    root = _make(tmp_path, init_text=_DUAL_INIT,
                 script_version='(8, 0, 1)', lib_dirs=['py3-windows-x86_64'])
    assert detect_engine_version(root) == (8, 0, 1)


def test_dual_branch_py3_runtime_takes_else(tmp_path):
    """双分支 + lib/py3-* 运行时 → else（8.x）支，不再误判 7.x"""
    root = _make(tmp_path, init_text=_DUAL_INIT,
                 lib_dirs=['py3-windows-x86_64', 'python3.9'])
    assert detect_engine_version(root) == (8, 0, 3)


def test_dual_branch_py2_runtime_takes_py2(tmp_path):
    """双分支 + python2.7 运行时 → PY2（7.x）支（7.x 游戏/SDK 布局）"""
    root = _make(tmp_path, init_text=_DUAL_INIT, lib_dirs=['python2.7'])
    assert detect_engine_version(root) == (7, 5, 3)


def test_dual_branch_sh_launcher_marker(tmp_path):
    """无 lib/ 时认 .sh 启动器的 PYTHON="py3" 标记"""
    root = _make(tmp_path, init_text=_DUAL_INIT, sh_text=_SH_PY3)
    assert detect_engine_version(root) == (8, 0, 3)


def test_dual_branch_unknown_runtime_keeps_py2_branch(tmp_path):
    """运行时认不出：维持旧行为取 PY2 支（不静默改判）"""
    root = _make(tmp_path, init_text=_DUAL_INIT)
    assert detect_engine_version(root) == (7, 5, 3)


def test_single_literal_unchanged(tmp_path):
    """单支字面量直接取（7.x 单版本源码）"""
    root = _make(tmp_path, init_text=_SINGLE_INIT)
    assert detect_engine_version(root) == (7, 4, 9)


def test_sdk_dir_name_fallback(tmp_path):
    """8.x 新版 __init__.py 版本动态计算无字面量 → SDK 目录名解析"""
    sdk = tmp_path / 'renpy-8.5.3-sdk'
    sdk.mkdir()
    assert detect_engine_version(sdk) == (8, 5, 3)


def test_nothing_detectable(tmp_path):
    """什么都认不出返回 None（调用方按无版本处理）"""
    assert detect_engine_version(tmp_path) is None


def test_pythonlib_runtime_recognized(tmp_path):
    """7.3.x 发行包的 pythonlib2.7 运行时目录也算 PY2 证据（BH-AM 实测布局）"""
    root = _make(tmp_path, init_text=_DUAL_INIT,
                 lib_dirs=['pythonlib2.7', 'windows-i686'])
    assert detect_engine_version(root) == (7, 5, 3)


def test_sdk_dir_name_beats_init_literals(tmp_path):
    """SDK 目录名是安装契约，优先于 __init__.py 双分支字面量——
    renpy-8.0.3-sdk 的共用源码字面量首支是 (7,5,3)，不得据此拿 7.x SDK"""
    sdk = tmp_path / 'renpy-8.0.3-sdk'
    sdk.mkdir()
    (sdk / 'renpy').mkdir()
    (sdk / 'renpy' / '__init__.py').write_text(_DUAL_INIT, encoding='utf-8')
    assert detect_engine_version(sdk) == (8, 0, 3)


def test_stripped_game_synthesizes_major_from_runtime(tmp_path):
    """源码与版本元组都被剥离：按 lib/ 运行时合成大版本，
    防止 7.x 剥壳快照被调用方默认偏到 8.x SDK"""
    root7 = tmp_path / 'g7'
    root7.mkdir()
    _make(root7, lib_dirs=['py2-windows-x86_64'])
    assert detect_engine_version(root7) == (7, 0, 0)
    root8 = tmp_path / 'g8'
    root8.mkdir()
    _make(root8, lib_dirs=['py3-linux-x86_64'])
    assert detect_engine_version(root8) == (8, 0, 0)


def test_commented_version_tuple_not_matched(tmp_path):
    """注释里的 version_tuple 示例不算字面量（行首锚定），
    否则双分支选支会因多出一支而错位"""
    init = (
        '# version_tuple = (6, 99, 12, 4, vc_version)\n'
        '# 示例：version_tuple = (9, 9, 9, vc_version)\n'
        'if PY2:\n'
        '    version_tuple = (7, 5, 3, vc_version)\n'
        'else:\n'
        '    version_tuple = (8, 0, 3, vc_version)\n'
    )
    root = _make(tmp_path, init_text=init,
                 lib_dirs=['py3-windows-x86_64'])
    assert detect_engine_version(root) == (8, 0, 3)


def test_malformed_script_version_falls_through(tmp_path):
    """script_version.txt 损坏 → 回退 __init__.py 解析"""
    root = _make(tmp_path, init_text=_DUAL_INIT,
                 script_version='not-a-tuple',
                 lib_dirs=['py3-windows-x86_64'])
    assert detect_engine_version(root) == (8, 0, 3)
