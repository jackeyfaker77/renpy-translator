"""提示词模板注册表：自定义覆盖 + 渲染 + 恢复默认

模板语法只有两种标签加注释：
- [[token]] 纯注入；值空 → 空串；未注册 token 原样保留
- [[?token]] 可选段落：注入值，且标记本自然段可选——值空时整段
  （空行分隔的文本块，含标题等静态文字）连同相邻一个空行一起消失
- # 开头的行是注释（## 除外），发送给模型前自动剔除

生效文本解析顺序：项目覆盖（project_meta['prompt_overrides']）>
全局覆盖（home()/config/prompts.json）> 代码默认（prompt_defaults）。
覆盖映射整体换引用（不原地改），并发线程读侧取一次引用即得一致快照。

调用方约定：build_* 签名不变，内部 prompt_store.render(id, **kw)；
动态数据（术语表/台词/JSON 等）由调用方算好传入占位符，不写进模板。
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from prompt_defaults import DEFAULT_TEXTS


@dataclass(frozen=True)
class PromptDef:
    id: str
    category: str
    name: str
    description: str
    default_text: str
    placeholders: dict = field(default_factory=dict)      # token -> 说明
    value_titles: dict = field(default_factory=dict)      # token -> 随值注入的标题
    contract_anchors: tuple = ()                          # 解析器依赖锚点
    cache_note: str = ''                                  # prompt 缓存契约备注


# 分类顺序即设置页展示顺序
_CATEGORIES = ('对话', 'UI', '人名', '人物分析', '分析', '预筛', '图谱', '导出', '重试')


def _p(pid, category, name, description, placeholders=None,
       titles=None, anchors=(), cache_note=''):
    return PromptDef(
        id=pid, category=category, name=name, description=description,
        default_text=DEFAULT_TEXTS[pid], placeholders=placeholders or {},
        value_titles=titles or {}, contract_anchors=tuple(anchors),
        cache_note=cache_note)


REGISTRY: dict = {
    d.id: d for d in [
        _p('dialogue.system', '对话', '对话翻译 · 系统提示词',
           '单句/对话翻译的角色设定、规则与翻译风格。',
           {'style_guide': '作品风格指南（标题随内容注入，空则整段消失）',
            'glossary_text': '术语表+人名对照表（自动追加，空则不出现）',
            'character_profile': '角色特征（自动追加，空则不出现）'},
           titles={'style_guide': '【作品风格指南】'},
           anchors=('【请翻译以下文本】', '代码标记规则', '术语提取规则')),
        _p('dialogue.user', '对话', '对话翻译 · 用户提示词',
           '单句对话翻译的输入格式与输出协议。',
           {'context_before_block': '前文参考块（标题随内容注入，空则整段消失）',
            'text': '待翻译原文（标题【请翻译以下文本】随内容注入）',
            'character': '说话角色（空则整段消失）',
            'context_after_block': '后文参考块（标题随内容注入，空则整段消失）'},
           titles={'context_before_block': '【前文参考 - 用于理解剧情和翻译风格】',
                   'text': '【请翻译以下文本】',
                   'context_after_block': '【后文参考 - 用于理解剧情走向】'},
           anchors=('【请翻译以下文本】', '【术语】', '【输出格式】')),
        _p('dialogue.batch_user', '对话', '批翻译 · 用户提示词',
           '批量翻译的 JSON 输入格式与提交要求。',
           {'context_before_block': '前文参考块（标题随内容注入，空则整段消失）',
            'items_json': '待译条目 JSON 数组（标题随内容注入）', 'count': '本批条数'},
           titles={'context_before_block': '【前文参考 - 用于理解剧情和翻译风格】',
                   'items_json': '【请翻译以下 JSON 数组中每一项的 text 字段，共 [[count]] 条】'},
           anchors=('submit_translations', 'translations', 'terms'),
           cache_note='批翻译 tool schema（submit_translations 参数结构）不开放编辑，'
                      '修改字段说明时勿偏离 schema'),
        _p('ui.system', 'UI', 'UI 翻译 · 系统提示词',
           '界面文字翻译的角色设定与风格。',
           {'style_guide': '作品风格指南（标题随内容注入，空则整段消失）',
            'glossary_text': '术语表+人名对照表（自动追加，空则不出现）'},
           titles={'style_guide': '【作品风格指南】'},
           anchors=('代码标记规则', '术语提取规则')),
        _p('ui.user', 'UI', 'UI 翻译 · 用户提示词',
           '界面文字翻译的输入格式与输出协议。',
           {'text': '待翻译原文'}, anchors=('【术语】',)),
        _p('name.system', '人名', '人名翻译 · 系统提示词',
           '人名音译/意译规则。',
           {'glossary_text': '术语表+人名对照表（自动追加，空则不出现）'},
           anchors=('只返回中文名',)),
        _p('name.user', '人名', '人名翻译 · 用户提示词',
           '单个人名的翻译请求。', {'name': '待翻译人名'}),
        _p('name.analyze_only', '人物分析', '角色分析 · 仅分析',
           '仅分析角色特征（不译名）。',
           {'en_name': '角色名', 'lines_text': '台词内容',
            'batch_idx1': '当前批次号（从 1 起）', 'total_batches': '总批次'},
           anchors=('性格特点', '说话风格', '翻译建议')),
        _p('name.translate_analyze', '人物分析', '角色分析 · 翻译+分析',
           '人名翻译与角色分析一次完成（主入口）。',
           {'en_name': '角色名', 'lines_text': '台词内容',
            'batch_idx1': '当前批次号（从 1 起）', 'total_batches': '总批次',
            'dict_block': '已有人名对照（标题随内容注入，空则整段消失）'},
           titles={'dict_block': '已有的人名翻译（供参考）：'},
           anchors=('【人名翻译】', '中文名', '【人物分析】', '翻译建议')),
        _p('name.continue', '人物分析', '角色分析 · 续段补充',
           '多批台词时后续批次的补充分析。',
           {'en_name': '角色名', 'lines_text': '台词内容',
            'batch_idx1': '当前批次号（从 1 起）', 'total_batches': '总批次',
            'prev_summaries': '前几批分析摘要'}),
        _p('name.merge', '人物分析', '角色分析 · 多段合并',
           '把多批分析合并为完整人物报告。',
           {'name': '角色名', 'summaries_text': '各批分析摘要'},
           anchors=('性格特点', '翻译建议')),
        _p('analysis.system', '分析', '通用分析 · 系统提示词',
           '所有 analyze_text 调用共用的系统提示词（角色/关系/场景/自愈）。'),
        _p('style_guide', '分析', '作品风格指南 · 生成提示词',
           '从台词抽样分析并生成风格指南。',
           {'sample': '台词抽样'}, anchors=('【题材基调】', '【翻译基调建议】')),
        _p('screener.coarse_a', '预筛', '内嵌预筛 · 粗筛变体 A',
           '内嵌字符串翻译价值粗筛（投票变体 1/3）。',
           {'items_json': '候选 JSON 数组'},
           anchors=('keep', 'reason', 'id'),
           cache_note='三变体须保持互异（票间去相关依赖措辞差异）'),
        _p('screener.coarse_b', '预筛', '内嵌预筛 · 粗筛变体 B',
           '内嵌字符串翻译价值粗筛（投票变体 2/3）。',
           {'items_json': '候选 JSON 数组'},
           anchors=('keep', 'reason', 'id'),
           cache_note='三变体须保持互异（票间去相关依赖措辞差异）'),
        _p('screener.coarse_c', '预筛', '内嵌预筛 · 粗筛变体 C',
           '内嵌字符串翻译价值粗筛（投票变体 3/3）。',
           {'items_json': '候选 JSON 数组'},
           anchors=('keep', 'reason', 'id'),
           cache_note='三变体须保持互异（票间去相关依赖措辞差异）'),
        _p('screener.refine_system', '预筛', '内嵌预筛 · 精审系统提示词',
           'agentic 精审的判定标准与工具使用纪律。',
           anchors=('submit_verdicts', 'keep', 'evidence')),
        _p('screener.anchor_section', '预筛', '内嵌预筛 · 灰区复核锚定段',
           '灰区复核模式追加段（维持原判语义）。',
           anchors=('维持原判',)),
        _p('screener.verdict_partial_followup', '预筛', '内嵌预筛 · 判决补齐追问',
           'submit_verdicts 提交不全时的追问。',
           {'accepted_count': '已受理条数', 'total_count': '本批总数',
            'err_note': '提交错误说明（空则整段消失）',
            'missing_ids': '缺失 id 列表'},
           anchors=('submit_verdicts',)),
        _p('screener.verdict_text_followup', '预筛', '内嵌预筛 · 文本兜底追问',
           '模型返回文本而非工具调用时的追问。',
           {'missing_ids': '缺失 id 列表'}, anchors=('submit_verdicts',)),
        _p('screener.round_limit_force', '预筛', '内嵌预筛 · 轮数上限强制提交',
           '精审轮数耗尽时要求立即输出判决。',
           {'missing_ids': '缺失 id 列表（空则整段消失）'}),
        _p('relation.prompt', '图谱', '关系推断 · 提示词',
           '从角色清单推断人物关系与阵营。',
           {'game': '游戏名', 'chars_json': '角色清单 JSON'},
           anchors=('relations', 'factions', 'source', 'target', 'relation',
                    'category', 'polarity')),
        _p('relation.head', '图谱', '关系推断 · 花名册前缀提示词',
           '大批量时的缓存优化版（花名册常量前缀 + 本批名字）。',
           {'game': '游戏名', 'roster_json': '完整角色清单 JSON',
            'batch_names_json': '本批角色名 JSON'},
           anchors=('relations', 'factions', 'source', 'target', 'relation',
                    'category', 'polarity'),
           cache_note='缓存契约：[[roster_json]] 须留在前缀位置、'
                      '[[batch_names_json]] 须留在末尾，打乱会断 prompt 前缀缓存'),
        _p('scene.summary', '图谱', '场景摘要 · 提示词',
           '为剧情场景生成标题与梗概。',
           {'scenes_json': '场景清单 JSON'}, anchors=('title', 'summary', 'id')),
        _p('export.fix', '导出', '导出修复 · 提示词',
           '导出后编译错误的译文修复。',
           {'err_file': '出错文件', 'err_line': '出错行号', 'err_msg': '错误信息',
            'original': '条目原文', 'current': '当前译文', 'context': '周边代码'},
           anchors=('action', 'fix', 'blank', 'text')),
        _p('retry.must_fix', '重试', '标记校验重试 · 纠正指令',
           '单句/UI 重译时附加的纠正指令。',
           {'problems': '校验问题列表（；分隔）'}),
        _p('retry.batch_hint', '重试', '批翻译重试 · 纠正 hint',
           '批翻译重试时写入条目 hint 字段的纠正指令。',
           {'reason': '校验失败原因'}),
    ]
}

# ---- 覆盖层（不可变映射整体换引用；仅模块内单例替换） ----
_global_overrides: dict = {}
_project_overrides: dict = {}


def reset_state():
    """清空两层覆盖（测试与全部恢复默认用）"""
    global _global_overrides, _project_overrides
    _global_overrides = {}
    _project_overrides = {}


def set_global_overrides(overrides: dict):
    global _global_overrides
    _global_overrides = dict(overrides or {})


def set_project_overrides(overrides: dict):
    global _project_overrides
    _project_overrides = dict(overrides or {})


def clear_project_overrides():
    set_project_overrides({})


def get_global_overrides() -> dict:
    return dict(_global_overrides)


def get_project_overrides() -> dict:
    return dict(_project_overrides)


def effective(pid: str):
    """生效模板与来源：project > global > default"""
    proj, glob = _project_overrides, _global_overrides
    if pid in proj:
        return proj[pid], 'project'
    if pid in glob:
        return glob[pid], 'global'
    return REGISTRY[pid].default_text, 'default'


# ---- 渲染 ----
#
# 语法只有两种标签加注释：
# - [[token]] 纯注入；值空 → 空串；未注册 token 原样保留
# - [[?token]] 可选段落（推荐）：同时是注入点，且标记「本自然段可选」——
#   值空时整个段落（空行分隔的文本块，含标题等静态文字）连同相邻一个
#   空行一起消失（优先吃上方的空行；在模板开头则吃下方的）
# - # 开头的行是注释（## 除外——## 是正文小标题），发送给模型前剔除
#
# 换行就是模板里的普通换行，标签不管排版。

_TOKEN_RE = re.compile(r'\[\[([a-z0-9_]+)\]\]')
_OPT_RE = re.compile(r'\[\[\?([a-z0-9_]+)\]\]')


def _is_comment(line: str) -> bool:
    s = line.lstrip()
    return s.startswith('#') and not s.startswith('##')


def render(pid: str, **values) -> str:
    """渲染生效模板：注释剔除 → 可选段落（[[?x]]）→ 简单占位符。

    内容块标题随标签注入（value_titles）：值非空时标题拼进值一起发，
    模板正文不写标题行——内容为空标题自然跟着不发。

    未注册/未提供的 token 原样保留（用户模板里的笔误会原样发给模型，
    保存时由 warnings_for 提示）。
    """
    text, _ = effective(pid)
    titles = REGISTRY[pid].value_titles
    if titles:
        values = dict(values)
        for token, title in titles.items():
            v = values.get(token)
            if not v:
                continue
            resolved = _TOKEN_RE.sub(
                lambda m: str(values[m.group(1)])
                if m.group(1) in values else m.group(0), title)
            values[token] = f'{resolved}\n{v}'
    return render_text(text, values)


def render_text(template: str, values: dict) -> str:
    # 0) 注释行剔除（# 开头、## 除外）
    lines = [l for l in template.split('\n') if not _is_comment(l)]

    # 1) 分段（空行分隔）并删除可选段落
    items = []   # ('para', [行]) | ('blank', [空行])
    buf = []
    for line in lines:
        if line == '':
            if buf:
                items.append(['para', buf])
                buf = []
            if items and items[-1][0] == 'blank':
                items[-1][1].append(line)
            else:
                items.append(['blank', [line]])
        else:
            buf.append(line)
    if buf:
        items.append(['para', buf])

    drop = set()
    for idx, (kind, pls) in enumerate(items):
        if kind != 'para':
            continue
        opts = [m.group(1) for pl in pls for m in _OPT_RE.finditer(pl)]
        if opts and not any(values.get(t) for t in opts):
            drop.add(idx)

    eaten = set()   # 随删段一起消失的相邻空行
    for idx in sorted(drop):
        if idx - 1 >= 0 and items[idx - 1][0] == 'blank' and (idx - 1) not in eaten:
            eaten.add(idx - 1)
        elif (idx + 1 < len(items) and items[idx + 1][0] == 'blank'
              and (idx + 1) not in eaten):
            eaten.add(idx + 1)

    out_lines = []
    for idx, (kind, pls) in enumerate(items):
        if idx in drop or idx in eaten:
            continue
        out_lines.extend(pls)
    text = '\n'.join(out_lines)

    # 2) 占位符（[[token]] 与 [[?token]] 都是注入点）
    def _tok(m):
        key = m.group(1)
        if key not in values:
            return m.group(0)
        v = values[key]
        return '' if v is None else str(v)
    text = _TOKEN_RE.sub(_tok, text)
    return _OPT_RE.sub(_tok, text)


# ---- 保存校验（警告不阻断） ----

def warnings_for(pid: str, text: str) -> list:
    """检查自定义模板，返回 [{kind, message}]。

    kind: missing_placeholder / missing_anchor / unknown_token /
    legacy_conditional
    """
    d = REGISTRY[pid]
    # 注释行不参与检查（发送前会剔除；注释里提一嘴占位符不算数）
    body = '\n'.join(l for l in text.split('\n') if not _is_comment(l))
    warns = []
    for token, desc in d.placeholders.items():
        if f'[[{token}]]' not in body and f'[[?{token}]]' not in body:
            warns.append({'kind': 'missing_placeholder',
                          'message': f'缺少占位符 [[{token}]]（{desc}），'
                                     '该内容将不会注入'})
    for anchor in d.contract_anchors:
        if anchor not in body and not any(
                anchor in t for t in d.value_titles.values()):
            warns.append({'kind': 'missing_anchor',
                          'message': f'缺少「{anchor}」——输出解析依赖此锚点，'
                                     '缺失可能导致解析失败'})
    for m in _TOKEN_RE.finditer(body):
        token = m.group(1)
        if token not in d.placeholders:
            warns.append({'kind': 'unknown_token',
                          'message': f'未知占位符 [[{token}]]，将原样发送给模型'})
    for m in re.finditer(r'\[\[\?([a-z0-9_]+)\]\]', body):
        token = m.group(1)
        if token not in d.placeholders:
            warns.append({'kind': 'unknown_token',
                          'message': f'未知可选段落 [[?{token}]]，将原样发送给模型'})
    if re.search(r'\[\[#|\[\[/', body):
        warns.append({'kind': 'legacy_conditional',
                      'message': '模板含旧版条件段 [[#x]]…[[/x]]，已停用——请改用 '
                                 '[[?x]] 可选段落标记（值空时整段消失），'
                                 '否则该标记会原样发送给模型'})
    return warns


# ---- 持久化（全局层） ----

def load_global(path):
    """从 home()/config/prompts.json 读全局覆盖；文件不存在视为无覆盖"""
    path = Path(path)
    if not path.exists():
        set_global_overrides({})
        return
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        set_global_overrides(data.get('overrides') or {})
    except (OSError, ValueError):
        set_global_overrides({})


def save_global(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({'overrides': get_global_overrides()},
                   ensure_ascii=False, indent=2),
        encoding='utf-8')
