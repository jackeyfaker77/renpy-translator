"""Ren'Py 工程根定位测试（macOS .app 发行包布局）

背景：Mac 版游戏 zip 顶层是 `Xxx.app`，真正的工程根在
`.app/Contents/Resources/autorun`（game/ 与 renpy/ 同级）。管线此前
把 .app 根当工程根：找不到 game/ → 不解包 rpa、不反编译、官方中文
检测漏掉，且 SDK translate 扫到引擎自带 common 产生海量重复 label
输出，配合 _run_once 的先等后读管道死锁 → 导入卡死超时。

契约：
- 顶层已有 game/（PC 布局 / autorun 内层）→ 原样返回
- .app 或其父目录 → 定位到 Contents/Resources/autorun
- 未知布局 → 原样返回（后续步骤按原逻辑报错，不静默改道）
"""
from services.game_pipeline import resolve_game_root


def _make_project(root):
    """构造 <root>/game 与 <root>/renpy 的标准工程布局"""
    (root / 'game').mkdir(parents=True)
    (root / 'renpy').mkdir()
    return root


def test_plain_layout_unchanged(tmp_path):
    """顶层直接有 game/（PC 解压布局）→ 原样返回"""
    project = _make_project(tmp_path)
    assert resolve_game_root(tmp_path) == project


def test_macos_app_bundle(tmp_path):
    """直接选中 .app → 定位到 Contents/Resources/autorun"""
    autorun = (tmp_path / 'KingdomOfMarionettes.app'
               / 'Contents' / 'Resources' / 'autorun')
    _make_project(autorun)
    (autorun / 'KingdomOfMarionettes.py').write_text('# launcher',
                                                      encoding='utf-8')

    assert resolve_game_root(tmp_path / 'KingdomOfMarionettes.app') == autorun


def test_parent_of_single_app(tmp_path):
    """选中只含一个 .app 的父目录 → 下探一层后定位"""
    autorun = (tmp_path / 'KingdomOfMarionettes.app'
               / 'Contents' / 'Resources' / 'autorun')
    _make_project(autorun)
    (tmp_path / '说明.txt').write_text('hi', encoding='utf-8')  # 非目录不参与计数

    assert resolve_game_root(tmp_path) == autorun


def test_app_without_game_falls_back_to_origin(tmp_path):
    """未知布局（无 game/）→ 原样返回，不猜路径"""
    (tmp_path / 'random').mkdir()

    assert resolve_game_root(tmp_path) == tmp_path


def test_nested_app_under_single_wrapper(tmp_path):
    """多层包装：wrapper/Xxx.app/Contents/Resources/autorun → 逐层下探"""
    wrapper = tmp_path / 'extracted'
    wrapper.mkdir()
    autorun = (wrapper / 'Game.app'
               / 'Contents' / 'Resources' / 'autorun')
    _make_project(autorun)

    assert resolve_game_root(wrapper) == autorun
