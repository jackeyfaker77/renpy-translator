"""定时任务 API：CRUD + 立即执行（触发判定在 server/scheduler.py）"""
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..deps import get_state
from ..errors import ApiError
from ..scheduler import next_run_at
from ..state import AppState

router = APIRouter(prefix='/scheduled', tags=['scheduled'])

# 可定时的任务类型（与直接执行一致的细粒度；项目创建/更新需用户输入文件，不可定时）
SUPPORTED_KINDS = {
    'texts.translate-all.dialogue': '对话批量翻译',
    'texts.translate-all.ui': 'UI 字符串翻译',
    'names.translate-all.both': '人名翻译+分析',
    'names.translate-all.name': '人名翻译',
    'names.translate-all.analyze': '角色分析',
    'export.game': '导出游戏',
    'embedded.scan': '提取内嵌文本',
    'ui.hints-rebuild': '重建字符串上下文',
    'graph.story-build': '构建剧情图',
    'graph.relations-build': '构建关系图',
    'texts.failed-retry.dialogue': '失败条目重试（对话）',
    'texts.failed-retry.ui': '失败条目重试（字符串）',
    'project.export-zip': '导出项目包',
}

_HHMM = re.compile(r'^([01]?\d|2[0-3]):[0-5]\d$')


def _validate_schedule(schedule_type: str, schedule: dict, repeat: str) -> None:
    if schedule_type not in ('delay', 'at', 'window'):
        raise ApiError(400, 'BAD_SCHEDULE', 'schedule_type 必须是 delay/at/window')
    if repeat not in ('once', 'daily'):
        raise ApiError(400, 'BAD_SCHEDULE', 'repeat_policy 必须是 once/daily')
    try:
        if schedule_type == 'delay':
            if float(schedule.get('minutes', 0)) <= 0:
                raise ValueError('minutes 必须为正数')
        elif schedule_type == 'at':
            datetime.fromisoformat(schedule['at'])
        elif schedule_type == 'window':
            if not (_HHMM.match(schedule.get('start', ''))
                    and _HHMM.match(schedule.get('end', ''))):
                raise ValueError('start/end 需为 HH:MM')
    except (KeyError, TypeError, ValueError) as e:
        raise ApiError(400, 'BAD_SCHEDULE', f'调度配置无效: {e}') from e


class CreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str
    project: str
    params: dict = {}
    schedule_type: str
    schedule: dict
    repeat_policy: str = 'once'


class UpdateIn(BaseModel):
    name: Optional[str] = None
    params: Optional[dict] = None
    schedule_type: Optional[str] = None
    schedule: Optional[dict] = None
    repeat_policy: Optional[str] = None
    enabled: Optional[bool] = None


@router.get('')
async def list_tasks(state: AppState = Depends(get_state)):
    tasks = await state.run_sync(state.app_db.list_scheduled_tasks)
    now = datetime.now()
    out = []
    for t in tasks:
        row = dict(t)
        nxt = next_run_at(t, now)
        row['next_run_at'] = nxt.isoformat(timespec='seconds') if nxt else None
        out.append(row)
    return {'tasks': out, 'kinds': SUPPORTED_KINDS}


@router.post('')
async def create_task(req: CreateIn, state: AppState = Depends(get_state)):
    if req.kind not in SUPPORTED_KINDS:
        raise ApiError(400, 'BAD_KIND', f'未知任务类型: {req.kind}')
    if not await state.run_sync(state.project_manager.project_exists, req.project):
        raise ApiError(404, 'PROJECT_NOT_FOUND', f'项目不存在: {req.project}')
    repeat = req.repeat_policy
    if req.schedule_type == 'delay':
        repeat = 'once'  # 倒计时恒为一次
    _validate_schedule(req.schedule_type, req.schedule, repeat)
    tid = await state.run_sync(state.app_db.add_scheduled_task, {
        'name': req.name.strip(), 'kind': req.kind, 'project': req.project,
        'params': req.params, 'schedule_type': req.schedule_type,
        'schedule': req.schedule, 'repeat_policy': repeat, 'enabled': True,
    })
    return {'id': tid}


@router.put('/{task_id}')
async def update_task(task_id: str, req: UpdateIn,
                      state: AppState = Depends(get_state)):
    task = await state.run_sync(state.app_db.get_scheduled_task, task_id)
    if not task:
        raise ApiError(404, 'NOT_FOUND', f'定时任务不存在: {task_id}')
    fields = {}
    if req.name is not None:
        fields['name'] = req.name.strip()
    if req.params is not None:
        fields['params'] = req.params
    if req.schedule_type is not None or req.schedule is not None or req.repeat_policy is not None:
        st = req.schedule_type or task['schedule_type']
        sch = req.schedule if req.schedule is not None else task['schedule']
        repeat = req.repeat_policy or task['repeat_policy']
        if st == 'delay':
            repeat = 'once'
        _validate_schedule(st, sch, repeat)
        fields.update(schedule_type=st, schedule=sch, repeat_policy=repeat)
    if req.enabled is not None:
        fields['enabled'] = req.enabled
    await state.run_sync(state.app_db.update_scheduled_task, task_id, **fields)
    return {'ok': True}


@router.delete('/{task_id}')
async def delete_task(task_id: str, state: AppState = Depends(get_state)):
    ok = await state.run_sync(state.app_db.delete_scheduled_task, task_id)
    if not ok:
        raise ApiError(404, 'NOT_FOUND', f'定时任务不存在: {task_id}')
    return {'ok': True}


@router.post('/{task_id}/run-now')
async def run_now(task_id: str, state: AppState = Depends(get_state)):
    """立即执行一次（不消耗/不改变计划本身）"""
    from ..scheduler import fire
    task = await state.run_sync(state.app_db.get_scheduled_task, task_id)
    if not task:
        raise ApiError(404, 'NOT_FOUND', f'定时任务不存在: {task_id}')
    result = await fire(state, task)
    if result.get('error') == 'project-missing':
        raise ApiError(404, 'PROJECT_NOT_FOUND', f'项目不存在: {task["project"]}')
    return {'job_id': result.get('job_id'),
            'message': '已发起执行' if result.get('job_id') else '未能发起（详见任务中心/上次状态）'}
