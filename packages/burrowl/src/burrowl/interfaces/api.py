"""工作台 HTTP 服务（FastAPI）。只监听 127.0.0.1，所有路径在随机 token 前缀下：

    GET  /<token>/                          工作台页面（前端构建产物）
    GET  /<token>/api/v1/ops                操作目录
    POST /<token>/api/v1/ops/<name>         调用操作（JSON 请求体即参数）
    GET  /<token>/api/v1/events/reading/<RS> 阅读会话的修订变化（SSE，实时刷新）
    *    /<token>/mcp                       MCP Streamable HTTP（可选）

安全：校验 Host（防 DNS rebinding）；POST 要求同源 Origin 与 JSON 内容类型；
响应带 CSP；错误统一为 Problem 结构。工作台调用者的身份是本机用户（actor.kind=human）。
"""
import asyncio
import contextlib
import json
import secrets as token_source
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from ..app import App
from ..core.errors import BurrowlError
from .ops.registry import Ctx, call, catalog, load

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
MAX_BODY = 4 * 1024 * 1024


def create_api(app: App, *, token: str | None = None, static_dir: Path | None = None, port: int = 0,
               actor: dict | None = None, mcp_server: Any = None) -> tuple[FastAPI, str]:
    """mcp_server：可选的 MCPServer，挂载在 /<token>/mcp（其会话管理器随本应用启动/关闭）。"""
    token = token or token_source.token_hex(24)
    actor = actor or {"kind": "human", "id": "workbench-user"}
    prefix = f"/{token}"
    load()
    mcp_app = mcp_server.streamable_http_app(streamable_http_path="/") if mcp_server is not None else None

    @contextlib.asynccontextmanager
    async def lifespan(_api: FastAPI):
        if mcp_app is None:
            yield
            return
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    api = FastAPI(title="burrowl", docs_url=None, redoc_url=None, openapi_url=f"{prefix}/api/v1/openapi.json",
                  lifespan=lifespan)
    api.state.port = port

    @api.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"code": "ACCESS_DENIED", "message": "只接受本机访问"}, status_code=403)
        if not request.url.path.startswith(prefix + "/") and request.url.path != prefix:
            return JSONResponse({"code": "NOT_FOUND", "message": "未知地址"}, status_code=404)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin is not None and origin.split("://", 1)[-1].split(":")[0] not in {"127.0.0.1", "localhost"}:
                return JSONResponse({"code": "ACCESS_DENIED", "message": "拒绝跨站请求"}, status_code=403)
            if not request.url.path.endswith("/mcp") and \
                    not (request.headers.get("content-type") or "").startswith("application/json"):
                return JSONResponse({"code": "INVALID_ARGUMENT", "message": "需要 application/json"}, status_code=415)
            if int(request.headers.get("content-length") or 0) > MAX_BODY:
                return JSONResponse({"code": "INVALID_ARGUMENT", "message": "请求体过大"}, status_code=413)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @api.exception_handler(BurrowlError)
    async def problem(_request: Request, exc: BurrowlError):
        return JSONResponse(exc.problem(), status_code=exc.http_status)

    @api.get(f"{prefix}/api/v1/ops")
    def ops() -> list:
        return catalog()

    @api.post(f"{prefix}/api/v1/ops/{{name}}")
    async def invoke(name: str, request: Request):
        body = await request.body()
        try:
            arguments = json.loads(body or b"{}")
        except ValueError as exc:
            raise BurrowlError("INVALID_ARGUMENT", "请求体不是合法 JSON") from exc
        if not isinstance(arguments, dict):
            raise BurrowlError("INVALID_ARGUMENT", "请求体必须是 JSON 对象")
        ctx = Ctx(app=app, actor=actor, channel="http")
        result = await asyncio.to_thread(call, ctx, name, arguments)
        return JSONResponse(result)

    @api.get(f"{prefix}/api/v1/events/reading/{{session_id}}")
    async def reading_events(session_id: str, since: int = 0):
        """推送阅读会话的新修订号（工作台据此刷新）。连接最长 10 分钟，客户端自动重连。"""
        app.reading.sessions.read(session_id)  # 不存在时立即 404

        async def stream():
            last = since
            for _ in range(1200):
                _, revision = await asyncio.to_thread(app.reading.sessions.read, session_id)
                if revision != last:
                    last = revision
                    yield f"event: revision\ndata: {json.dumps({'revision': revision})}\n\n"
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    if mcp_app is not None:
        api.mount(f"{prefix}/mcp", mcp_app)

    @api.get(prefix + "/{path:path}")
    def static(path: str = ""):
        if static_dir is None or not static_dir.is_dir():
            return Response("工作台前端尚未构建（frontend/ → npm run build）", media_type="text/plain; charset=utf-8")
        root = static_dir.resolve()
        target = (root / path).resolve() if path else root / "index.html"
        if not target.is_relative_to(root) or not target.is_file():
            target = root / "index.html"  # 前端使用 hash 路由；未知路径回到首页
        return FileResponse(target)

    return api, token
