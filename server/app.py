"""FastAPI 应用工厂：CORS、API 路由、MCP 挂载、SPA 静态挂载（fallback 到 index.html）"""
import asyncio
import logging
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .errors import register_error_handlers

ROOT = Path(__file__).resolve().parent.parent
# 冻结后 src/ 在 _MEIPASS 下（作为 data 打包）；开发态在仓库根
_BUNDLE = Path(getattr(sys, '_MEIPASS', ROOT))
SRC = _BUNDLE / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rt_home import home as _home  # noqa: E402

# PyInstaller 冻结时前端产物在 _MEIPASS 下
_WEB_DIST = _BUNDLE / 'web' / 'dist'

_req_logger = logging.getLogger('renpy_translator.requests')


def _quiet_disconnect_errors(loop, context):
    """过滤客户端断开连接的收尾噪声（Windows Proactor 已知问题）。

    SSE/HTTP 长连接被浏览器或 WebView 强断（页面刷新/关闭发 RST）时，
    Proactor 的 _call_connection_lost 在 sock.shutdown() 上抛
    ConnectionResetError [WinError 10054]，asyncio 默认 exception handler
    会把它整栈打成 "Exception in callback"——传输本已死亡，纯日志噪声。
    这类断开异常静默，其余异常走默认处理。
    """
    exc = context.get('exception')
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                        BrokenPipeError)):
        return
    loop.default_exception_handler(context)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .state import AppState
    # 挂到 uvicorn 主循环：客户端断开的收尾异常不再刷屏控制台
    asyncio.get_running_loop().set_exception_handler(_quiet_disconnect_errors)
    app.state.app_state = AppState(app.state.root)
    await app.state.app_state.startup()
    # mount 会禁用 MCP 子应用内置 lifespan：宿主必须自己跑 session_manager
    mcp = getattr(app.state, 'mcp', None)
    if mcp is not None:
        async with mcp.session_manager.run():
            yield
    else:
        yield
    # 关停广播在 /api/shutdown 端点里做（lifespan 退出时连接已被 uvicorn 断开）
    await app.state.app_state.shutdown()


def create_app(root: Path = None) -> FastAPI:
    app = FastAPI(title="Ren'Py 翻译工具", lifespan=lifespan)
    # 用户数据根（data/app.db、logs/ 等的基准）；冻结后为 exe 目录
    app.state.root = Path(root) if root else _home()

    @app.middleware('http')
    async def request_status_log(request: Request, call_next):
        """按状态码分级记录请求：4xx 警告、5xx 错误（替代 uvicorn 一刀切 INFO）"""
        try:
            response = await call_next(request)
        except Exception as e:
            _req_logger.error('%s %s -> 异常: %s',
                              request.method, request.url.path, e)
            raise
        status = response.status_code
        if status >= 500:
            _req_logger.error('%s %s -> %s',
                              request.method, request.url.path, status)
        elif status >= 400:
            _req_logger.warning('%s %s -> %s',
                                request.method, request.url.path, status)
        return response

    register_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],
        allow_methods=['*'],
        allow_headers=['*'],
    )

    from .api import router as api_router
    app.include_router(api_router, prefix='/api')

    # MCP：可选 token 只拦 /mcp（WebUI /api 保持既有信任级）
    @app.middleware('http')
    async def mcp_auth(request: Request, call_next):
        path = request.url.path
        if path != '/mcp' and not path.startswith('/mcp/'):
            return await call_next(request)
        state = getattr(request.app.state, 'app_state', None)
        if state is None:
            return JSONResponse(status_code=404, content={
                'error': {'code': 'MCP_UNAVAILABLE', 'message': '服务启动中'}})
        db = state.app_db
        if db.get_setting('mcp_enabled', '0') != '1':
            return JSONResponse(status_code=404, content={
                'error': {'code': 'MCP_DISABLED',
                          'message': 'MCP 未启用（设置页可开启）'}})
        token = db.get_setting('mcp_token', '')
        if token:
            auth = request.headers.get('authorization', '')
            got = auth[7:] if auth.startswith('Bearer ') else ''
            if not secrets.compare_digest(got, token):
                return JSONResponse(status_code=401, content={
                    'error': {'code': 'MCP_UNAUTHORIZED',
                              'message': '缺少或错误的 Authorization: Bearer <token>'}})
        return await call_next(request)

    # MCP 必须在 SPA catch-all 之前挂载（GET catch-all 会吞 MCP 的 GET/SSE）
    from .mcp_service import install_mcp
    install_mcp(app)

    if _WEB_DIST.is_dir():
        app.mount('/assets', StaticFiles(directory=_WEB_DIST / 'assets'),
                  name='assets')

        @app.get('/{full_path:path}', include_in_schema=False)
        async def spa_fallback(full_path: str):
            # resolve 后必须仍在 dist 内，防止 %2e%2e/ 之类目录穿越读出任意文件
            target = (_WEB_DIST / full_path).resolve()
            if (full_path and target.is_relative_to(_WEB_DIST.resolve())
                    and target.is_file()):
                return FileResponse(target)
            # index.html 必须每次重校验：重新构建后 chunk hash 全变，
            # 缓存的旧入口会让未访问页面的动态 import 404（菜单点击无反应）。
            # /assets 下的文件按内容 hash 命名，可安全使用默认缓存。
            return FileResponse(_WEB_DIST / 'index.html',
                                headers={'Cache-Control': 'no-cache'})

    return app
