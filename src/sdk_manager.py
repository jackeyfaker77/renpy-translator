"""Ren'Py SDK 管理器 - 负责调用 Ren'Py SDK 功能"""

import os
import re
import sys
import subprocess
import tempfile
import time
from pathlib import Path

from proc_registry import track, untrack

# 7.x 及更早：__init__.py 里字面量 version_tuple = (7, 4, 9, vc_version)
_VER_TUPLE_RE = re.compile(r'version_tuple\s*=\s*\((\d+),\s*(\d+),\s*(\d+)')
# 8.x 的 __init__.py 版本是动态计算的，改从 SDK 目录名解析
_DIR_NAME_RE = re.compile(r'renpy-(\d+)\.(\d+)\.(\d+)-sdk')


def detect_engine_version(base) -> tuple | None:
    """探测 Ren'Py 引擎版本 (major, minor, patch)。

    base: 游戏目录（读取其内置 renpy/__init__.py；7.x 可直接解析，
    8.x 游戏读不到则返回 None）或 SDK 目录（从目录名解析）。
    """
    base = Path(base)
    text = ''
    try:
        text = (base / 'renpy' / '__init__.py').read_text(
            encoding='utf-8', errors='ignore')
    except OSError:
        pass
    m = _VER_TUPLE_RE.search(text)
    if m:
        return tuple(int(g) for g in m.groups())
    m = _DIR_NAME_RE.search(base.name)
    if m:
        return tuple(int(g) for g in m.groups())
    return None


# 常备 SDK 默认版本：8.x 跟随当前稳定版；7.x 为 7.4 系列最终维护版
# （Ren'Py 7 已停更，读 7.x 游戏的 .rpyc 必须用它）
DEFAULT_SDK_8 = '8.5.3'
DEFAULT_SDK_7 = '7.4.11'


def unmangle_mac_binaries(sdk_path) -> int:
    """还原 dmg 分发的 .macho 平台二进制，返回还原个数（幂等）。

    Ren'Py 打 macOS dmg 时为规避公证，把未签名二进制改名为 *.macho
    并加 5 字节 "RENPY" 头（见 launcher/game/distribute.rpy）；官方靠
    renpy.app 启动时 launcher（mac.rpy）剥头改回。我们 headless 直接
    跑 renpy.sh，不经 GUI——不还原则 `lib/<平台>/renpy` 缺失，
    renpy.sh line61 exec 直接 ENOENT（用户报错：生成失败 返回码 126）。

    找不到 *.macho（zip/Windows 布局）时零副作用；无 RENPY 头的
    .macho 只改名不剥数据（避免误伤）。
    """
    sdk_path = Path(sdk_path)
    count = 0
    for macho in sdk_path.rglob('*.macho'):
        try:
            data = macho.read_bytes()
        except OSError:
            continue
        # mac.rpy 无条件跳过 5 字节；此处仅在头存在时剥，更稳
        body = data[5:] if data.startswith(b'RENPY') else data
        target = macho.with_suffix('')  # renpy.macho → renpy
        try:
            target.write_bytes(body)
            target.chmod(0o755)
            macho.unlink()
            count += 1
        except OSError:
            continue
    return count


def find_installed_sdks() -> list:
    """扫描默认目录下已安装的 SDK，返回 [(version_tuple, path)]。

    覆盖：数据根与 exe 目录下的 tools/renpy-*-sdk、exe 目录下的
    renpy-*-sdk（开发态仓库根）。不支持自定义目录。
    """
    from rt_home import resources
    mgr = SDKManager()
    found, seen = [], set()

    def _add(path):
        path = Path(path)
        key = str(path.resolve())
        if key in seen or not mgr.is_valid_sdk(path):
            return
        v = detect_engine_version(path)
        if v:
            seen.add(key)
            found.append((v, path))

    for base in resources():
        tools = base / 'tools'
        if tools.is_dir():
            for cand in sorted(tools.glob('renpy-*-sdk')):
                _add(cand)
        for cand in sorted(base.glob('renpy-*-sdk')):
            _add(cand)
    return found


class SDKManager:
    """Ren'Py SDK 管理器"""

    def __init__(self, sdk_path: str = ""):
        self.sdk_path = Path(sdk_path) if sdk_path else None

    def is_valid_sdk(self, path: Path) -> bool:
        """检查是否是有效的 Ren'Py SDK"""
        # 检查必要的文件是否存在
        renpy_exe = self.get_renpy_exe(path)
        return renpy_exe.exists()

    def get_renpy_exe(self, sdk_path: Path = None) -> Path:
        """获取 renpy 可执行文件路径（多候选：Linux SDK 根的 renpy.sh、
        macOS .app 内脚本/二进制等）"""
        if sdk_path is None:
            sdk_path = self.sdk_path

        if sys.platform == 'win32':
            return sdk_path / 'renpy.exe'

        for rel in ('renpy.sh',
                    'renpy.app/Contents/MacOS/renpy.sh',
                    'renpy.app/Contents/MacOS/renpy'):
            p = sdk_path / rel
            if p.exists():
                return p
        # 都不存在时返回默认路径（is_valid_sdk 判定失败，错误信息含路径）
        return sdk_path / 'renpy.sh'

    # renpy.exe 在 Windows 上偶发访问冲突崩溃（0xC0000005，与负载/杀软扫描
    # 相关的间歇性崩溃）：无输出的纯崩溃值得自动重试，真实脚本错误不重试
    _CRASH_RETRIES = 3
    _ACCESS_VIOLATION = 0xC0000005  # 3221225477（有符号为 -1073741819）

    def generate_translations(self, game_dir: str, language: str = "chinese",
                              cancel_event=None) -> dict:
        """调用 Ren'Py 生成翻译文件

        Args:
            game_dir: 游戏目录路径
            language: 目标语言
            cancel_event: 可选 threading.Event，置位时终止子进程并返回
                {'success': False, 'cancelled': True, ...}

        Returns:
            {'success': bool, 'message': str, 'output': str}
            取消时额外含 'cancelled': True
        """
        if not self.sdk_path:
            return {'success': False, 'message': '未配置 Ren\'Py SDK 路径', 'output': ''}

        # 自愈：dmg 副本（含用户手动放入的存量安装）里 *.macho 未还原
        # 会让 renpy.sh exec 缺文件（返回码 126）——幂等，正常 SDK 零开销
        unmangle_mac_binaries(self.sdk_path)

        renpy_exe = self.get_renpy_exe()
        if not renpy_exe.exists():
            return {'success': False, 'message': f'找不到 {renpy_exe}', 'output': ''}

        # 构建命令 - 使用正确的 translate 命令
        cmd = [str(renpy_exe), str(game_dir), "translate", language]

        for attempt in range(1, self._CRASH_RETRIES + 1):
            result = self._run_once(cmd, language, cancel_event)
            rc = result.pop('returncode', 0)
            if (result['success'] or result.get('cancelled')
                    or rc & 0xFFFFFFFF != self._ACCESS_VIOLATION):
                return result
            if attempt < self._CRASH_RETRIES:
                print(f'[SDK] renpy.exe 访问冲突崩溃（0xC0000005），'
                      f'第 {attempt}/{self._CRASH_RETRIES} 次，2s 后重试')
                # 重试间隔也可被取消
                if cancel_event is not None and cancel_event.wait(2):
                    return {'success': False, 'cancelled': True,
                            'message': '已取消', 'output': ''}
            else:
                result['message'] += '（0xC0000005 间歇性崩溃，已重试仍失败，可再试一次）'
                return result
        return result  # 理论不可达

    def _run_once(self, cmd: list, language: str, cancel_event=None) -> dict:
        """执行一次 translate 命令（Popen + 轮询，支持取消/超时）"""
        proc = None
        try:
            print(f'[SDK] 执行命令: {" ".join(cmd)}')

            # 执行命令
            # cwd 必须用 SDK 目录而非游戏目录：游戏发行目录自带 renpy/
            # 引擎包，cwd 在游戏目录时它会抢占 sys.path——Python 代码用
            # 游戏的、原生模块（librenpython.dll 里的 render）用 SDK 的，
            # 大版本不一致即崩溃（如 'Cache' object has no attribute
            # 'get_renders'）。cwd 在 SDK 目录时两处都来自同一 SDK，自洽。
            #
            # Popen + 轮询而非 subprocess.run(timeout=...)：run 阻塞期间
            # 任务无法取消（最长 1 小时），轮询让 cancel_event 能及时生效。
            #
            # stdout 落临时文件而非 PIPE：管道 +「先等退出、退出后才读」
            # 在子进程输出超过管道缓冲（Windows 匿名管道默认 4KB）时互锁
            # ——子进程阻塞在写、父进程阻塞在等退出，直到 deadline 才报
            # 「执行超时」。工程根错误时 renpy 会倒出海量 duplicate label
            # 错误（实测 10KB+），必踩；临时文件无缓冲上限，取消/超时
            # 路径也不需要和读线程抢关闭时序。输出按 Ren'Py 实际编码
            # UTF-8 读（会回显脚本内容/报错，日文游戏的文本对 GBK 是
            # 非法序列——用系统区域编码读会 UnicodeDecodeError，真实
            # SDK 输出丢失、翻译实际成功也会被误报成失败）。
            with tempfile.TemporaryFile(
                    mode='w+', encoding='utf-8', errors='replace') as out_f:
                proc = subprocess.Popen(
                    cmd,
                    stdout=out_f,
                    stderr=subprocess.STDOUT,
                    cwd=str(self.sdk_path)
                )
                # 注册到全局表：服务关停时兜底杀树（取消/超时路径之外，
                # os._exit 抢在轮询循环响应前执行的话进程会变孤儿）
                track(proc)

                deadline = time.monotonic() + 3600
                while proc.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        # 取消：先 terminate 给子进程退出机会，不行再强杀
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        return {'success': False, 'cancelled': True,
                                'message': '已取消', 'output': '',
                                'returncode': -1}
                    if time.monotonic() > deadline:
                        proc.kill()
                        proc.wait()
                        return {'success': False, 'message': '执行超时',
                                'output': '', 'returncode': -1}
                    time.sleep(0.5)

                out_f.seek(0)
                output = out_f.read()
            print(f'[SDK] 输出:\n{output}')

            if proc.returncode == 0:
                return {
                    'success': True,
                    'message': f'成功生成 {language} 翻译文件',
                    'output': output, 'returncode': 0,
                }
            else:
                return {
                    'success': False,
                    'message': f'生成失败 (返回码: {proc.returncode})',
                    'output': output, 'returncode': proc.returncode,
                }

        except Exception as e:
            if proc is not None and proc.poll() is None:
                proc.kill()
            return {'success': False, 'message': str(e), 'output': '',
                    'returncode': -1}
        finally:
            untrack(proc)
            if proc is not None and proc.stdout:
                proc.stdout.close()

    def list_languages(self, game_dir: str) -> list:
        """列出已有的翻译语言"""
        tl_dir = Path(game_dir) / 'game' / 'tl'
        if not tl_dir.exists():
            return []

        languages = []
        for item in tl_dir.iterdir():
            if item.is_dir() and not item.name.startswith('_'):
                languages.append(item.name)

        return languages
