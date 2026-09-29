"""统一错误类型与异常处理器（API-02 / API-03）。

响应体固定为 `{ code, message, details, request_id }`：
- `code` 是机器可读的稳定标识，前端只认它（API-03）；
- `message` 是给人看的中文，随时可能改，前端不得依赖；
- `request_id` 与响应头 `X-Request-ID` 同值，用它去翻服务端日志（FR-EVAL-05）。

领域错误码（如「邮箱已注册」）不在这里定义，它们跟着对应功能一起加，
本文件只放与业务无关的通用错误。加新码时注意：**已发布的码不能改字符串**，
它就是前后端的接口契约。
"""

import logging
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_id import bind_request_id, get_request_id

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """错误码。用 StrEnum 而非裸字符串：拼错时是 AttributeError 而不是静默不匹配。"""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    # FR-AUTH-03：重复注册要被拒绝，且错误码稳定——前端靠它区分「邮箱已被占用」，
    # 而不是靠中文文案。
    AUTH_EMAIL_TAKEN = "AUTH_EMAIL_TAKEN"
    # FR-AUTH-04：登录失败。这一个码同时覆盖「邮箱没注册」和「密码不对」两种情况，
    # 有意的——分成两个码就等于把「这个邮箱存在吗」直接告诉调用方。
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    # 兜底的 4xx：框架抛的、状态码不在上面映射表里的那些。
    HTTP_ERROR = "HTTP_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """所有业务异常的基类。

    继承 Exception 而不是 Starlette 的 HTTPException，是为了让服务层抛错时不依赖
    Web 框架：状态码作为类属性声明，处理器负责翻译成响应。

    用法是「一个语义一个子类」而不是「抛的时候传 code」，这样错误码散不掉，
    搜代码也能直接按类名定位。
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "服务器内部错误"

    def __init__(self, message: str | None = None, details: Any = None) -> None:
        # 允许调用点覆盖文案但保留类上的 code/status；不传就用类默认。
        self.message = message or type(self).message
        self.details = details
        super().__init__(self.message)


class ValidationError(AppError):
    code = ErrorCode.VALIDATION_ERROR
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    message = "请求参数不合法"


class UnauthorizedError(AppError):
    code = ErrorCode.AUTH_REQUIRED
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "请先登录"


class InvalidCredentialsError(UnauthorizedError):
    """FR-AUTH-04：邮箱或密码不对。

    继承 UnauthorizedError，状态码沿用 401。文案刻意只写「邮箱或密码不正确」，
    不区分是哪种——写成「该邮箱未注册」就是一个免费的账号枚举接口。
    """

    code = ErrorCode.AUTH_INVALID_CREDENTIALS
    message = "邮箱或密码不正确"


class ForbiddenError(AppError):
    code = ErrorCode.FORBIDDEN
    status_code = status.HTTP_403_FORBIDDEN
    message = "没有权限执行该操作"


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND
    status_code = status.HTTP_404_NOT_FOUND
    message = "资源不存在"


class ConflictError(AppError):
    code = ErrorCode.CONFLICT
    status_code = status.HTTP_409_CONFLICT
    message = "资源冲突"


class RateLimitedError(AppError):
    code = ErrorCode.RATE_LIMITED
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "请求过于频繁，请稍后再试"


# 框架抛出的 HTTPException 状态码 -> 我们的错误码/文案。
# 映射表之外的 4xx 归到 HTTP_ERROR，5xx 归到 INTERNAL_ERROR。
_HTTP_ERROR_MAP: dict[int, tuple[ErrorCode, str]] = {
    status.HTTP_401_UNAUTHORIZED: (ErrorCode.AUTH_REQUIRED, "请先登录"),
    status.HTTP_403_FORBIDDEN: (ErrorCode.FORBIDDEN, "没有权限执行该操作"),
    status.HTTP_404_NOT_FOUND: (ErrorCode.NOT_FOUND, "资源不存在"),
    status.HTTP_405_METHOD_NOT_ALLOWED: (ErrorCode.METHOD_NOT_ALLOWED, "请求方法不被允许"),
    status.HTTP_409_CONFLICT: (ErrorCode.CONFLICT, "资源冲突"),
    status.HTTP_429_TOO_MANY_REQUESTS: (ErrorCode.RATE_LIMITED, "请求过于频繁，请稍后再试"),
}


def request_id_of(request: Request) -> str | None:
    """取当前请求的 id。

    优先读 scope（即 request.state），contextvar 只作兜底。原因：未处理异常的
    处理器跑在最外层的 ServerErrorMiddleware 上，而异常是先穿过
    RequestIDMiddleware 的 finally（contextvar 在那里被清掉）才冒到最外层的，
    那时 contextvar 已经是空的。scope 跟着请求走，不受影响。
    """
    return getattr(request.state, "request_id", None) or get_request_id()


def error_response(
    status_code: int,
    code: ErrorCode,
    message: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    """按 API-02 组装响应。

    request_id 由调用方从 request 上取（见 request_id_of）；不传才退回 contextvar。
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code.value,
            "message": message,
            "details": details,
            "request_id": request_id or get_request_id(),
        },
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """装上全部异常处理器。抽成函数是为了测试能在一张干净的 app 上重复安装。"""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        # 4xx 是调用方的问题，记 warning 就够；5xx 是我们的问题，要有 error + 堆栈。
        if exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            logger.error("业务异常 %s: %s", exc.code.value, exc.message, exc_info=exc)
        else:
            logger.warning("%s %s -> %s: %s", request.method, request.url.path, exc.code.value, exc.message)
        return error_response(
            exc.status_code, exc.code, exc.message, exc.details, request_id=request_id_of(request)
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 只留字段路径、错误类型、提示三样。刻意丢掉 pydantic 原始结构里的
        # input 和 ctx：
        # - input 是用户提交的原始值，密码字段校验不过时明文密码会原样出现在
        #   响应体里，直接违反 FR-AUTH-02（不得出现明文密码或哈希）；
        # - ctx 里可能带着异常对象，未必能 JSON 序列化。
        details = [
            {
                "loc": [str(part) for part in error["loc"]],
                "type": error["type"],
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        logger.warning("%s %s -> VALIDATION_ERROR: %s", request.method, request.url.path, details)
        return error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorCode.VALIDATION_ERROR,
            "请求参数不合法",
            details,
            request_id=request_id_of(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # 路由没匹配上（404）、方法不对（405）走的是这条。不接管的话响应体是
        # Starlette 的 {"detail": "Not Found"}，形状对不上 API-02。
        mapped = _HTTP_ERROR_MAP.get(exc.status_code)
        if mapped is None:
            if exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
                code, message = ErrorCode.INTERNAL_ERROR, "服务器内部错误"
            else:
                code, message = ErrorCode.HTTP_ERROR, str(exc.detail)
        else:
            code, message = mapped

        logger.warning("%s %s -> %s(%s)", request.method, request.url.path, code.value, exc.status_code)
        # 透传 headers：401 的 WWW-Authenticate 之类的语义靠它，
        # 吞掉的话浏览器/客户端的标准行为就不对了。
        return error_response(
            exc.status_code, code, message, headers=exc.headers, request_id=request_id_of(request)
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 未预料的异常：堆栈只进日志，不回给客户端——异常信息里常有表名、SQL、
        # 甚至连接串片段，直接吐出去就是信息泄露。
        request_id = request_id_of(request)
        # 重新绑一次 contextvar，好让下面这条日志带上 request_id。这是 FR-EVAL-05
        # 最需要成立的一处：500 正是最需要拿着 id 去翻日志的场景。
        with bind_request_id(request_id):
            # exc_info 带上，否则日志里只有一个光秃秃的类型名，等于没记。
            logger.error("未处理异常 %s %s", request.method, request.url.path, exc_info=exc)
        return error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ErrorCode.INTERNAL_ERROR,
            "服务器内部错误",
            request_id=request_id,
        )
