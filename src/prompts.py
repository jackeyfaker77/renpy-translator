"""提示词模板入口（薄封装，模板正文在 prompt_defaults / 用户覆盖层）

全部 build_* 经 prompt_store.render 渲染生效模板：
用户可在「提示词设置」页自定义（全局/项目覆盖），未覆盖走内置默认。
动态数据（台词/术语表/JSON 等）仍由调用方算好经占位符传入。
"""

from typing import List

import prompt_store

# 兼容旧导入面（translator / name_translation 引用这些名字）
ANALYSIS_SYSTEM_PROMPT_ID = 'analysis.system'
STYLE_GUIDE_PROMPT_ID = 'style_guide'


def _render(pid: str, **kwargs) -> str:
    return prompt_store.render(pid, **kwargs)


# ========== 对话翻译 ==========

def build_system_prompt(glossary_text: str = "",
                        character_profile: str = "",
                        style_guide: str = "") -> str:
    """构建对话翻译系统提示词"""
    return _render('dialogue.system', glossary_text=glossary_text,
                   character_profile=character_profile, style_guide=style_guide)


def build_user_prompt(text: str, character: str = "",
                      context_before: List[dict] = None,
                      context_after: List[dict] = None) -> str:
    """构建单句翻译用户提示词

    context_before/after 格式：
    [{'original_text': '...', 'translated_text': '...', 'character': '...'}]
    """
    return _render('dialogue.user', text=text, character=character,
                   context_before_block=_context_block(context_before),
                   context_after_block=_context_block(context_after))


def _context_block(items: List[dict] = None) -> str:
    """上下文行块（行格式暂不开放编辑，整块注入模板）

    行间以 \\n 分隔、不带行尾换行——段间空行由模板的空行负责，
    接缝处保持单空行。
    """
    if not items:
        return ''
    out = []
    for item in items:
        char = item.get('character', '') or '旁白'
        orig = item.get('original_text', '')
        trans = item.get('translated_text', '')
        if trans:
            out.append(f'[已译] {char}: "{orig}" → "{trans}"')
        else:
            out.append(f'{char}: "{orig}"')
    return '\n'.join(out)


# ========== 人名翻译 ==========

def build_name_system_prompt(glossary_text: str = "") -> str:
    """构建人名翻译系统提示词"""
    return _render('name.system', glossary_text=glossary_text)


def build_name_user_prompt(name: str) -> str:
    return _render('name.user', name=name)


# ========== UI 翻译 ==========

def build_ui_system_prompt(glossary_text: str = "",
                           style_guide: str = "") -> str:
    """构建 UI 翻译系统提示词"""
    return _render('ui.system', glossary_text=glossary_text,
                   style_guide=style_guide)


def build_ui_user_prompt(text: str) -> str:
    return _render('ui.user', text=text)


# ========== 批次翻译 ==========

def build_batch_user_prompt(items: List[dict],
                            context_before: List[dict] = None,
                            content_type: str = 'dialogue') -> str:
    """构建批次翻译用户提示词（JSON 结构化输入）

    待译文本以 JSON 数组给出，每项含 id/speaker/text/scene/hint，
    speaker 在独立字段中，模型不会把它当待译文本翻译。
    输出靠 id 对齐，不要求位置对应。
    """
    import json
    structured = []
    for i, item in enumerate(items, 1):
        entry = {"id": i, "text": item.get('original_text', '').replace('\n', ' ')}
        char = item.get('character', '')
        if char:
            entry["speaker"] = char
        if content_type == 'dialogue':
            label = item.get('label', '')
            if label:
                entry["scene"] = label
        # hint 两种类型都带：UI 的出处提示，或重试时注入的纠正指令
        # （标记校验失败原因贴着原文展示，模型按它修正上一版的错误）
        hint = item.get('context_hint', '') or item.get('hint', '')
        if hint:
            entry["hint"] = hint
        structured.append(entry)

    return _render('dialogue.batch_user',
                   context_before_block=_context_block(context_before),
                   items_json=json.dumps(structured, ensure_ascii=False,
                                         indent=None),
                   count=len(items))


# ========== 文本分析 / 风格指南 ==========

def get_analysis_system_prompt() -> str:
    """通用分析 system（analyze_text 固定注入）"""
    return _render('analysis.system')


def build_style_guide_prompt(sample: str) -> str:
    """风格指南生成 prompt"""
    return _render('style_guide', sample=sample)


# ========== 人名翻译 + 人物分析（services/name_translation 使用） ==========

def build_analyze_only_prompt(en_name, lines_text, batch_idx, total_batches):
    return _render('name.analyze_only', en_name=en_name, lines_text=lines_text,
                   batch_idx1=batch_idx + 1, total_batches=total_batches)


def build_translate_analyze_prompt(en_name, lines_text, batch_idx,
                                   total_batches, dict_text):
    return _render('name.translate_analyze', en_name=en_name,
                   lines_text=lines_text, batch_idx1=batch_idx + 1,
                   total_batches=total_batches, dict_block=dict_text or '')


def build_continue_prompt(en_name, lines_text, batch_idx, total_batches,
                          summaries):
    prev = '\n'.join(summaries[-2:]) if summaries else '无'
    return _render('name.continue', en_name=en_name, lines_text=lines_text,
                   batch_idx1=batch_idx + 1, total_batches=total_batches,
                   prev_summaries=prev)


def build_merge_summaries_prompt(name, summaries):
    summaries_text = '\n\n'.join(
        [f'第{i + 1}批分析：\n{s}' for i, s in enumerate(summaries)])
    return _render('name.merge', name=name, summaries_text=summaries_text)
