"""项目创建服务（从 project_panel 抽取，无 UI 依赖）

管线：建库 → 复制游戏文件 → 解包 rpa/反编译 rpyc → 官中检测(需用户确认)
→ SDK 生成模板 → 解析 tl → 解析角色 → 定位 UI 上下文 → 清理冲突。
与更新管线共用的编排步骤在 services/game_pipeline.py。

用法:
    creator = ProjectCreator(project_manager, logger, get_sdk_path)
    result = await creator.create(
        name, game_dir,
        progress=lambda pct, text: ...,                    # 同步回调
        confirm_official_chinese=async def (file_count) -> str,
            返回 'delete'（删除官中重译）/ 'keep'（保留官中续译）/
            'cancel'（取消创建）；旧布尔契约 True/False 等价 delete/cancel,
    )
返回 {'success': True, 'dialogues': n, 'ui_texts': m} 或 {'cancelled': True}。
异常：抛出前已关闭 db（否则 Windows 上清理目录 WinError 32）。
UnrpycMissingError 原样上抛（调用方负责提示安装方法并清理项目目录）。
"""
import asyncio
import json as _json
import re
import shutil
import zipfile
from datetime import datetime
from functools import partial
from pathlib import Path

from logger import TranslationLogger
from project_manager import ProjectManager
from services.game_pipeline import (  # noqa: F401（_unrpyc_python_exe/copy_game_files 为兼容旧导入再导出）
    _unrpyc_python_exe, copy_game_files, copy_with_progress,
    find_app_root, locate_ui_hints, refresh_characters, resolve_game_root,
    resolve_sdk_or_raise, sync_app_shell, unpack_and_decompile,
)

# 中文相关语言目录名（小写匹配）
CHINESE_LANG_NAMES = {
    'chinese', 'zh', 'ch', 'chs', 'cht', 'zhs', 'zht',
    'schinese', 'tchinese', 'cn', 'zh-cn', 'zh-tw', 'zh-hans', 'zh-hant',
    'simplified_chinese', 'traditional_chinese',
}


def detect_official_chinese(game_work_dir: Path) -> int:
    """检测游戏 tl 目录下是否已有中文相关翻译目录

    解压后、SDK 生成前调用。tl 下若存在中文相关目录（Chinese/zh/ch/zhs 等）
    且其中有 .rpy/.rpyc，即判定存在官中/第三方汉化（.rpyc 为编译版汉化）。
    返回中文目录中的翻译文件数，0 表示无官中。
    """
    tl_root = game_work_dir / 'game' / 'tl'
    if not tl_root.exists():
        return 0
    count = 0
    for lang_dir in tl_root.iterdir():
        if not lang_dir.is_dir():
            continue
        if lang_dir.name.lower() in CHINESE_LANG_NAMES:
            count += sum(1 for _ in lang_dir.rglob('*.rpy'))
            count += sum(1 for _ in lang_dir.rglob('*.rpyc'))
    return count


def remove_official_chinese(game_work_dir: Path, logger: TranslationLogger = None):
    """删除 tl 下的中文相关翻译目录（含 .rpy/.rpyc/图片等）

    SDK 之后会重建干净的模板目录。
    """
    tl_root = game_work_dir / 'game' / 'tl'
    if not tl_root.exists():
        return
    for lang_dir in tl_root.iterdir():
        if not lang_dir.is_dir():
            continue
        if lang_dir.name.lower() in CHINESE_LANG_NAMES:
            try:
                shutil.rmtree(lang_dir)
                if logger:
                    logger.info(f'已删除中文翻译目录: tl/{lang_dir.name}', panel='projects')
            except OSError as e:
                if logger:
                    logger.warning(f'删除中文目录失败 {lang_dir.name}: {e}', panel='projects')


def normalize_official_chinese(game_work_dir: Path,
                               logger: TranslationLogger = None) -> str | None:
    """保留官中时归一语言目录：非 chinese 的中文目录改名为 chinese。

    SDK 只生成 tl/chinese、导出与运行时也只认 chinese——官方译文若在
    schinese/zh 等目录，改名归并后才能被 SDK 追加与解析入库。
    已存在 chinese 时不改名（避免覆盖），返回 None；改名成功返回原目录名。
    """
    tl_root = game_work_dir / 'game' / 'tl'
    if not tl_root.exists():
        return None
    chinese = tl_root / 'chinese'
    for lang_dir in sorted(tl_root.iterdir()):
        if not lang_dir.is_dir() or lang_dir.name == 'chinese':
            continue
        if lang_dir.name.lower() not in CHINESE_LANG_NAMES:
            continue
        if chinese.exists():
            if logger:
                logger.warning(
                    f'同时存在多个中文翻译目录（chinese 与 {lang_dir.name}），'
                    f'保留官中仅使用 chinese，{lang_dir.name} 保持原样',
                    panel='projects')
            continue
        lang_dir.rename(chinese)
        if logger:
            logger.info(f'保留官中：tl/{lang_dir.name} 归并为 tl/chinese',
                        panel='projects')
        return lang_dir.name
    return None


def dedupe_translations(result: dict) -> dict:
    """去掉「同 (file, 原文) 已有已译行」的未译重复块。

    Ren'Py 以 identifier 决定是否重生成：保留官中重跑 SDK 时若
    identifier 与官方失配（反编译源码摘要差异），会给已译块追加一条
    空译副本——同文件同原文存在已译行时丢弃未译行。两侧都已译
    （原文重复的真实台词）或都未译（原文重复的两处台词）则都保留。
    """
    def _dedupe(rows: list) -> list:
        translated_keys = {
            (r.get('file_path', ''), r.get('original_text', ''))
            for r in rows if r.get('translated_text')
        }
        return [
            r for r in rows
            if not (r.get('translated_text') == ''
                    and (r.get('file_path', ''), r.get('original_text', ''))
                    in translated_keys)
        ]

    return {
        **result,
        'dialogues': _dedupe(result.get('dialogues', [])),
        'ui_texts': _dedupe(result.get('ui_texts', [])),
    }


def cleanup_conflicts(game_dir: Path, logger: TranslationLogger = None):
    """清理冲突文件：.rpa 存档与自动提取的 .rpy"""
    game_sub = Path(game_dir) / 'game'
    for rpa_file in game_sub.glob('*.rpa'):
        rpa_file.unlink()
    scripts_dir = game_sub / 'scripts'
    if scripts_dir.exists():
        for rpy_file in scripts_dir.glob('*.rpy'):
            py_file = rpy_file.with_suffix('.py')
            if py_file.exists():
                try:
                    with open(rpy_file, 'r', encoding='utf-8') as f:
                        content = f.read(100)
                    if '从.rpyc文件自动提取' in content or '\x00' in content:
                        rpy_file.unlink()
                except Exception as e:
                    if logger:
                        logger.warning(f'清理冲突文件 {rpy_file.name}: {e}')
                    rpy_file.unlink()


def extract_game_zip(zip_path, extract_dir: Path, progress_cb=None) -> str:
    """解压游戏 zip（同步，调用方放 executor）。progress_cb(current, total)。

    返回解压出的游戏目录（zip 内只有一个顶层目录时返回该目录）。
    失败抛异常。
    """
    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as zf:
        members = zf.namelist()
        total = len(members)
        for i, member in enumerate(members):
            zf.extract(member, str(extract_dir))
            if progress_cb:
                progress_cb(i + 1, total)
    entries = list(extract_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return str(entries[0])
    return str(extract_dir)


# SDK 的精简标准库可能缺游戏脚本 init 阶段 import 的模块：这类模块作者
# 通常直接塞进发行包 lib/pythonX.Y（如 Lab Rats 2 在脚本里 import unittest，
# 其依赖 pprint 就是作者手动放进自带 lib 的），SDK 里没有 → translate 跑
# init 即 ImportError。玩家正常玩没事（用的是游戏自带 lib），只有 SDK 生成
# 模板时中招。按报错从游戏自带运行库把该模块复制进项目副本 game/ 下
# （loader 会从 game/ 找到它）后重试。
# py2: No module named pprint；py3: No module named 'pprint'
_MISSING_MOD_RE = re.compile(
    r"(?:ImportError|ModuleNotFoundError): No module named (?:'([^']+)'|(\S+))")


def heal_missing_module(game_work_dir: Path, output: str):
    """按 SDK 输出补齐 init 缺失的标准库模块，返回模块名（未补则 None）

    源只认游戏自带的 lib/pythonX.Y（作者补丁所在），找不到源、或 game/ 下
    已有同名模块仍报错（注入无效）时返回 None，走原有报错路径。
    """
    m = _MISSING_MOD_RE.search(output or '')
    if not m:
        return None
    name = (m.group(1) or m.group(2)).split('.')[0]
    game_sub = game_work_dir / 'game'
    if (game_sub / (name + '.py')).exists() or (game_sub / name).is_dir():
        return None
    for lib in sorted((game_work_dir / 'lib').glob('python*')):
        src = lib / (name + '.py')
        if src.is_file():
            shutil.copyfile(src, game_sub / (name + '.py'))
            return name
        pkg = lib / name
        if (pkg / '__init__.py').is_file():
            shutil.copytree(pkg, game_sub / name,
                            copy_function=shutil.copyfile)
            return name
    return None


async def generate_tl_templates(sdk_path: str, game_work_dir: Path,
                                decompiled_rel: list, db,
                                logger: TranslationLogger,
                                progress, run_in_executor,
                                cancel_event=None) -> list:
    """SDK 生成翻译模板（自愈重试：隔离损坏的反编译文件后重试）。

    阻塞调用通过 run_in_executor(fn, *args) 调度（调用方传入绑定 loop 与
    None executor 的偏函数）。db 用于回写 decompiled_rpy_files meta；
    progress(pct, text)。返回隔离的 .broken 相对路径列表。
    cancel_event: 可选 threading.Event，置位时终止 SDK 子进程并抛异常。
    失败抛异常（含 SDK 输出尾部）。
    """
    from sdk_manager import SDKManager
    sdk = SDKManager()
    sdk.sdk_path = Path(sdk_path)

    def _sdk():
        return sdk.generate_translations(str(game_work_dir), 'chinese',
                                         cancel_event=cancel_event)

    # 反编译产物可能有语法瑕疵（unrpyc 对部分语句还原不完美，
    # 如空 scene 块），translate 解析到就整体失败。把报错的
    # 反编译文件隔离（改名 .rpy.broken）后重试：损失该文件的
    # 模板，但保住整项目创建。游戏自带源文件报错则不降级，原样抛。
    err_re = re.compile(r'File "([^"]+\.rpy)", line \d+')
    decompiled_set = set(decompiled_rel)
    quarantined = []
    for attempt in range(5):
        sdk_result = await run_in_executor(_sdk)
        if sdk_result['success']:
            break
        if sdk_result.get('cancelled'):
            raise Exception('已取消：SDK 生成翻译模板被中止')
        bad = None
        for m in err_re.finditer(sdk_result.get('output') or ''):
            rel = m.group(1).replace('\\', '/')
            if rel in decompiled_set:
                bad = rel
                break
        if bad is None:
            healed = heal_missing_module(
                game_work_dir, sdk_result.get('output') or '')
            if healed:
                logger.warning(
                    f'游戏 init 引用了 SDK 标准库缺失的模块 {healed}，'
                    '已从游戏自带运行库复制到项目副本，重试生成...',
                    panel='projects')
                progress(0.55, f'已补齐缺失模块 {healed}，重试生成...')
                continue
            # 必须带上 SDK 输出：返回码本身无法区分是游戏脚本
            # 解析失败还是 SDK 版本不匹配
            tail = (sdk_result.get('output') or '')[-2000:].strip()
            detail = f'\nSDK 输出:\n{tail}' if tail else ''
            raise Exception(
                f'SDK 生成翻译文件失败: {sdk_result["message"]}{detail}')
        src = game_work_dir / bad
        src.rename(src.with_name(src.name + '.broken'))
        quarantined.append(bad + '.broken')
        logger.warning(
            f'反编译文件语法错误，已隔离（该文件的对话将缺失）: {bad}',
            panel='projects')
        progress(0.55, '已隔离损坏的反编译文件，重试生成...')
    else:
        raise Exception(
            'SDK 生成翻译文件失败：损坏的反编译文件过多'
            f'（已隔离 {len(quarantined)} 个仍有报错）')
    if quarantined:
        # 隔离文件加入导出清理清单，并给用户一条汇总
        await run_in_executor(
            db.set_meta, 'decompiled_rpy_files',
            _json.dumps(decompiled_rel + quarantined))
        logger.warning(
            f'共隔离 {len(quarantined)} 个损坏的反编译文件，'
            '对应对话不会出现在翻译列表中', panel='projects')

    # 只有 common.rpy = 游戏脚本一条都没进模板（典型原因：
    # 游戏引擎与 SDK 大版本不匹配，SDK 读不了游戏的 .rpyc），
    # 继续走只会得到一个没有任何对话的空项目。
    # 注意模板目录镜像游戏脚本的子目录结构（脚本在 game/scripts/ 下时
    # 模板落在 tl/chinese/scripts/），必须递归查找。
    tl_out = game_work_dir / 'game' / 'tl' / 'chinese'
    has_templates = tl_out.exists() and any(
        p.name != 'common.rpy' for p in tl_out.rglob('*.rpy'))
    if not has_templates:
        raise Exception(
            "SDK 未为游戏脚本生成任何翻译模板（只有 common.rpy）。\n"
            "通常是游戏引擎版本与 SDK 不匹配——请检查游戏的 Ren'Py "
            "版本，在模型配置页下载对应大版本的 SDK 后重新创建项目")
    return quarantined


def parse_tl_dir(tl_dir: Path, game_work_dir: Path,
                 logger: TranslationLogger) -> dict:
    """解析 SDK 生成的 tl/chinese 目录（同步，调用方放 executor）"""
    from tl_parser import parse_translation_files
    result = parse_translation_files(tl_dir, str(game_work_dir), logger=logger)
    # 保留官中重跑时 identifier 失配会追加未译副本，按 (file, 原文) 去重
    return dedupe_translations(result)


class ProjectCreator:
    """创建项目（异步编排；阻塞操作全部 run_in_executor）"""

    def __init__(self, project_manager: ProjectManager,
                 logger: TranslationLogger, get_sdk_path=None):
        self.project_manager = project_manager
        self.logger = logger
        # get_sdk_path(game_dir)：按游戏引擎大版本解析匹配的 SDK 路径
        self.get_sdk_path = get_sdk_path

    async def create(self, name: str, game_dir: str,
                     progress, confirm_official_chinese=None,
                     cancel_event=None) -> dict:
        """执行创建。progress(pct, text) 同步回调；confirm_official_chinese
        为 async (file_count) -> str：'delete' 删除官中重译、'keep' 保留
        官中续译、'cancel' 取消创建（旧布尔 True/False 等价 delete/cancel）。
        cancel_event: 可选 threading.Event，传给 SDK 子进程以便中止。

        用户取消：关闭 db、删除项目目录，返回 {'cancelled': True}。
        """
        loop = asyncio.get_event_loop()
        _rie = partial(loop.run_in_executor, None)
        db = None
        # macOS .app 发行包顶层不是工程根（game/ 在 Contents/Resources/
        # autorun 下），先定位真根再建库/复制——否则下游全程找不到 game/
        # 顺带记录 .app 壳位置：导出时把骨架快照套回，产出可双击的 .app
        resolved_root = resolve_game_root(game_dir)
        app_root = find_app_root(resolved_root)
        game_dir = str(resolved_root)
        try:
            # 步骤1: 创建项目数据库
            progress(0.05, '正在初始化项目...')

            db = await _rie(
                self.project_manager.create_project, name, game_dir
            )
            project_dir = self.project_manager.project_dir(name)
            if app_root:
                progress(0.055, '正在保留 macOS 应用壳...')
                await _rie(sync_app_shell, project_dir, app_root,
                           self.logger)
            game_work_dir = project_dir / 'game'

            # 步骤2: 复制游戏文件（逐文件复制，带进度）
            progress(0.06, '正在复制游戏文件...')
            await copy_with_progress(_rie, game_dir, game_work_dir, progress,
                                     0.06, 0.10)
            progress(0.30, '游戏文件复制完成')

            # 步骤3: 解包 rpa & 反编译 rpyc
            rel_files = await unpack_and_decompile(
                _rie, game_work_dir, db, self.logger, progress)

            # 步骤3.5: 检测官方中文翻译，确认后处理（删除重译 / 保留续译）
            # 必须在解包后（官中可能在 rpa 里）、SDK 生成前检测
            official_tl = await _rie(detect_official_chinese, game_work_dir)
            if official_tl:
                progress(0.50, '检测到游戏自带中文翻译...')
                if confirm_official_chinese is None:
                    raise RuntimeError('检测到官方中文翻译但未提供确认回调')
                action = await confirm_official_chinese(official_tl)
                # 兼容旧布尔契约：True=删除继续，False=取消
                if action is True:
                    action = 'delete'
                elif action is False:
                    action = 'cancel'
                if action == 'cancel':
                    # 用户取消：关闭 db、删除已生成的项目目录
                    progress(0.50, '已取消创建')
                    await _rie(db.close)
                    db = None
                    await _rie(self.project_manager.delete_project, name)
                    self.logger.info('用户取消创建：检测到游戏自带中文翻译', panel='projects')
                    return {'cancelled': True}
                if action == 'delete':
                    progress(0.50, '正在删除官方中文翻译...')
                    await _rie(remove_official_chinese, game_work_dir, self.logger)
                    self.logger.info(f'已删除官方中文翻译（{official_tl} 个文件）', panel='projects')
                else:
                    # 保留官中：语言目录归一到 chinese，SDK 追加未译条目，
                    # 官方译文经 parse_tl_dir 直接入库为已译
                    progress(0.50, '保留官方中文翻译，继续...')
                    renamed = await _rie(normalize_official_chinese,
                                          game_work_dir, self.logger)
                    msg = f'保留官方中文翻译（{official_tl} 个文件）'
                    if renamed:
                        msg += f'，tl/{renamed} 归并为 tl/chinese'
                    self.logger.info(msg, panel='projects')

            # 步骤4: SDK 生成翻译文件（自愈重试）
            sdk_path = await resolve_sdk_or_raise(
                _rie, self.get_sdk_path, game_work_dir, '创建')
            if sdk_path:
                progress(0.55, '正在使用 SDK 生成翻译文件...')
                await generate_tl_templates(
                    sdk_path, game_work_dir, rel_files, db,
                    self.logger, progress, _rie, cancel_event=cancel_event)

            progress(0.60, 'SDK 模板就绪')

            # 步骤5: 解析翻译文件
            tl_dir = game_work_dir / 'game' / 'tl' / 'chinese'
            tl_exists = await _rie(tl_dir.exists)
            tl_result = {}
            if tl_exists:
                progress(0.70, '正在解析翻译文件...')
                tl_result = await _rie(
                    parse_tl_dir, tl_dir, game_work_dir, self.logger)

                def _save_tl():
                    db.insert_dialogues(tl_result.get('dialogues', []))
                    db.insert_ui_texts(tl_result.get('ui_texts', []))

                await _rie(_save_tl)

            # 步骤6: 解析角色 + 统计台词数（无模板时也保留角色清单）
            progress(0.80, '正在解析角色信息...')
            await _rie(refresh_characters, game_work_dir, db,
                       tl_result.get('dialogues', []))

            # 步骤6.5: 回扫源码定位 UI 字符串出处
            if tl_exists:
                progress(0.90, '正在定位字符串上下文...')
                await locate_ui_hints(
                    _rie, game_work_dir, db, self.logger, '建项')

            progress(0.95, '正在清理冲突文件...')

            # 步骤7: 清理冲突文件
            await _rie(cleanup_conflicts, game_work_dir, self.logger)

            # 更新时间戳和统计
            def _finalize():
                db.set_meta("updated_at", datetime.now().isoformat())
                d_count = db.get_dialogue_count()
                u_count = db.get_ui_text_count()
                db.close()
                return d_count, u_count

            d_count, u_count = await _rie(_finalize)
            db = None

            progress(1.0, f'✅ 创建成功！{d_count["total"]} 条对话, {u_count["total"]} 条字符串')
            return {'success': True,
                    'dialogues': d_count['total'], 'ui_texts': u_count['total']}

        except Exception:
            if db is not None:
                try:
                    await _rie(db.close)
                except Exception:
                    pass
            raise
