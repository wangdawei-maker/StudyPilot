"""API-02 / API-03 错误契约，以及 FR-EVAL-05 的 request_id 串联。

错误处理器装在探针 app 上，不是装在 app.main 那个 app 上。原因：要测 500 和自定义
业务异常，就得有会抛它们的路由，把这种路由挂到真 app 上很难看。探针 app 走的是
同一个 register_exception_handlers 和同一个中间件，注册代码本身是被真实覆盖的；
main.py 有没有把它们装上，由本文件末尾那两条针对真 app 的用例负责。
"""

import logging
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, EmailStr, Field
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import NotFoundError, register_exception_handlers
from app.core.logging_setup import RequestIDFilter
from app.core.request_id import REQUEST_ID_HEADER, RequestIDMiddleware
from app.main import app as real_app

# 故意写成一眼能认出来的字符串，方便断言「它没有出现在响应体里」。
LEAKY_PASSWORD = "LEAKME-1"


class _RegisterPayload(BaseModel):
    email: EmailStr
    # 12 位下限是为了让 LEAKY_PASSWORD（8 位）稳定校验失败。
    password: str = Field(min_length=12)


@pytest.fixture
def probe_app() -> FastAPI:
    probe = FastAPI()
    probe.add_middleware(RequestIDMiddleware)
    register_exception_handlers(probe)

    @probe.get("/probe/app-error")
    async def _app_error() -> None:
        raise NotFoundError("工作区不存在", details={"workspace_id": "w-1"})

    @probe.post("/probe/validate")
    async def _validate(payload: _RegisterPayload) -> dict[str, str]:
        return {"email": payload.email}

    @probe.get("/probe/unexpected")
    async def _unexpected() -> None:
        # 模拟一个会泄露内部信息的意外异常。异常文案里的连接串是给
        # 「响应体不得回显异常内容」那条用例准备的诱饵。
        raise RuntimeError("连接 postgresql://studypilot:sekrit@db:5432 失败")

    return probe


@pytest_asyncio.fixture
async def probe_client(probe_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=probe_app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def probe_client_no_reraise(probe_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """不重抛版本。

    未处理异常最终由 Starlette 的 ServerErrorMiddleware 兜住，而它在生成 500 响应
    之后**一定会把异常重新抛出**（好让 ASGI 服务器记日志）。ASGITransport 默认
    raise_app_exceptions=True，于是异常会直接冲进测试里，看不到响应。
    只有这条路径需要关掉它。
    """
    async with AsyncClient(
        transport=ASGITransport(app=probe_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


async def test_request_id_header_is_a_fresh_uuid_per_request(
    probe_client: AsyncClient,
) -> None:
    ids = []
    for _ in range(2):
        response = await probe_client.get("/probe/app-error")
        ids.append(response.headers[REQUEST_ID_HEADER])

    assert len(ids[0]) == 32 and all(c in "0123456789abcdef" for c in ids[0])
    assert ids[0] != ids[1], "两个请求拿到了同一个 request_id"


async def test_app_error_has_the_api_02_shape(probe_client: AsyncClient) -> None:
    response = await probe_client.get("/probe/app-error")

    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"code", "message", "details", "request_id"}
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "工作区不存在"
    assert body["details"] == {"workspace_id": "w-1"}


async def test_body_request_id_matches_the_header(probe_client: AsyncClient) -> None:
    """两边必须同值，否则拿响应里的 id 去搜日志会搜不到。"""
    response = await probe_client.get("/probe/app-error")

    assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]


async def test_unknown_route_is_reshaped_into_api_02(probe_client: AsyncClient) -> None:
    """路由 404 由 Starlette 抛出，默认响应体是 {"detail": "Not Found"}，得被接管。"""
    response = await probe_client.get("/probe/does-not-exist")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert "detail" not in response.json()


async def test_method_not_allowed_is_reshaped_into_api_02(
    probe_client: AsyncClient,
) -> None:
    response = await probe_client.get("/probe/validate")

    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"


async def test_validation_error_lists_fields_without_echoing_input(
    probe_client: AsyncClient,
) -> None:
    response = await probe_client.post(
        "/probe/validate", json={"email": "a@example.com", "password": LEAKY_PASSWORD}
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"

    fields = [detail["loc"] for detail in body["details"]]
    assert ["body", "password"] in fields

    # FR-AUTH-02：响应里不得出现明文密码。pydantic 的原始错误结构里 input 字段
    # 就是用户提交的原值，处理器把它剥掉了，这里守住这个行为。
    assert LEAKY_PASSWORD not in response.text


def test_pydantic_errors_really_do_carry_the_input() -> None:
    """上面那条断言的护栏。

    剥掉 input 只有在 input 真的存在时才有意义。哪天 pydantic 改了默认行为，
    上面那条用例会一直绿着，却已经什么都没在保护了——这条会在那时失败，
    提醒去删掉上面那条，或者改用别的防泄露手段。
    """
    with pytest.raises(PydanticValidationError) as excinfo:
        _RegisterPayload.model_validate({"email": "a@example.com", "password": LEAKY_PASSWORD})

    assert LEAKY_PASSWORD in str(excinfo.value.errors())


async def test_unexpected_exception_hides_internals_from_the_client(
    probe_client_no_reraise: AsyncClient,
) -> None:
    response = await probe_client_no_reraise.get("/probe/unexpected")

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert body["request_id"]

    # 异常文案里有数据库连接串，绝不能出现在响应里。
    assert "sekrit" not in response.text
    assert "RuntimeError" not in response.text


async def test_error_response_can_be_traced_back_to_the_log(
    probe_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-EVAL-05 的核心：拿响应里的 request_id 能捞到那条日志。"""
    # 过滤器挂在我们的 handler 上，caplog 的 handler 上没有，
    # 所以 record.request_id 得手动补一个才读得到。
    caplog.handler.addFilter(RequestIDFilter())
    caplog.set_level(logging.WARNING)

    response = await probe_client.get("/probe/app-error")
    request_id = response.json()["request_id"]

    matches = [r for r in caplog.records if getattr(r, "request_id", None) == request_id]
    assert matches, f"日志里没有 request_id 为 {request_id} 的记录"
    assert any("NOT_FOUND" in r.getMessage() for r in matches)


async def test_real_app_installs_the_middleware() -> None:
    """main.py 有没有真的装上中间件——探针 app 覆盖不到这一条。"""
    async with AsyncClient(
        transport=ASGITransport(app=real_app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert len(response.headers[REQUEST_ID_HEADER]) == 32


async def test_real_app_installs_the_handlers() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=real_app), base_url="http://test"
    ) as client:
        response = await client.get("/no-such-route")

    assert response.status_code == 404
    assert set(response.json()) == {"code", "message", "details", "request_id"}
