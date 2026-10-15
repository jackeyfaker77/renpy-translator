"""pick_sdk 三级匹配：精确 x.y.z → 同大版本最新 → ''。"""
from sdk_manager import pick_sdk

INSTALLED = [
    ((8, 3, 2), '/sdks/renpy-8.3.2-sdk'),
    ((8, 5, 3), '/sdks/renpy-8.5.3-sdk'),
    ((7, 4, 11), '/sdks/renpy-7.4.11-sdk'),
]


def test_exact_version_wins_over_newer_same_major():
    assert pick_sdk(INSTALLED, (8, 3, 2)) == '/sdks/renpy-8.3.2-sdk'
    assert pick_sdk(INSTALLED, (8, 5, 3)) == '/sdks/renpy-8.5.3-sdk'


def test_same_major_falls_back_to_newest():
    assert pick_sdk(INSTALLED, (8, 1, 0)) == '/sdks/renpy-8.5.3-sdk'
    assert pick_sdk(INSTALLED, (7, 5, 0)) == '/sdks/renpy-7.4.11-sdk'


def test_missing_major_returns_empty():
    assert pick_sdk(INSTALLED, (6, 99, 9)) == ''
    assert pick_sdk([], (8, 3, 2)) == ''


def test_no_game_version_prefers_eight_then_any():
    assert pick_sdk(INSTALLED, None) == '/sdks/renpy-8.5.3-sdk'
    seven_only = [INSTALLED[2]]
    assert pick_sdk(seven_only, None) == '/sdks/renpy-7.4.11-sdk'
    assert pick_sdk([], None) == ''
