"""请求级追踪 id（FR-EVAL-05）。

id 存在 contextvar 里，日志过滤器负责取值，中间件负责写入与清理。
之所以用 contextvar 而不是塞进 request 对象：日志调用点（服务层、数据库层）
手里往往没有 Request，它们只想要「当前请求是谁」。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """当前请求的 id；不在请求上下文里（后台任务、启动期）返回 None。"""
    return _request_id.get()


@contextmanager
def bind_request_id(request_id: str | None) -> Iterator[None]:
    """临时把 contextvar 绑成指定值，退出时还原。

    给「已经脱离请求上下文、但还希望日志带上 id」的场合用。目前唯一的使用点是
    未处理异常的处理器：它跑在最外层的 ServerErrorMiddleware 里，
    而那时 RequestIDMiddleware 的 finally 已经把 contextvar 清掉了
    （异常是先穿过中间件的 finally 再冒到最外层的）。
    """
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


class RequestIDMiddleware:
    """给每个请求分配一个 id，写进 contextvar 和响应头。

    刻意写成纯 ASGI 中间件，不用 BaseHTTPMiddleware：后者会把下游调用丢进
    单独的 task，而 contextvar 是随 task 走的，在子 task 里 set 的值不会回流到
    外层——日志过滤器就读不到了，表现为「日志里全是占位符」，很难查。
    纯 ASGI 中间件只是 await 下游，同一个 task，没有这个问题。

    不沿用客户端传来的 X-Request-ID：那个值可以任意伪造，直接进日志等于开了
    日志注入的口子。本项目也没有需要串联的上游服务，自己生成即可。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # lifespan / websocket 没有请求这个概念，直接放行。
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex
        token = _request_id.set(request_id)
        # 顺手放进 scope，方便以后写别的中间件时用 request.state.request_id 取。
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            # 中间件实例是全局的，contextvar 却是按 task 复用的。
            # 不 reset 的话，同一个 task 处理下一个请求前若有人读，会拿到上一个请求的 id。
            _request_id.reset(token)
