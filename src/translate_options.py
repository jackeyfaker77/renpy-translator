"""翻译模式选项：深度/轻量/纯批量三档预设 + 细粒度开关

存 project_meta['translate_options']（JSON 字符串），缺省 = deep 全开 =
现状行为零回归。选预设写全量 flags；手动微调任一开关 → preset='custom'。

设计约束：
- 可关段都是提示词尾部的 [[?token]] 可选段，装配处传空串即整段消失
  （prompt_store.render_text），不要挪到模板前部——会打断前缀缓存；
- term_extract 是例外（规则段是模板静态正文）：关闭=落库侧丢弃 terms，
  不动默认模板（golden 不破）。
"""
import json
from dataclasses import dataclass

PROFILE_MODES = ('full', 'advice_only', 'off')
FLAG_KEYS = ('term_extract', 'glossary', 'style_guide', 'character_profile', 'context')

PRESETS = {
    # 深度：全效果（现状默认）——人物特征/术语/风格/上下文齐上
    'deep': {'term_extract': True, 'glossary': True, 'style_guide': True,
             'character_profile': 'full', 'context': True},
    # 轻量：低成本高收益项——留术语表（短静态、缓存友好、防人名漂移）
    # 与「翻译建议」（档案里对译法约束最强、又便宜的一段）
    'light': {'term_extract': False, 'glossary': True, 'style_guide': False,
              'character_profile': 'advice_only', 'context': False},
    # 纯批量：无附加效果，最快最省
    'bulk': {'term_extract': False, 'glossary': False, 'style_guide': False,
             'character_profile': 'off', 'context': False},
}

PRESET_LABELS = {'deep': '深度', 'light': '轻量', 'bulk': '纯批量', 'custom': '自定义'}


@dataclass
class TranslateOptions:
    preset: str = 'deep'
    term_extract: bool = True
    glossary: bool = True
    style_guide: bool = True
    character_profile: str = 'full'
    context: bool = True

    def flags(self) -> dict:
        return {
            'term_extract': self.term_extract,
            'glossary': self.glossary,
            'style_guide': self.style_guide,
            'character_profile': self.character_profile,
            'context': self.context,
        }

    def to_json(self) -> str:
        return json.dumps({'preset': self.preset, 'flags': self.flags()},
                          ensure_ascii=False)


def options_from_preset(name: str) -> TranslateOptions:
    flags = PRESETS.get(name, PRESETS['deep'])
    preset = name if name in PRESETS else 'deep'
    return TranslateOptions(preset=preset, **flags)


def parse_options(raw) -> TranslateOptions:
    """project_meta 值 → TranslateOptions；坏数据/缺省一律回退 deep"""
    if not raw:
        return options_from_preset('deep')
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        return options_from_preset('deep')
    if not isinstance(data, dict):
        return options_from_preset('deep')

    preset = data.get('preset') or 'deep'
    flags_src = data.get('flags') or PRESETS.get(preset, PRESETS['deep'])
    flags = dict(PRESETS['deep'])
    if isinstance(flags_src, dict):
        for k in FLAG_KEYS:
            if k in flags_src:
                flags[k] = flags_src[k]
    if flags['character_profile'] not in PROFILE_MODES:
        flags['character_profile'] = 'full'
    for k in ('term_extract', 'glossary', 'style_guide', 'context'):
        flags[k] = bool(flags[k])
    if preset not in PRESET_LABELS:
        preset = 'custom'
    return TranslateOptions(preset=preset, **flags)


def validate_flags(flags: dict) -> dict:
    """API 入参校验：枚举外/类型错抛 ValueError"""
    out = {}
    for k in FLAG_KEYS:
        if k not in flags:
            continue
        v = flags[k]
        if k == 'character_profile':
            if v not in PROFILE_MODES:
                raise ValueError('character_profile 只能是 full/advice_only/off')
            out[k] = v
        else:
            if not isinstance(v, bool):
                raise ValueError(f'{k} 必须是布尔值')
            out[k] = v
    return out
