"""提示词设置 API：列表 / 单条保存 / 恢复默认 / 全部恢复

生效层级：项目覆盖 > 全局覆盖 > 代码默认。保存返回警告（缺占位符/
缺契约锚点等）但不阻断——占位符允许删除，删除后对应内容不注入。
"""
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_state, require_project
from ..errors import ApiError
from ..state import AppState

router = APIRouter(prefix='/prompts', tags=['prompts'])


def _registry():
    import prompt_store
    return prompt_store


@router.get('')
async def list_prompts(state: AppState = Depends(get_state)):
    """全部提示词模板：默认文本 / 两层覆盖 / 生效文本与来源"""
    ps = _registry()
    global_ov = ps.get_global_overrides()
    project_ov = ps.get_project_overrides() if state.db is not None else {}
    prompts = []
    for pid, d in ps.REGISTRY.items():
        text, source = ps.effective(pid)
        prompts.append({
            'id': pid, 'category': d.category, 'name': d.name,
            'description': d.description, 'default_text': d.default_text,
            'global_override': global_ov.get(pid),
            'project_override': project_ov.get(pid),
            'effective_text': text, 'source': source,
            'placeholders': [
                {'token': k, 'desc': v} for k, v in d.placeholders.items()],
            'contract_anchors': list(d.contract_anchors),
            'cache_note': d.cache_note,
        })
    return {
        'prompts': prompts,
        'categories': list(ps._CATEGORIES),
        'has_project': state.db is not None,
    }


class PromptSaveIn(BaseModel):
    scope: str          # global | project
    text: str


@router.put('/{pid}')
async def save_prompt(pid: str, req: PromptSaveIn,
                      state: AppState = Depends(get_state)):
    """保存单条自定义（scope 决定写入层）；返回警告列表（不阻断）"""
    ps = _registry()
    if pid not in ps.REGISTRY:
        raise ApiError(404, 'NO_PROMPT', f'未知提示词: {pid}')
    text = req.text or ''
    if not text.strip():
        raise ApiError(400, 'EMPTY_TEXT',
                       '提示词不能为空；要恢复默认请使用「恢复默认」')
    warnings = ps.warnings_for(pid, text)

    if req.scope == 'global':
        overrides = ps.get_global_overrides()
        overrides[pid] = text
        await state.run_sync(state.save_global_prompts, overrides)
    elif req.scope == 'project':
        if state.db is None:
            raise ApiError(400, 'NEED_PROJECT', '未打开项目，无法保存项目级覆盖')
        overrides = ps.get_project_overrides()
        overrides[pid] = text
        await state.run_sync(state.save_project_prompts, overrides)
    else:
        raise ApiError(400, 'BAD_SCOPE', f'未知 scope: {req.scope}')

    state.logger.info(f'提示词已保存: {pid}（{req.scope}）', panel='projects')
    return {'ok': True, 'warnings': warnings}


class PromptResetIn(BaseModel):
    scope: str = 'both'   # global | project | both


@router.post('/{pid}/reset')
async def reset_prompt(pid: str, req: PromptResetIn | None = None,
                       state: AppState = Depends(get_state)):
    """恢复单条默认（清指定层的覆盖；默认清两层）"""
    ps = _registry()
    if pid not in ps.REGISTRY:
        raise ApiError(404, 'NO_PROMPT', f'未知提示词: {pid}')
    scope = 'both' if req is None else req.scope
    if scope not in ('global', 'project', 'both'):
        raise ApiError(400, 'BAD_SCOPE', f'未知 scope: {scope}')

    if scope in ('global', 'both'):
        overrides = ps.get_global_overrides()
        if pid in overrides:
            del overrides[pid]
            await state.run_sync(state.save_global_prompts, overrides)
    if scope in ('project', 'both') and state.db is not None:
        overrides = ps.get_project_overrides()
        if pid in overrides:
            del overrides[pid]
            await state.run_sync(state.save_project_prompts, overrides)

    state.logger.info(f'提示词已恢复默认: {pid}（{scope}）', panel='projects')
    return {'ok': True}


@router.post('/reset')
async def reset_all(state: AppState = Depends(get_state)):
    """全部恢复默认（清全局 + 项目两层全部覆盖）"""
    ps = _registry()
    await state.run_sync(state.save_global_prompts, {})
    if state.db is not None:
        await state.run_sync(state.save_project_prompts, {})
    else:
        ps.clear_project_overrides()
    state.logger.info('全部提示词已恢复默认', panel='projects')
    return {'ok': True}
