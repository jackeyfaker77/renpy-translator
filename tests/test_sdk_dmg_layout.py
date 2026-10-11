"""macOS SDK dmg 布局与 .macho 还原测试

两个官方 dmg（7.4.11 / 8.5.3）卷根都是同名**文件夹**
（renpy-X.Y.Z-sdk/，.app 在其内一层），旧 _extract_dmg 只认卷根
*.app → 自动下载必失败（dmg 中未找到 .app）。

且 dmg 里 `lib/**` 的平台二进制被改名为 *.macho 并加 5 字节
"RENPY" 头（distribute.rpy 的 notarization 规避），官方靠 renpy.app
启动时 launcher（mac.rpy）还原。我们 headless 直接跑 renpy.sh，
不还原则 `lib/<平台>/renpy` 缺失 → exec ENOENT（用户实际报错：
生成失败 返回码 126）。

契约：
- find_dmg_sdk_root：卷根即 SDK / 卷根唯一子文件夹内含 SDK / 找不到
- unmangle_mac_binaries：剥 RENPY 头、还原文件名、加执行位、删 .macho；
  幂等；无前缀的 .macho 仅改名不剥数据
"""
from pathlib import Path

from sdk_manager import unmangle_mac_binaries
from server.sdk_download import find_dmg_sdk_root


# ---- find_dmg_sdk_root ----

def test_dmg_root_is_sdk_folder(tmp_path):
    """官方布局：卷根唯一文件夹内含 renpy.sh"""
    inner = tmp_path / 'renpy-7.4.11-sdk'
    inner.mkdir()
    (inner / 'renpy.sh').write_text('#!/bin/sh', encoding='utf-8')
    (inner / 'renpy.app').mkdir()
    # 卷级隐藏项不参与匹配
    (tmp_path / '.HFS+ Private Directory Data_').mkdir()
    (tmp_path / '[HFS+ Private Data]').mkdir()

    assert find_dmg_sdk_root(tmp_path) == inner


def test_dmg_root_is_sdk_itself(tmp_path):
    """卷根直接是 SDK（含 renpy.sh）"""
    (tmp_path / 'renpy.sh').write_text('#!/bin/sh', encoding='utf-8')

    assert find_dmg_sdk_root(tmp_path) == tmp_path


def test_dmg_root_is_bare_app(tmp_path):
    """卷根直接是 .app（旧假设布局，仍兼容）"""
    (tmp_path / 'renpy.app').mkdir()

    assert find_dmg_sdk_root(tmp_path) == tmp_path


def test_dmg_not_sdk_returns_none(tmp_path):
    """找不到 SDK → None（调用方给出明确报错）"""
    (tmp_path / 'random').mkdir()

    assert find_dmg_sdk_root(tmp_path) is None


# ---- unmangle_mac_binaries ----

def _write_macho(path: Path, payload: bytes = b'\xcf\xfa\xed\xfeFAKE'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'RENPY' + payload)
    return path


def test_unmangle_strips_prefix_and_renames(tmp_path):
    macho = _write_macho(tmp_path / 'lib' / 'mac-x86_64' / 'renpy.macho')

    count = unmangle_mac_binaries(tmp_path)

    assert count == 1
    target = tmp_path / 'lib' / 'mac-x86_64' / 'renpy'
    assert target.read_bytes() == b'\xcf\xfa\xed\xfeFAKE'
    assert not macho.exists()
    if __import__('os').name != 'nt':
        assert target.stat().st_mode & 0o111, '还原后必须可执行'


def test_unmangle_handles_dylib_double_suffix(tmp_path):
    """librenpython.dylib.macho → librenpython.dylib（只剥最后一层后缀）"""
    _write_macho(tmp_path / 'lib' / 'mac-x86_64' / 'librenpython.dylib.macho')

    unmangle_mac_binaries(tmp_path)

    assert (tmp_path / 'lib' / 'mac-x86_64'
            / 'librenpython.dylib').exists()


def test_unmangle_idempotent(tmp_path):
    _write_macho(tmp_path / 'lib' / 'py3-mac-universal' / 'renpy.macho')

    first = unmangle_mac_binaries(tmp_path)
    second = unmangle_mac_binaries(tmp_path)

    assert (first, second) == (1, 0)


def test_unmangle_without_prefix_keeps_data(tmp_path):
    """无 RENPY 头的 .macho：只改名，不剥数据（避免误伤）"""
    raw = b'\xcf\xfa\xed\xfeNOSTRIP'
    macho = tmp_path / 'lib' / 'mac-x86_64' / 'python.macho'
    macho.parent.mkdir(parents=True)
    macho.write_bytes(raw)

    unmangle_mac_binaries(tmp_path)

    assert (macho.parent / 'python').read_bytes() == raw
    assert not macho.exists()


def test_unmangle_no_macho_is_noop(tmp_path):
    """普通（zip/Windows）SDK 无 .macho → 零副作用"""
    plain = tmp_path / 'lib' / 'linux-x86_64' / 'renpy'
    plain.parent.mkdir(parents=True)
    plain.write_bytes(b'\xcf\xfa\xed\xfePLAIN')

    assert unmangle_mac_binaries(tmp_path) == 0
    assert plain.read_bytes() == b'\xcf\xfa\xed\xfePLAIN'
