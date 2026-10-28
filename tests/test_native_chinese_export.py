"""导出时补全游戏实际使用的原生中文语言目录。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from services.game_export import GameExporter


def test_native_chinese_fills_blanks_and_preserves_official_text(tmp_path):
    tl_root = tmp_path / 'game' / 'tl'
    native = tl_root / 'SimplifiedChinese'
    native.mkdir(parents=True)
    source = native / 'story.rpy'
    source.write_text(
        'translate SimplifiedChinese a_12345678:\n\n'
        '    # e "Untranslated [name]."\n'
        '    e "Untranslated [name]."\n'
        'translate SimplifiedChinese b_12345678:\n\n'
        '    # e "Already translated."\n'
        '    e "官方已有译文。"\n'
        'translate SimplifiedChinese strings:\n'
        '    old "Start"\n'
        '    new ""\n'
        '    old "Quit"\n'
        '    new "官方退出"\n', encoding='utf-8')

    exporter = object.__new__(GameExporter)
    exporter._blocked = []
    exporter._cancel_event = None
    exporter.db = type('StubDB', (), {'get_characters': lambda self: []})()
    logs = []
    exporter._fill_native_chinese(tl_root, {
        'Untranslated [name].': '未翻译的 [name]。',
        'Already translated.': '工具中的另一译文。',
        'Start': '开始',
        'Quit': '工具中的退出',
    }, logs.append)

    output = source.read_text(encoding='utf-8')
    assert 'e "未翻译的 [name]。"' in output
    assert 'e "官方已有译文。"' in output
    assert '工具中的另一译文' not in output
    assert 'new "开始"' in output
    assert 'new "官方退出"' in output
    assert '工具中的退出' not in output
    assert logs == ['tl/SimplifiedChinese 补全：对话 1 条，字符串 1 条']
