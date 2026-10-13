"""prompt_store：模板注册表 / 渲染 / 覆盖解析 / 警告

- 渲染语法只有两种标签加注释：[[token]] 纯注入、[[?token]] 可选段落、
  # 注释行（## 除外）发送前剔除
- 解析顺序：项目覆盖 > 全局覆盖 > 代码默认
- 覆盖映射整体换引用（并发读安全）
- warnings_for：缺占位符 / 缺契约锚点 / 未知 token / 旧版条件段残留
"""
import json

import pytest

import prompt_store
from prompt_store import (
    REGISTRY, _is_comment, effective, render, reset_state, save_global,
    set_global_overrides, set_project_overrides, warnings_for,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_state()
    yield
    reset_state()


# ---- 渲染 ----

def test_render_simple_token():
    text = render('name.user', name='Ricardo')
    assert text == '请将以下人名翻译成中文，只返回中文名：\nRicardo'


def test_render_missing_token_leaves_empty():
    """模板保留占位符但调用方没给值 → 注入空串（不炸）"""
    out = render('name.user', name='')
    assert out == '请将以下人名翻译成中文，只返回中文名：\n'


def test_render_conditional_dropped_when_empty():
    """可选段落值空 → 整段（含静态文案）删除"""
    empty = render('dialogue.system')
    full = render('dialogue.system', style_guide='风格指南内容')
    assert '【作品风格指南】' not in empty
    assert '【作品风格指南】\n风格指南内容' in full


def test_render_opt_paragraph():
    """[[?x]] 可选段落：注入值，且值空时整段（含标题）连同相邻空行消失"""
    tpl = '\n'.join([
        '正文',
        '',
        '【作品风格指南】',
        '[[?style_guide]]',
        '',
        '[[?glossary_text]]',
        '',
        '尾部',
    ])
    kept = prompt_store.render_text(
        tpl, {'style_guide': 'S', 'glossary_text': 'G'})
    assert kept == '正文\n\n【作品风格指南】\nS\n\nG\n\n尾部'
    dropped = prompt_store.render_text(tpl, {})
    assert dropped == '正文\n\n尾部'


def test_render_opt_para_eats_one_adjacent_blank():
    """删段只吃一个相邻空行（优先上方）；段在模板开头时吃下方"""
    tpl = '\n'.join(['标题', '[[?x]]', '', '后文'])
    assert prompt_store.render_text(tpl, {}) == '后文'
    assert prompt_store.render_text(tpl, {'x': '1'}) == '标题\n1\n\n后文'
    tpl = '\n'.join(['前文', '', '【块】', '[[?y]]', '', '后文'])
    assert prompt_store.render_text(tpl, {}) == '前文\n\n后文'


def test_render_unknown_token_kept_as_is():
    """用户自定义模板里未知 [[foo]] 原样保留"""
    set_global_overrides({'name.user': 'Hello [[foo]] [[name]]'})
    text = render('name.user', name='X')
    assert text == 'Hello [[foo]] X'


def test_render_comment_lines_stripped():
    """# 注释行发送前剔除；## 开头是正文小标题，保留"""
    tpl = '\n'.join([
        '# 给人看的说明：以下按需注入',
        '正文 [[name]]',
        '## 输出格式（正文小标题，不是注释）',
        '# 又一行注释',
    ])
    out = prompt_store.render_text(tpl, {'name': 'X'})
    assert out == '正文 X\n## 输出格式（正文小标题，不是注释）'


def test_render_legacy_inline_conditional_kept_as_is():
    """旧版 [[#x]]…[[/x]] 已退休：渲染原样保留（warnings_for 会给 legacy 警告）"""
    out = prompt_store.render_text('a[[#x]]b[[/x]]c', {})
    assert out == 'a[[#x]]b[[/x]]c'


def test_render_screener_followups_paragraph_form():
    """预筛两条追问：旧内联条件段迁移为 [[?x]] 段落形态后语义不变"""
    # err_note 空 → 「注意：…」整段消失
    out = render('screener.verdict_partial_followup',
                 accepted_count=1, total_count=5,
                 err_note='', missing_ids=[3])
    assert '注意' not in out
    assert '已受理 1/5 条判决' in out
    assert '还缺 id [3]' in out
    # err_note 非空 → 注入值，整段保留
    out = render('screener.verdict_partial_followup',
                 accepted_count=1, total_count=5,
                 err_note='参数无效', missing_ids=[3])
    assert '注意：上次提交参数有误（参数无效）。' in out
    # missing_ids 空 → 「只需输出…」整段消失
    out = render('screener.round_limit_force', missing_ids='')
    assert '只需输出' not in out
    assert '轮数已达上限' in out
    out = render('screener.round_limit_force', missing_ids=[2])
    assert '只需输出还缺判决的 id: [2]。' in out


def test_render_effective_priority():
    assert effective('name.user')[1] == 'default'
    set_global_overrides({'name.user': 'G [[name]]'})
    assert effective('name.user') == ('G [[name]]', 'global')
    set_project_overrides({'name.user': 'P [[name]]'})
    assert effective('name.user') == ('P [[name]]', 'project')
    # 项目未覆盖的条目回落全局
    set_project_overrides({'analysis.system': 'P'})
    assert effective('name.user') == ('G [[name]]', 'global')


def test_reset_returns_default():
    set_global_overrides({'name.user': 'G'})
    set_project_overrides({'name.user': 'P'})
    reset_state()
    text, source = effective('name.user')
    assert source == 'default'
    assert '请将以下人名翻译成中文' in text


# ---- 解析顺序与不可变换引用 ----

def test_override_swap_is_atomic_readable():
    """整体换引用：并发读侧拿到的永远是完整映射"""
    set_global_overrides({'name.user': 'G1'})
    snap = prompt_store._global_overrides
    set_global_overrides({'name.user': 'G2'})
    assert snap is not prompt_store._global_overrides
    assert snap['name.user'] == 'G1'  # 旧引用不受影响


# ---- 警告 ----

def test_warnings_missing_placeholder():
    w = warnings_for('name.user', '只返回中文名：')  # 删了 [[name]]
    kinds = [x['kind'] for x in w]
    assert 'missing_placeholder' in kinds


def test_warnings_missing_contract_anchor():
    w = warnings_for('dialogue.batch_user',
                     render('dialogue.batch_user', items_json='[]', count=1)
                     .replace('submit_translations', ''))
    assert any(x['kind'] == 'missing_anchor' for x in w)


def test_warnings_unknown_token():
    w = warnings_for('name.user', '[[name]] [[foo]]')
    assert any(x['kind'] == 'unknown_token' for x in w)


def test_warnings_unclosed_conditional():
    """旧版 [[#x]]…[[/x]] 已退休 → legacy_conditional 警告"""
    w = warnings_for('name.user', '[[#name]]abc')
    assert any(x['kind'] == 'legacy_conditional' for x in w)
    w = warnings_for('name.user', 'abc[[/name]]')
    assert any(x['kind'] == 'legacy_conditional' for x in w)


def test_warnings_clean_template():
    """默认模板本身零警告（warnings_for 检查的是模板，不是渲染结果）"""
    w = warnings_for('name.user', REGISTRY['name.user'].default_text)
    assert w == []
    w = warnings_for('dialogue.system', REGISTRY['dialogue.system'].default_text)
    assert w == []
    # 注释里提到占位符不算使用
    w = warnings_for('name.user', '# 说明 [[name]]\n只返回中文名')
    assert any(x['kind'] == 'missing_placeholder' for x in w)


# ---- 持久化 ----

def test_save_load_global(tmp_path):
    path = tmp_path / 'prompts.json'
    set_global_overrides({'name.user': 'G'})
    save_global(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data == {'overrides': {'name.user': 'G'}}
    reset_state()
    prompt_store.load_global(path)
    assert effective('name.user')[1] == 'global'


def test_registry_covers_all_ids():
    """27 条注册完整，字段齐全"""
    assert len(REGISTRY) == 27
    for pid, d in REGISTRY.items():
        assert d.id == pid
        assert d.category and d.name and d.default_text
        assert isinstance(d.placeholders, dict)


# ---- 默认模板契约锚点（防 default_text 被改坏机器解析） ----

def test_default_templates_keep_contract_anchors():
    """每条默认模板必须含其契约锚点（解析器依赖）

    锚点可在正文（含注释外的任何行）或 value_titles（随内容注入的标题）里
    ——与 warnings_for 的判定一致，注释行不算数。
    """
    for pid, d in REGISTRY.items():
        body = '\n'.join(l for l in d.default_text.split('\n')
                         if not _is_comment(l))
        for anchor in d.contract_anchors:
            assert (anchor in body
                    or any(anchor in t for t in d.value_titles.values())), \
                f'{pid} 缺锚点: {anchor}'


def test_default_templates_clean():
    """默认模板自身零警告（占位符齐全、无旧语法残留）"""
    for pid in REGISTRY:
        assert warnings_for(pid, REGISTRY[pid].default_text) == [], pid


def test_value_titles_not_duplicated_in_body():
    """标题随值注入：默认模板正文（注释外）不得再写标题行，否则双重注入"""
    for pid, d in REGISTRY.items():
        body = '\n'.join(l for l in d.default_text.split('\n')
                         if not _is_comment(l))
        for title in d.value_titles.values():
            assert title not in body, f'{pid} 正文重复标题: {title}'


def test_render_injects_value_title_once():
    """渲染输出里标题恰好出现一次（随值注入，不是模板正文）"""
    out = render('dialogue.user', text='Hello', context_before_block='B',
                 context_after_block='A')
    assert out.count('【请翻译以下文本】') == 1
    assert out.count('【前文参考 - 用于理解剧情和翻译风格】') == 1
    assert out.count('【后文参考 - 用于理解剧情走向】') == 1
    out = render('dialogue.batch_user', items_json='[]', count=2)
    assert out.count('【请翻译以下 JSON 数组中每一项的 text 字段，共 2 条】') == 1
