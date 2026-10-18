"""LLM 调用轨迹 API：轨迹面板数据源（主存储在 app.db 的 api_log_entries）"""
import os

from fastapi import APIRouter, Depends

from ..deps import get_state
from ..state import AppState

router = APIRouter(tags=['api-logs'])


@router.get('/logs/api')
async def get_api_logs(limit: int = 50, task_type: str = '',
                       before_id: int = 0,
                       state: AppState = Depends(get_state)):
    """最近 LLM 调用记录（时间正序），含完整请求/响应与 usage。

    存储：app.db api_log_entries（重启不丢，保留最近 500 条）；
    before_id>0 向前翻页；limit 上限 200。
    """
    env_on = bool(os.environ.get('API_LOG'))
    setting_on = False
    try:
        setting_on = state.app_db.get_setting('api_log', '0') == '1'
    except Exception:
        pass
    entries = await state.run_sync(
        state.app_db.list_api_log_entries, min(max(limit, 1), 200),
        task_type, max(before_id, 0))
    return {'entries': entries, 'enabled': env_on or setting_on,
            'env_forced': env_on}
