"""翻译文本导入 API：JSON / 翻译Word 回导译文

单步导入：上传文件 → 解析生成计划 → 直接写库，返回各类型统计。
- JSON（本工具导出格式）：按原文匹配，顺序无关
- 翻译Word（仅原文每条一段）：按段落顺序对齐，段落数不一致直接报错
"""
import json
from pathlib import Path

from pydantic import BaseModel

from fastapi import APIRouter, Depends, File, Form, UploadFile

from ..deps import require_project
from ..errors import ApiError
from ..state import AppState

router = APIRouter(prefix='/current/import', tags=['import'])

_MAX_UPLOAD = 64 * 1024 * 1024


@router.post('/texts')
async def import_texts(file: UploadFile = File(...),
                       types: str = Form(''),
                       filter: str = Form(''),
                       state: AppState = Depends(require_project)):
    from services import text_import

    ext = Path(file.filename or '').suffix.lower()
    if ext not in ('.json', '.docx'):
        raise ApiError(400, 'BAD_FILE', '仅支持导入 .json 或翻译 .docx 文件')
    content = await file.read()
    if len(content) > _MAX_UPLOAD:
        raise ApiError(400, 'TOO_LARGE', '文件超过 64MB')

    sel_types = json.loads(types) if types else []
    flt = json.loads(filter) if filter else None
    if ext == '.docx' and not sel_types:
        raise ApiError(400, 'BAD_TYPES',
                       '翻译 Word 按段落顺序对齐导入，请选择与导出时一致的内容类型')

    try:
        if ext == '.json':
            pairs = text_import.parse_json_import(content.decode('utf-8-sig'))
            plan, stats = await state.db_call(
                text_import.build_json_plan, state.db, pairs)
        else:
            lines = await state.run_sync(text_import.read_tword_lines, content)
            plan, stats = await state.db_call(
                text_import.build_tword_plan, state.db, lines, sel_types, flt)
    except ValueError as e:
        raise ApiError(400, 'BAD_IMPORT', str(e)) from e

    counts = await state.db_call(text_import.apply_plan, state.db, plan)
    return {'counts': counts, 'total': len(plan), 'stats': stats}


# ---- 跨项目导入（从其他项目复用已翻译文段，冲突逐条对比）----

class FromProjectIn(BaseModel):
    source_project: str
    types: list[str] = ['dialogue', 'ui']


class ApplyFromProjectIn(FromProjectIn):
    expected_digest: str = ''
    selections: list[dict] = []  # [{type, key}]：用户选择「用源」的条目


def _load_source_pairs(state: AppState, source_project: str, types: list):
    from services import text_import
    if source_project == state.current_project:
        raise ApiError(400, 'SAME_PROJECT', '不能从当前项目导入')
    if not state.project_manager.project_exists(source_project):
        raise ApiError(404, 'NOT_FOUND', f'项目不存在: {source_project}')
    src = state.project_manager.open_project(source_project)
    if not src:
        raise ApiError(404, 'NOT_FOUND', f'项目不存在: {source_project}')
    try:
        return text_import.read_translated_pairs(src, types)
    finally:
        src.close()


@router.get('/from-projects')
async def list_source_projects(state: AppState = Depends(require_project)):
    """可选的源项目（不含当前项目）"""
    projects = await state.run_sync(state.project_manager.list_projects)
    return {'projects': [
        {'name': p.name} for p in projects
        if getattr(p, 'name', '') != state.current_project
    ]}


@router.post('/from-project/preview')
async def preview_from_project(req: FromProjectIn,
                               state: AppState = Depends(require_project)):
    """分组预览：direct/conflict/review + same 计数 + digest"""
    from services import text_import

    def _work():
        pairs = _load_source_pairs(state, req.source_project, req.types)
        return text_import.build_cross_project_plan(state.db, pairs, req.types)

    try:
        return await state.run_sync(_work)
    except ValueError as e:
        raise ApiError(400, 'BAD_IMPORT', str(e)) from e


@router.post('/from-project/apply')
async def apply_from_project(req: ApplyFromProjectIn,
                             state: AppState = Depends(require_project)):
    """按逐行选择落库：重建计划比对 digest（防预览后数据变化），
    只写用户选「用源」的条目，译文取源项目值（不信任客户端回传文本）"""
    from services import text_import

    def _work():
        pairs = _load_source_pairs(state, req.source_project, req.types)
        rebuilt = text_import.build_cross_project_plan(state.db, pairs, req.types)
        if not req.expected_digest or rebuilt['digest'] != req.expected_digest:
            raise ApiError(409, 'IMPORT_CONFLICT',
                           '预览后项目数据已变化，请重新预览再导入')
        incoming = {(r['type'], r['key']): r['incoming'] for r in
                    rebuilt['direct'] + rebuilt['conflict'] + rebuilt['review']}
        plan = []
        for sel in req.selections:
            key = (sel.get('type'), sel.get('key'))
            if key in incoming:
                plan.append({'type': sel['type'], 'key': sel['key'],
                             'translated': incoming[key]})
        return text_import.apply_plan(state.db, plan)

    try:
        counts = await state.run_sync(_work)
    except ValueError as e:
        raise ApiError(400, 'BAD_IMPORT', str(e)) from e
    return {'counts': counts}
