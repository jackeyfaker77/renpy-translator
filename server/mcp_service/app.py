"""MCPServer 构建与挂载：Streamable HTTP @ /mcp/ + 精选/全量动态工具列表"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, TextContent

from . import generate
from .curated import register_curated

_INSTRUCTIONS = """\
这是 Ren'Py 游戏翻译工具（renpy-translator）的 MCP 接口。可用能力覆盖：
项目管理、对话/UI 文本翻译、人名/术语、内嵌文本预筛、剧情/关系图谱、
导出导入、定时任务、任务中心、诊断日志、模型配置与提示词。

工作流约定：
- 长任务（批量翻译/导出/建图/扫描等）先调 start_* 拿到 {job_id}，
  再用 wait_job 等待；不要在一次工具调用里阻塞等待数分钟的任务。
- wait_job 返回 waiting_input 时，按 question.type 构造答案调用 answer_job，
  然后再次 wait_job 继续。
- 危险操作（删除项目、覆盖式导入等）必须传 confirm=true，否则会被拒绝。
- 导出产物通过返回值里的绝对 path（同机可直接读）或 download_url 获取。
- 多数 /current/* 操作需要先 open_project 打开项目；未打开会返回 NO_PROJECT 错误。
"""

_SECURITY = TransportSecuritySettings(
    # 显式只信本机 Host；Origin 一律拒绝（防恶意网页打本机 MCP）
    allowed_hosts=['127.0.0.1:*', 'localhost:*'],
    allowed_origins=[],
)


class RouterMCPServer(MCPServer):
    """按 app.db 的 mcp_tool_mode 动态切换精选/全量工具列表"""

    def __init__(self, app: FastAPI, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._host_app = app
        self._full_cache: tuple[list, dict] | None = None

    def _mode(self) -> str:
        try:
            return self._host_app.state.app_state.app_db.get_setting(
                'mcp_tool_mode', 'curated')
        except Exception:
            return 'curated'

    def _full(self) -> tuple[list, dict]:
        if self._full_cache is None:
            self._full_cache = generate.build_full_tools(self._host_app)
        return self._full_cache

    async def list_tools(self):
        base = await super().list_tools()
        if self._mode() != 'full':
            return base
        tools, _table = self._full()
        # full = api_* 镜像 + wait_job（REST 无法表达的聚合）；不叠精选
        wait = next((t for t in base if t.name == 'wait_job'), None)
        return list(tools) + ([wait] if wait else [])

    async def call_tool(self, name, arguments, context=None):
        if name.startswith('api_'):
            _tools, table = self._full()
            return await generate.dispatch_full(self._host_app, name,
                                                arguments, table)
        return await super().call_tool(name, arguments, context)


def install_mcp(app: FastAPI) -> MCPServer:
    """在 create_app 里调用：构建 MCPServer、注册精选工具、mount /mcp。

    必须在 SPA catch-all 注册之前调用（GET catch-all 会吞 MCP 的 GET/SSE）。
    """
    mcp = RouterMCPServer(
        app,
        name='renpy-translator',
        instructions=_INSTRUCTIONS,
    )
    register_curated(mcp, app)

    # 先取子应用：此后 mcp.session_manager 才存在（SDK 要求）
    mcp_app = mcp.streamable_http_app(
        streamable_http_path='/',
        transport_security=_SECURITY,
    )

    # 无尾斜杠兼容：307 保方法与请求体
    for method in ('GET', 'POST', 'DELETE', 'PUT'):
        _add_redirect(app, method)

    app.mount('/mcp', mcp_app)
    app.state.mcp = mcp
    return mcp


def _add_redirect(app: FastAPI, method: str) -> None:
    async def _redirect():  # noqa: ANN202
        return RedirectResponse(url='/mcp/', status_code=307)

    app.add_api_route(
        '/mcp', _redirect, methods=[method], include_in_schema=False)
