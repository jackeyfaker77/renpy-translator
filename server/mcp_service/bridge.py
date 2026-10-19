"""ASGI 自调用桥：MCP 工具经 httpx ASGITransport 调本应用的 /api/* 路由。

保留 FastAPI 依赖注入（require_project 等守卫）、Pydantic 校验与
ApiError 错误信封——与 WebUI 走完全相同的链路，不直接 import handler。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class ApiBridgeError(Exception):
    """REST 调用失败：携带状态码与错误信封"""

    def __init__(self, status: int, code: str, message: str, detail: str = '') -> None:
        super().__init__(f'[{code}] {message}')
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def _parse_error(status: int, body: Any) -> ApiBridgeError:
    if isinstance(body, dict) and isinstance(body.get('error'), dict):
        err = body['error']
        return ApiBridgeError(status, str(err.get('code', 'ERROR')),
                              str(err.get('message', '')), str(err.get('detail', '')))
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return ApiBridgeError(status, 'HTTP_ERROR', f'HTTP {status}: {text[:500]}')


async def api_call(app: FastAPI, method: str, path: str, *,
                   params: dict[str, Any] | None = None,
                   json_body: Any = None) -> Any:
    """调本应用路由，返回解析后的 JSON（或非 JSON 时的文本）。

    status>=400 → 抛 ApiBridgeError。
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url='http://mcp-bridge',
                           timeout=300.0) as client:
        resp = await client.request(method.upper(), path,
                                    params=params, json=json_body)
    text = resp.text
    body: Any
    try:
        body = resp.json() if text else None
    except ValueError:
        body = text
    if resp.status_code >= 400:
        raise _parse_error(resp.status_code, body)
    return body


def public_base_url(app: FastAPI) -> str:
    """对外可达的本机基址（Agent 同机访问用）"""
    import os
    port = os.environ.get('PORT', '7861')
    return f'http://127.0.0.1:{port}'
