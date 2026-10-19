"""MCP 接入设置：启用开关 / 工具范围 / token 管理（存 app.db settings）"""
import os
import secrets

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_state
from ..errors import ApiError
from ..state import AppState

router = APIRouter(prefix='/settings/mcp', tags=['mcp-settings'])

_TOOL_MODES = ('curated', 'full')


def _endpoint() -> str:
    port = os.environ.get('PORT', '7861')
    return f'http://127.0.0.1:{port}/mcp/'


def _snapshot(state: AppState) -> dict:
    db = state.app_db
    return {
        'enabled': db.get_setting('mcp_enabled', '0') == '1',
        'tool_mode': db.get_setting('mcp_tool_mode', 'curated') or 'curated',
        'has_token': bool(db.get_setting('mcp_token', '')),
        'endpoint': _endpoint(),
    }


@router.get('')
async def get_mcp_settings(state: AppState = Depends(get_state)):
    return _snapshot(state)


class McpSettingsIn(BaseModel):
    enabled: bool | None = None
    tool_mode: str | None = None


@router.put('')
async def put_mcp_settings(req: McpSettingsIn,
                           state: AppState = Depends(get_state)):
    db = state.app_db
    if req.tool_mode is not None and req.tool_mode not in _TOOL_MODES:
        raise ApiError(400, 'BAD_MODE', f'tool_mode 必须是 {_TOOL_MODES}')
    if req.enabled is not None:
        db.set_setting('mcp_enabled', '1' if req.enabled else '0')
    if req.tool_mode is not None:
        db.set_setting('mcp_tool_mode', req.tool_mode)
    return _snapshot(state)


@router.post('/token')
async def regenerate_token(state: AppState = Depends(get_state)):
    """生成新 token 并明文返回一次（旧 token 同时失效）"""
    token = secrets.token_urlsafe(32)
    state.app_db.set_setting('mcp_token', token)
    return {'token': token, **_snapshot(state)}


@router.delete('/token')
async def clear_token(state: AppState = Depends(get_state)):
    state.app_db.set_setting('mcp_token', '')
    return _snapshot(state)
