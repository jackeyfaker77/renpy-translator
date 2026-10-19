"""精选工具集（默认模式）：30 个高层工具覆盖全部功能域。

统一经 bridge.api_call 走 /api/*；长任务 start_* 返回 {job_id}，
配套 wait_job / answer_job / cancel_job。危险工具需 confirm=true。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import FastAPI
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .bridge import ApiBridgeError, api_call, public_base_url

ContentTypes = Literal['dialogue', 'ui']

TERMINAL = ('succeeded', 'failed', 'cancelled', 'interrupted')


def _require_confirm(confirm: bool, action_desc: str) -> None:
    if not confirm:
        raise ToolError(
            f'这是危险操作：{action_desc}。确认要执行请重试并传 confirm=true。')


async def _call(app: FastAPI, method: str, path: str, *,
                params: dict | None = None, body: Any = None) -> Any:
    try:
        data = await api_call(app, method, path, params=params, json_body=body)
    except ApiBridgeError as e:
        msg = f'[{e.code}] {e.message}'
        if e.detail:
            msg += f'（{e.detail[:300]}）'
        raise ToolError(msg) from e
    # list 会被 SDK 拆成多条 TextContent（客户端只读 content[0] 会丢数据）
    # 统一包一层，保证 Agent 一次拿到完整 JSON
    if isinstance(data, list):
        return {'items': data}
    return data


def _pkg_abs(state: Any, file_name: str) -> str:
    """导出产物绝对路径（同机 agent 直读；与 projects._exports_dir 同布局）"""
    from pathlib import Path
    try:
        safe = ''.join(c for c in state.current_project
                       if c.isalnum() or c in '._- ').strip()
        return str(Path(state.root) / 'exports' / safe / file_name)
    except Exception:
        return ''


def register_curated(mcp: MCPServer, app: FastAPI) -> None:
    """把精选工具注册到 MCPServer（@mcp.tool 装饰器）"""

    # ---- 会话 / 项目 ----

    @mcp.tool(description='获取当前会话概览：当前项目、是否有翻译器、统计、活跃任务数。')
    async def get_session() -> dict:
        return await _call(app, 'GET', '/api/session')

    @mcp.tool(description='列出全部项目（名称、备注、统计等）。')
    async def list_projects() -> dict:
        return await _call(app, 'GET', '/api/projects')

    @mcp.tool(description='打开/切换当前项目。有活跃任务时会失败（先等任务结束）。')
    async def open_project(name: str) -> dict:
        return await _call(app, 'POST', '/api/session/open', body={'name': name})

    @mcp.tool(description='从游戏目录创建新项目（长任务）。返回 {job_id}，用 wait_job 等待；'
                          '期间可能进入 waiting_input（官中确认问答），用 answer_job 回答。')
    async def create_project(name: str, game_dir: str) -> dict:
        return await _call(app, 'POST', '/api/projects/create',
                           body={'name': name, 'game_dir': game_dir})

    @mcp.tool(description='删除项目（长任务，危险）。必须传 confirm=true。返回 {job_id}。')
    async def delete_project(name: str, confirm: bool = False) -> dict:
        _require_confirm(confirm, f'永久删除项目「{name}」及其全部译文数据')
        return await _call(app, 'DELETE', f'/api/projects/{name}')

    # ---- 文本 ----

    @mcp.tool(description='分页浏览对话/UI 文本条目。content_type=dialogue|ui；'
                          'filter_mode: all/untranslated/translated/failed 等；'
                          'page 从 0 开始。')
    async def list_texts(content_type: ContentTypes, page: int = 0, size: int = 50,
                         filter_mode: str = 'all', search: str = '',
                         character: str = '') -> dict:
        return await _call(app, 'GET', f'/api/current/texts/{content_type}',
                           params={'page': page, 'size': size,
                                   'filter_mode': filter_mode,
                                   'search': search, 'character': character})

    @mcp.tool(description='单条即时翻译并写回（同步，返回译文）。')
    async def translate_text(content_type: ContentTypes, item_id: str) -> dict:
        return await _call(
            app, 'POST', f'/api/current/texts/{content_type}/{item_id}/translate')

    @mcp.tool(description='批量翻译（长任务）：filter_mode=all 全量，或配合 search/character '
                          '筛选。返回 {job_id}，用 wait_job 等待。')
    async def start_translate_texts(content_type: ContentTypes,
                                    filter_mode: str = 'all', search: str = '',
                                    character: str = '') -> dict:
        return await _call(app, 'POST',
                           f'/api/current/texts/{content_type}/translate-all',
                           body={'filter_mode': filter_mode, 'search': search,
                                 'character': character})

    @mcp.tool(description='改写某条译文。')
    async def update_text(content_type: ContentTypes, item_id: str,
                          translated_text: str) -> dict:
        return await _call(app, 'PATCH',
                           f'/api/current/texts/{content_type}/{item_id}',
                           body={'translated_text': translated_text})

    @mcp.tool(description='失败暂存条目拆小批重试（长任务）。返回 {job_id}。')
    async def retry_failed_texts(content_type: ContentTypes) -> dict:
        return await _call(
            app, 'POST', f'/api/current/texts/{content_type}/failed-batches/retry')

    # ---- 人名 ----

    @mcp.tool(description='全部角色人名与译名、翻译/分析状态统计。')
    async def list_names() -> dict:
        return await _call(app, 'GET', '/api/current/names')

    @mcp.tool(description='人名翻译/角色分析批量任务（长任务）。'
                          'mode: both|name|analyze。返回 {job_id}。')
    async def start_translate_names(
            mode: Literal['both', 'name', 'analyze'] = 'both') -> dict:
        return await _call(app, 'POST', '/api/current/names/translate-all',
                           body={'mode': mode})

    # ---- 术语表 ----

    @mcp.tool(description='术语表分页查询（page 从 0 开始）。')
    async def list_glossary(page: int = 0, size: int = 50,
                            search: str = '') -> dict:
        return await _call(app, 'GET', '/api/current/glossary',
                           params={'page': page, 'size': size, 'search': search})

    @mcp.tool(description='新增术语条目。term_type: term|ui（ui 只注入 UI 分节）。')
    async def add_glossary_term(en_term: str, cn_term: str,
                                term_type: Literal['term', 'ui'] = 'term') -> dict:
        return await _call(app, 'POST', '/api/current/glossary',
                           body={'en_term': en_term, 'cn_term': cn_term,
                                 'term_type': term_type})

    # ---- 内嵌预筛 ----

    @mcp.tool(description='内嵌文本提取全管线（长任务，会进入 waiting_input 请求复核）。'
                          '返回 {job_id}；waiting_input 时用 answer_job 回答 '
                          'embedded_review 问题（{action?, chosen_ids, row_ids}）。')
    async def start_embedded_scan() -> dict:
        return await _call(app, 'POST', '/api/current/embedded/scan')

    @mcp.tool(description='内嵌单句 AI 精判（同步）。')
    async def embedded_refine(row_id: str) -> dict:
        return await _call(app, 'POST', f'/api/current/embedded/refine/{row_id}')

    # ---- 图谱 ----

    @mcp.tool(description='构建剧情图或关系图（长任务）。kind: story|relations。返回 {job_id}。')
    async def start_graph_build(
            kind: Literal['story', 'relations'],
            incremental: bool = False) -> dict:
        if kind == 'story':
            return await _call(app, 'POST', '/api/current/graph/story/build',
                               body={'incremental': incremental})
        return await _call(app, 'POST', '/api/current/graph/relations/build')

    @mcp.tool(description='人物关系与边列表。')
    async def get_relations() -> dict:
        return await _call(app, 'GET', '/api/current/graph/relations')

    # ---- 导出 / 导入 ----

    @mcp.tool(description='导出成品游戏 zip（长任务）。返回 {job_id}；'
                          'wait_job 成功后 result 含产物信息（path/download_url）。')
    async def start_export_game(shell: bool = False) -> dict:
        return await _call(app, 'POST', '/api/current/export/game',
                           body={'shell': shell})

    @mcp.tool(description='导出译文为 txt/json/xlsx/docx（同步）。'
                          'types: dialogue/ui/names/glossary；返回 file、path（绝对路径）、'
                          'download_url（绝对 URL）、counts。')
    async def export_texts(
            types: list[str], format: Literal[
                'txt', 'json', 'xlsx', 'docx', 'tword'] = 'xlsx',
            filename: str = '') -> dict:
        data = await _call(app, 'POST', '/api/current/export/texts',
                           body={'types': types, 'format': format,
                                 'filename': filename})
        if isinstance(data, dict) and data.get('file'):
            state = app.state.app_state
            base = public_base_url(app)
            data['path'] = _pkg_abs(state, data['file'])
            data['download_url'] = base + data.get('download_url', '')
        return data

    @mcp.tool(description='从其它项目导入译文（预览+落库一步完成）。'
                          'apply=direct_only 只导无冲突条目；apply=all 连冲突一起覆盖'
                          '（危险，需 confirm=true）。')
    async def import_texts_from_project(
            source_project: str, apply: Literal['direct_only', 'all'] = 'direct_only',
            confirm: bool = False) -> dict:
        types = ['dialogue', 'ui']
        preview = await _call(app, 'POST', '/api/current/import/from-project/preview',
                              body={'source_project': source_project, 'types': types})
        digest = preview.get('digest', '')
        if apply == 'all':
            _require_confirm(confirm,
                             f'从「{source_project}」导入并覆盖本项目冲突条目')
            rows = (preview.get('direct') or []) + (preview.get('conflict') or []) \
                + (preview.get('review') or [])
        else:
            rows = preview.get('direct') or []
        selections = [{'type': r['type'], 'key': r['key']} for r in rows]
        result = await _call(
            app, 'POST', '/api/current/import/from-project/apply',
            body={'source_project': source_project, 'types': types,
                  'expected_digest': digest, 'selections': selections})
        return {'preview_summary': {
            'direct': len(preview.get('direct') or []),
            'conflict': len(preview.get('conflict') or []),
            'review': len(preview.get('review') or []),
            'same': preview.get('same', 0),
        }, 'apply': apply, 'result': result}

    # ---- 定时任务 ----

    @mcp.tool(description='定时任务列表（含 next_run_at 与上次状态）。')
    async def list_scheduled_tasks() -> dict:
        return await _call(app, 'GET', '/api/scheduled')

    @mcp.tool(description='立即执行一次指定定时任务（不改变计划）。返回 {job_id}。')
    async def run_scheduled_task(task_id: str) -> dict:
        return await _call(app, 'POST', f'/api/scheduled/{task_id}/run-now')

    # ---- 任务中心 ----

    @mcp.tool(description=(
        '等待任务至终态/超时/待输入（默认最多等 60 秒，timeout_seconds=0 只取快照）。'
        '返回：终态 → {status, result, error, recent_logs}；'
        'waiting_input → {status, question:{question_id,type,payload}}，'
        '调用 answer_job 回答后再 wait_job；'
        '超时 → {timed_out:true, progress, stage}，可再次调用续等。'
        'question.type=confirm 时答案 {action: keep|delete|cancel}；'
        'embedded_review 时 {action?: recheck|refine_all|cancel, chosen_ids, row_ids}。'))
    async def wait_job(job_id: str, timeout_seconds: float = 60) -> dict:
        state = app.state.app_state
        deadline = time.monotonic() + max(timeout_seconds, 0)
        last_seq = 0
        logs: list[dict] = []
        progress: dict = {}
        while True:
            try:
                job = await _call(app, 'GET', f'/api/jobs/{job_id}')
            except ToolError:
                raise
            # 事件尾（直接读库，绕开 SSE）
            try:
                events = state.app_db.get_events(job_id, after_seq=last_seq)
                for ev in events:
                    last_seq = max(last_seq, ev['seq'])
                    if ev['kind'] == 'log':
                        logs.append({'seq': ev['seq'], **(ev.get('data') or {})})
                    elif ev['kind'] in ('progress', 'stage'):
                        progress = {**progress, **(ev.get('data') or {}),
                                    'kind': ev['kind']}
            except Exception:
                events = []
            tail = logs[-20:]
            status = job.get('status')
            if status in TERMINAL:
                return {'status': status, 'result': job.get('result'),
                        'error': job.get('error'), 'progress': job.get('progress'),
                        'stage': job.get('stage'), 'recent_logs': tail}
            if status == 'waiting_input':
                return {'status': status, 'question': job.get('question'),
                        'hint': '调用 answer_job 回答后再次 wait_job',
                        'recent_logs': tail}
            if time.monotonic() >= deadline:
                return {'status': status, 'timed_out': True,
                        'progress': job.get('progress'), 'stage': job.get('stage'),
                        'recent_logs': tail,
                        'hint': '再次调用 wait_job 可继续等待'}
            await asyncio.sleep(0.4)

    @mcp.tool(description='回答任务的 ask 问题（question_id 来自 wait_job 的 question）。'
                          'confirm: {action: keep|delete|cancel}；'
                          'embedded_review: {action?, chosen_ids, row_ids}。')
    async def answer_job(job_id: str, question_id: str,
                         answer: dict) -> dict:
        return await _call(app, 'POST', f'/api/jobs/{job_id}/answer',
                           body={'question_id': question_id, 'answer': answer})

    @mcp.tool(description='取消运行中的任务（协作式，在飞请求跑完才停）。')
    async def cancel_job(job_id: str) -> dict:
        return await _call(app, 'POST', f'/api/jobs/{job_id}/cancel')

    # ---- 诊断 / 设置 ----

    @mcp.tool(description='服务日志与 LLM API 轨迹（若开启 api_log）。'
                          'panel 可选；task_type 可过滤轨迹类型。')
    async def get_logs(limit: int = 50, panel: str = '',
                       task_type: str = '') -> dict:
        text_log = await _call(app, 'GET', '/api/logs',
                               params={'limit': limit, 'panel': panel})
        api_log: Any = None
        try:
            api_log = await _call(app, 'GET', '/api/logs/api',
                                  params={'limit': min(limit, 200),
                                          'task_type': task_type})
        except ToolError:
            api_log = None
        return {'log': text_log, 'api_log': api_log}

    @mcp.tool(description='模型配置列表（api_key 已掩码，含激活标记）。')
    async def list_model_configs() -> dict:
        return await _call(app, 'GET', '/api/configs')

    @mcp.tool(description='全部提示词模板与生效来源（global/project 覆盖）。')
    async def list_prompts() -> dict:
        return await _call(app, 'GET', '/api/prompts')

    @mcp.tool(description='保存单条提示词覆盖。scope: global|project。'
                          '返回 {ok, warnings}。')
    async def save_prompt(pid: str, text: str,
                          scope: Literal['global', 'project'] = 'project') -> dict:
        return await _call(app, 'PUT', f'/api/prompts/{pid}',
                           body={'scope': scope, 'text': text})
