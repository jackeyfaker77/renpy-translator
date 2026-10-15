"""LLM 调用轨迹 API：轨迹面板数据源（与 /logs 的文本日志分开）"""
from fastapi import APIRouter, Depends

from ..deps import get_state
from ..state import AppState

router = APIRouter(tags=['api-logs'])


@router.get('/logs/api')
async def get_api_logs(limit: int = 50, task_type: str = '',
                       before_id: int = 0,
                       state: AppState = Depends(get_state)):
    """最近 LLM 调用记录（时间正序），含完整请求/响应与 usage。

    before_id>0 向前翻页（只返回 id 更小的）；limit 上限 200（内存缓冲大小）。
    """
    data = await state.run_sync(
        state.get_api_log_entries, min(max(limit, 1), 200), task_type,
        max(before_id, 0))
    return data
