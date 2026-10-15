"""人名翻译 + 人物分析服务（从 name_panel 抽取，无 UI 依赖）

UI 层通过 hooks 接收行状态/进度回调：
    service.on_row_busy = lambda en_name: ...      # 开始处理（同步）
    service.on_row_done = async def(en_name): ...  # 处理结束刷新行（协程）
    service.on_progress = lambda i, total, text: ...
"""
import asyncio
import re
from concurrent.futures import ThreadPoolExecutor

import line_sampling
from database import ProjectDatabase
from logger import TranslationLogger
from prompts import (build_analyze_only_prompt, build_continue_prompt,
                     build_merge_summaries_prompt, build_translate_analyze_prompt)
from token_budget import TokenBudget
from translation_service import TranslationService
from translator import AITranslator, FatalAPIError, clean_name_result

PROFILE_KEYS = ['性格特点', '外貌特征', '说话风格', '口癖与口头禅', '称谓习惯',
                '行为习惯', '人物关系', '背景故事', '角色定位', '代表台词',
                '语气词建议', '翻译建议']


def split_self_check(text: str) -> tuple:
    """切出【自检】段：其行会被 parse_profile 误并入上一个键的值，
    解析档案前必须剥掉；补样/合并时保留全文（自检是定向补样的依据）。"""
    idx = (text or '').find('【自检】')
    if idx < 0:
        return text or '', ''
    return text[:idx], text[idx:]


def format_lines(rows) -> str:
    return '\n'.join(f'"{r["original_text"]}"' for r in rows)


def format_style_signals(signals: dict, evidence: dict) -> str:
    """line_sampling 统计结果 → 提示词文本；空结果返回 ''（可选段整段消失）"""
    parts = []
    tics = signals.get('tics') or []
    if tics:
        parts.append('口癖候选：' + '、'.join(
            f"{t['word']}（{t['count']}句/{t['scenes']}幕）" for t in tics))
    addrs = signals.get('addresses') or []
    if addrs:
        parts.append('称谓线索：' + '、'.join(
            f"{a['form']}（{a['count']}次）" for a in addrs))
    puncts = signals.get('punctuation') or []
    if puncts:
        parts.append('标点习惯：' + '、'.join(
            f"{p['form']}（{p['count']}次）" for p in puncts))
    if evidence.get('low_evidence'):
        parts.append(f"证据规模：约 {evidence.get('words', 0)} 词"
                     '（低于 3000 词，风格结论仅供参考）')
    solo = evidence.get('single_scene_tics') or []
    if solo:
        parts.append('仅单幕出现（存疑，可能是场景用词）：' + '、'.join(solo))
    return '\n'.join(parts)


def extract_name(text: str) -> str:
    """从 AI 返回中提取人名翻译"""
    match = re.search(r'【人名翻译】\s*\n\s*中文名[：:]\s*(.+)', text)
    if match:
        return clean_name_result(match.group(1).strip())
    return ''


def parse_profile(text: str) -> dict:
    """把 AI 结构化输出解析为 profile dict；解析不到任何键返回 None"""
    profile = {}
    current_key = None
    current_value = []

    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        found_key = False
        for key in PROFILE_KEYS:
            if line.startswith(key + '：') or line.startswith(key + ':'):
                if current_key:
                    profile[current_key] = '\n'.join(current_value).strip()
                current_key = key
                value_part = line.split('：', 1)[-1].split(':', 1)[-1].strip()
                current_value = [value_part] if value_part else []
                found_key = True
                break
        if not found_key and current_key:
            current_value.append(line)

    if current_key:
        profile[current_key] = '\n'.join(current_value).strip()
    return profile if profile else None


class NameTranslationService:
    """融合流程：一次 AI 调用同时完成人名翻译和人物分析（顺序处理）"""

    def __init__(self, db: ProjectDatabase, translator: AITranslator,
                 translation_service: TranslationService,
                 logger: TranslationLogger, max_context_k: int = 8):
        self.db = db
        self.translator = translator
        self.translation_service = translation_service
        self.logger = logger
        self.max_context_k = max_context_k
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._cancel = False

        # UI hooks（皆可 None）
        self.on_row_busy = None    # (en_name) -> None
        self.on_row_done = None    # (en_name) -> coroutine
        self.on_progress = None    # (i, total, text) -> None

    def stop(self):
        self._cancel = True

    def close(self):
        """关闭内部线程池。

        本服务由 state 按项目会话缓存，项目切换/配置变化重建实例时
        必须先 close 旧实例，否则 ThreadPoolExecutor 线程持续累积。
        """
        self._executor.shutdown(wait=False)

    def begin_single(self):
        """单条翻译前重置批量任务残留状态。

        服务实例在项目会话内被缓存复用：上次批量任务可能留下取消标记
        （不重置会让分段循环直接 break）和指向已结束 job 的 hooks。
        """
        self._cancel = False
        self.on_progress = None
        self.on_row_busy = None
        self.on_row_done = None

    @property
    def cancelled(self) -> bool:
        return self._cancel

    async def _emit_busy(self, en_name: str):
        if self.on_row_busy:
            self.on_row_busy(en_name)

    async def _emit_done(self, en_name: str):
        if self.on_row_done:
            await self.on_row_done(en_name)

    def _dict_text(self) -> str:
        """人名表 + 术语表提示词文本（同步，调用方放 executor）"""
        glossary_text = self.db.get_glossary_for_prompt()
        char_prompt = self.db.get_characters_for_prompt()
        text = ""
        if char_prompt:
            text += char_prompt + "\n"
        if glossary_text:
            text += glossary_text
        return text

    async def translate_name_only(self, en_name: str, variable: str = None):
        """仅翻译人名（不分析）。

        落库必须带 variable：同名显示名的角色靠它精确定位
        （translation_service.translate_single 的 name 保存不带
        variable，同名会串行，不能复用）。
        """
        loop = asyncio.get_event_loop()
        # 无显示名角色（泛指形参/玩家命名主角）：前端以变量名兜底
        # 回传，这里按库中真实显示名核实
        if en_name and variable:
            row = next((c for c in self.db.get_characters()
                        if c.get('variable') == variable), None)
            if row is not None and not (row.get('display_name') or '').strip():
                en_name = ''
        label = en_name or (variable or '')
        if (not en_name.strip()
                or (en_name.startswith('[') and en_name.endswith(']'))):
            self.logger.info(f'占位符 {label}，跳过人名翻译', panel='names')
            return
        await self._emit_busy(en_name)
        try:
            dict_text = await loop.run_in_executor(None, self._dict_text)
            cn = await loop.run_in_executor(
                self._executor,
                lambda: self.translator.translate_name(
                    en_name, glossary_text=dict_text))
            cn = clean_name_result(cn or '')
            if cn:
                await loop.run_in_executor(
                    None, lambda: self.db.update_character_cn_name(
                        en_name, cn, variable=variable))
                self.logger.info(f'人名: {en_name} -> {cn}', panel='names')
        except FatalAPIError:
            raise
        except Exception as e:
            self.logger.error(f'{label} 人名翻译失败: {e}', panel='names')
        finally:
            await self._emit_done(label)

    async def translate_and_analyze(self, en_name: str, variable: str = None,
                                    mode: str = 'both'):
        """翻译人名 + 分析角色。台词超长自动分段：
        第1段翻译人名+分析，后续段补充分析，最终合并。

        mode: 'both'（翻译+分析）/ 'name'（仅翻译人名，委托
        translate_name_only）/ 'analyze'（仅分析，不动人名）。
        variable：角色变量名（同名显示名的角色靠它精确定位）
        FatalAPIError 向上抛出以中止批量任务；其他错误记日志后返回。
        """
        if mode == 'name':
            await self.translate_name_only(en_name, variable)
            return
        loop = asyncio.get_event_loop()

        # 无显示名角色（泛指形参/玩家命名主角，display_name 留空）：
        # 前端以变量名兜底显示/回传，这里按库中真实显示名核实
        if en_name and variable:
            row = next((c for c in self.db.get_characters()
                        if c.get('variable') == variable), None)
            if row is not None and not (row.get('display_name') or '').strip():
                en_name = ''

        # 占位符（如 [mc_name]、[hero]）或无显示名角色不翻译人名，
        # 仅分析角色
        is_placeholder = (
            not en_name.strip()
            or (en_name.startswith('[') and en_name.endswith(']')))
        label = en_name or (variable or '')
        if is_placeholder:
            self.logger.info(f'占位符 {label}，跳过人名翻译，仅分析角色', panel='names')
            # cn_name=原名 的标记只在带翻译的模式做（仅分析不动人名）
            if en_name and mode != 'analyze':
                await loop.run_in_executor(
                    None, lambda: self.db.update_character_cn_name(en_name, en_name, variable=variable)
                )

        await self._emit_busy(en_name)

        try:
            # 加载该角色的台词（有变量名直接查，同名显示名不会串）
            def _load_rows():
                var_name = variable
                if not var_name:
                    variable_map = self.db.get_variable_map()
                    for var, display in variable_map.items():
                        if display == en_name:
                            var_name = var
                            break
                if var_name:
                    return self.db.get_dialogues_by_character(var_name)
                return []

            char_rows = await loop.run_in_executor(None, _load_rows)

            if not char_rows:
                # 无台词角色：仅分析模式只留空档案；翻译经
                # translate_name_only（带 variable 落库，同名不串）
                self.logger.info(f'{label} 没有台词，仅翻译人名', panel='names')
                if en_name and mode != 'analyze':
                    await self.translate_name_only(en_name, variable)
                empty_profile = {'性格特征': '该角色没有台词', '说话风格': '无', '背景': '无'}
                await loop.run_in_executor(
                    None, lambda: self.db.save_profile(en_name, empty_profile, variable=variable)
                )
                await self._emit_done(label)
                return

            dict_text = await loop.run_in_executor(None, self._dict_text)

            # 零 LLM 预筛 + 槽位抽样（调研结论：均匀抽样漏稀缺口癖）；
            # 预算 = 上限 300 句，且 Pass1（core ≈ 60%）要装进单次调用窗口
            def _sample():
                all_rows = self.db.get_all_dialogues()
                per_call = TokenBudget(self.max_context_k * 1024).name_batch_size(
                    len(char_rows))
                budget = min(TokenBudget.NAME_MAX_INPUT_LINES,
                             max(20, int(per_call / 0.6)))
                return line_sampling.sample_for_analysis(
                    char_rows, all_rows, budget=budget)

            sample = await loop.run_in_executor(None, _sample)
            style_signals = format_style_signals(sample['signals'],
                                                 sample['evidence'])
            core_rows, support_rows = sample['core'], sample['support']
            if not core_rows and not support_rows:
                # 全是极短句/模板句时打分会清空池：退回原始台词兜底
                core_rows = char_rows[:30]
            stats = sample['stats']
            self.logger.info(
                f'{label} 台词 {stats["total"]} 条（去重后 {stats["after_dedup"]}），'
                f'抽样 {len(core_rows) + len(support_rows)} 条'
                + ('（两段分析）' if support_rows else '（单次分析）'),
                panel='names')

            cn_name = ''
            summaries = []
            total_passes = 2 if support_rows else 1

            # Pass1：core（口癖配额 + 通用启发式），带风格线索
            action = '分析' if (is_placeholder or mode == 'analyze') else '翻译+分析'
            self.logger.info(f'[1/{total_passes}] {action} {label}',
                             panel='names')
            lines_text = format_lines(core_rows)
            if is_placeholder or mode == 'analyze':
                prompt = self._build_analyze_only_prompt(
                    label, lines_text, 0, total_passes, style_signals)
            else:
                prompt = self._build_translate_analyze_prompt(
                    en_name, lines_text, 0, total_passes, dict_text,
                    style_signals)

            result = await loop.run_in_executor(
                self._executor,
                lambda: self.translator.analyze_text(prompt=prompt,
                                                     task_type='name_analysis')
            )
            if mode != 'analyze':
                cn_name = extract_name(result)
            summaries.append(result)

            # Pass2：support（剧情覆盖 + 探索槽），按 Pass1 自检定向补足。
            # 取消即止：已成的 Pass1 是完整草稿，但计划要求取消不落库，
            # 避免半截档案整覆盖已有档案
            if support_rows:
                if self._cancel:
                    self.logger.info(f'{label} 已取消，丢弃未完成分析', panel='names')
                    await self._emit_done(label)
                    return
                self.logger.info(f'[2/{total_passes}] 补充分析 {label}', panel='names')
                prompt = self._build_continue_prompt(
                    label, format_lines(support_rows), 1, 2, summaries)
                result = await loop.run_in_executor(
                    self._executor,
                    lambda: self.translator.analyze_text(prompt=prompt,
                                                         task_type='name_analysis')
                )
                if self._cancel:
                    self.logger.info(f'{label} 已取消，丢弃未完成分析', panel='names')
                    await self._emit_done(label)
                    return
                summaries.append(result)

            # 保存人名翻译（占位符与仅分析模式不翻译）
            if cn_name and not is_placeholder and mode != 'analyze':
                await loop.run_in_executor(
                    None, lambda: self.db.update_character_cn_name(en_name, cn_name, variable=variable)
                )
                self.logger.info(f'人名: {en_name} -> {cn_name}', panel='names')

            # 解析/合并档案（【自检】段不进 profile 解析）
            if len(summaries) == 1:
                head, _tail = split_self_check(summaries[0])
                profile = parse_profile(head)
            else:
                profile = await self._merge_summaries(label, summaries)

            if profile:
                await loop.run_in_executor(
                    None, lambda: self.db.save_profile(en_name, profile, variable=variable)
                )
                self.logger.info(f'{label} 分析完成', panel='names')

            await self._emit_done(label)

        except FatalAPIError:
            await self._emit_done(label)
            raise
        except Exception as e:
            self.logger.error(f'{label} 翻译+分析失败: {e}', panel='names')
            await self._emit_done(label)

    async def translate_all(self, mode: str = 'both') -> dict:
        """批量处理角色。mode: 'both' 翻译+分析 / 'name' 仅翻译 /
        'analyze' 仅分析。

        'both' 逐行按缺口补：缺人名的翻译+分析，已有人名只缺画像的
        仅分析（不覆盖人工译名/保留原名标记）。
        Returns: {'completed': int, 'total': int, 'stopped': bool, 'nothing': bool}
        """
        self._cancel = False
        loop = asyncio.get_event_loop()
        completed_count = 0
        action = {'both': '翻译+分析', 'name': '翻译', 'analyze': '分析'}[mode]

        if mode == 'name':
            chars_todo = await loop.run_in_executor(
                None, self.db.get_untranslated_characters)
            todo = [(c['display_name'], c['variable'] or None, 'name')
                    for c in chars_todo]
        else:
            all_chars = await loop.run_in_executor(None, self.db.get_characters)
            todo = []
            for c in all_chars:
                if c['is_placeholder']:
                    continue
                display = (c.get('display_name') or '').strip()
                named = bool((c.get('cn_name') or '').strip())
                if mode == 'both' and display and not named:
                    # 缺人名：翻译+分析（画像也一并出）
                    todo.append((c['display_name'], c['variable'] or None,
                                 'both'))
                elif not c.get('profile_json'):
                    # 只缺画像（含无显示名角色）：仅分析，不动人名
                    todo.append((display, c['variable'] or None, 'analyze'))

        total = len(todo)
        if total == 0:
            return {'completed': 0, 'total': 0, 'stopped': False, 'nothing': True}
        self.logger.info(f'开始{action} {total} 个角色', panel='names')

        for i, (name, var, row_mode) in enumerate(todo):
            if self._cancel:
                break
            if self.on_progress:
                self.on_progress(i, total, f'正在{action}... ({i+1}/{total}) {name}')
            try:
                await self.translate_and_analyze(name, variable=var,
                                                 mode=row_mode)
                completed_count += 1
            except FatalAPIError as e:
                self.logger.error(f'API 致命错误，批量任务中止: {e}', panel='names')
                self._cancel = True
                raise
            except Exception as e:
                self.logger.error(f'{name} {action}失败: {e}', panel='names')

        self.logger.info(f'{action}完成: {completed_count}/{total}', panel='names')
        return {'completed': completed_count, 'total': total,
                'stopped': self._cancel, 'nothing': False}

    async def _merge_summaries(self, name: str, summaries: list) -> dict:
        """合并多段分析结果为最终人物特征"""
        loop = asyncio.get_event_loop()
        prompt = build_merge_summaries_prompt(name, summaries)

        result = await loop.run_in_executor(
            self._executor,
            lambda: self.translator.analyze_text(prompt=prompt,
                                                 task_type='name_analysis')
        )
        return parse_profile(result)

    # ---- 提示词构建（实现在 prompts.py） ----

    @staticmethod
    def _build_analyze_only_prompt(en_name, lines_text, batch_idx, total_batches,
                                   style_signals=''):
        return build_analyze_only_prompt(en_name, lines_text, batch_idx,
                                         total_batches, style_signals)

    @staticmethod
    def _build_translate_analyze_prompt(en_name, lines_text, batch_idx,
                                        total_batches, dict_text,
                                        style_signals=''):
        return build_translate_analyze_prompt(en_name, lines_text, batch_idx,
                                              total_batches, dict_text,
                                              style_signals)

    @staticmethod
    def _build_continue_prompt(en_name, lines_text, batch_idx, total_batches, summaries):
        return build_continue_prompt(en_name, lines_text, batch_idx, total_batches, summaries)
