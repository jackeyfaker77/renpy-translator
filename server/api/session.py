"""会话 API：当前项目、统计、开关项目"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_state, require_project
from ..errors import ApiError
from ..state import AppState

router = APIRouter(tags=['session'])


class OpenRequest(BaseModel):
    name: str


async def _stats(state: AppState) -> dict:
    if state.db is None:
        return {}
    d = await state.db_call(state.db.get_dialogue_count)
    u = await state.db_call(state.db.get_ui_text_count)
    n = await state.db_call(state.db.get_char_dict_count)
    return {'dialogue': d, 'ui': u, 'names': n}


@router.get('/session')
async def get_session(state: AppState = Depends(get_state)):
    return {
        'current_project': state.current_project,
        'has_translator': state.translator is not None,
        'stats': await _stats(state),
        'active_jobs': state.app_db.list_jobs(active_only=True),
        'interrupted_jobs': state.interrupted_count,
    }


@router.post('/session/open')
async def open_project(req: OpenRequest, state: AppState = Depends(get_state)):
    # 有在飞任务时拒绝切换：换项目会关旧库/拆服务，任务还在用旧库跑，
    # 而提示词覆盖等会话态会切到新项目——两头受污染
    if state.app_db.list_jobs(active_only=True):
        raise ApiError(409, 'JOBS_ACTIVE',
                       '还有任务正在执行，请等待完成或取消后再切换项目')
    await state.open_project(req.name)
    return {
        'current_project': state.current_project,
        'has_translator': state.translator is not None,
        'stats': await _stats(state),
    }


@router.post('/session/close')
async def close_project(state: AppState = Depends(require_project)):
    await state.close_project()
    return {'current_project': ''}
