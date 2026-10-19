"""全量模式：从 app.openapi() 自省生成 REST 镜像工具（api_*）。

- 命名：api_{method}_{path}（去 /api 前缀、{param}→param、/→_）
- schema：path/query 平铺，requestBody JSON 整体挂 body
- 危险自动判定（非 GET）：全部 DELETE、shutdown/data-dir、
  path 含 /clear|/reset|/replace|/delete|/purge 段 → 注入 confirm
- 排除：SSE、multipart、二进制、明文 key、SPA fallback
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI
from mcp_types import CallToolResult, TextContent, Tool as MCPTool

from .bridge import ApiBridgeError, api_call

_DANGEROUS_PATH = re.compile(
    r'^/api/(shutdown|settings/data-dir)$|/(clear|reset|replace|delete|purge)(/|$)')
_EXCLUDE_SUFFIX = ('/events',)
_EXCLUDE_SUBSTR = (
    '/packages/{file}',      # 二进制下载
    '/thumb/',               # 缩略图
    '/avatar',               # 立绘/头像
    '/configs/{name}/key',   # 明文 API Key
    '/jobs/{job_id}/events', # SSE（已由 suffix 覆盖，双保险）
)
_METHODS = ('get', 'post', 'put', 'patch', 'delete')


def tool_name(method: str, path: str) -> str:
    """api_get_texts_dialogue 形式；≤64 字符"""
    p = path[len('/api'):] if path.startswith('/api') else path
    p = re.sub(r'\{([^}]+)\}', r'\1', p)
    p = re.sub(r'[^a-zA-Z0-9]+', '_', p).strip('_').lower()
    name = f'api_{method.lower()}_{p}'
    return name[:64]


def is_dangerous(method: str, path: str) -> bool:
    if method.upper() == 'GET':
        return False
    if method.upper() == 'DELETE':
        return True
    return bool(_DANGEROUS_PATH.search(path))


def _excluded(method: str, path: str, op: dict) -> bool:
    if path in ('/',) or path.startswith('/assets'):
        return True
    if any(path.endswith(s) or s in path for s in _EXCLUDE_SUFFIX):
        return True
    if any(s in path for s in _EXCLUDE_SUBSTR):
        return True
    rb = op.get('requestBody') or {}
    content = rb.get('content') or {}
    if 'multipart/form-data' in content:
        return True
    return False


def _param_schema(op: dict, path: str) -> dict:
    """inputSchema：path/query 平铺 + body 子对象"""
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in op.get('parameters') or []:
        name = p.get('name')
        if not name:
            continue
        schema = dict(p.get('schema') or {})
        # OpenAPI 的 nullable/anyOf 简化：保留原 schema，query 可选不进 required
        props[name] = schema
        if p.get('required') and p.get('in') == 'path':
            required.append(name)
    rb = op.get('requestBody') or {}
    content = (rb.get('content') or {}).get('application/json') or {}
    body_schema = content.get('schema')
    if body_schema:
        props['body'] = body_schema
        if rb.get('required'):
            required.append('body')
    danger = False  # 由调用方注入 confirm；此处占位
    schema: dict[str, Any] = {'type': 'object', 'properties': props}
    if required:
        schema['required'] = required
    return schema


def build_full_tools(app: FastAPI) -> tuple[list[MCPTool], dict[str, dict]]:
    """返回 (tools, dispatch 表)。dispatch 表: name -> {method, path, dangerous}"""
    spec = app.openapi()
    paths = spec.get('paths') or {}
    tools: list[MCPTool] = []
    dispatch: dict[str, dict] = {}
    used: dict[str, int] = {}
    for path, item in sorted(paths.items()):
        if not path.startswith('/api'):
            continue
        for method in _METHODS:
            op = (item or {}).get(method)
            if not op or _excluded(method, path, op):
                continue
            name = tool_name(method, path)
            if name in used:
                used[name] += 1
                name = f'{name}_{used[name]}'[:64]
            else:
                used[name] = 1
            dangerous = is_dangerous(method, path)
            schema = _param_schema(op, path)
            if dangerous:
                schema.setdefault('properties', {})['confirm'] = {
                    'type': 'boolean',
                    'description': '危险操作必须显式传 true 才执行',
                    'default': False,
                }
            summary = op.get('summary') or op.get('description') or f'{method} {path}'
            desc = summary
            if dangerous:
                desc = f'【危险】{summary}。必须传 confirm=true 才执行。'
            tools.append(MCPTool(
                name=name, description=desc,
                input_schema=schema,
            ))
            dispatch[name] = {
                'method': method.upper(), 'path': path,
                'dangerous': dangerous, 'desc': desc,
            }
    return tools, dispatch


async def dispatch_full(app: FastAPI, name: str, arguments: dict,
                        table: dict[str, dict]) -> CallToolResult:
    """执行 api_* 工具，返回 CallToolResult"""
    entry = table.get(name)
    if not entry:
        return CallToolResult(content=[TextContent(
            type='text', text=f'未知工具: {name}')], is_error=True)
    args = dict(arguments or {})
    confirm = bool(args.pop('confirm', False))
    if entry['dangerous'] and not confirm:
        return CallToolResult(content=[TextContent(
            type='text',
            text=f"危险操作需要 confirm=true：{entry['desc']}")],
            is_error=True)
    path = entry['path']
    # 填 path 参数（URL 编码：项目名等可能含空格/中文）
    from urllib.parse import quote
    for key in list(args.keys()):
        if '{' + key + '}' in path:
            path = path.replace('{' + key + '}',
                                quote(str(args.pop(key)), safe=''))
    # 剩余：body 子对象 + query（不在 path 里出现的标量）
    body = args.pop('body', None)
    params = {k: v for k, v in args.items()
              if not isinstance(v, (dict, list))}
    # 数组/对象类 query 参数以 JSON 字符串传（FastAPI 通常用 body，这里兜底）
    for k, v in list(args.items()):
        if isinstance(v, (dict, list)):
            params[k] = v
    try:
        data = await api_call(app, entry['method'], path,
                              params=params or None, json_body=body)
    except ApiBridgeError as e:
        return CallToolResult(content=[TextContent(
            type='text', text=f'[{e.code}] {e.message}')], is_error=True)
    import json as _json
    if isinstance(data, list):
        data = {'items': data}
    try:
        text = _json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(data)
    return CallToolResult(content=[TextContent(type='text', text=text)])
