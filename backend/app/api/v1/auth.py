"""认证相关路由。

这一层只做三件事：接参数、调服务、决定状态码（NFR-MAINT-02）。
业务规则一律在 app/services/auth.py。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cookies import clear_session_cookie, set_session_cookie
from app.api.deps import CurrentUser, session_token
from app.db.models import User
from app.db.session import get_session
from app.schemas.auth import LoginRequest, RegisterRequest, UserResponse
from app.services.auth import login_user, logout_user, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="注册",
    responses={409: {"description": "邮箱已被注册（AUTH_EMAIL_TAKEN）"}},
)
async def register(
    payload: RegisterRequest,
    # 用 Annotated 而不是 `session: AsyncSession = Depends(get_session)`：
    # 后者把一次函数调用放进了参数默认值，ruff 的 B008 会报（那个规则对 FastAPI
    # 是误报，但与其 ignore 掉规则，不如用 FastAPI 现在推荐的写法，
    # 顺带让参数默认值恢复成真正的「默认值」语义）。
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """注册新用户，并自动创建个人学习空间（FR-AUTH-01~03、FR-AUTH-08）。

    **不在这里种会话 Cookie**：FR-AUTH-05 把「登录成功写入 Cookie」定义在登录上。
    注册后是否自动登录是个产品决定，要改的话在这里加一行 set_session_cookie 即可，
    逻辑都已经在下面备好了。目前不做，是因为注册页和登录页是两个页面，
    自动登录会让「注册完回到哪」这个问题多一种分支，第一版没必要。
    """
    return await register_user(
        session,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
    )


@router.post(
    "/login",
    response_model=UserResponse,
    summary="登录",
    responses={401: {"description": "邮箱或密码不正确（AUTH_INVALID_CREDENTIALS）"}},
)
async def login(
    payload: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """校验邮箱密码，成功后把会话令牌写进 HttpOnly Cookie（FR-AUTH-04~06）。

    直接返回用户对象，而不是 204 让前端再调一次 /me：前端登录后马上就要渲染
    用户名之类的东西，少一次往返；而且这一步的用户数据本来就是刚查出来的。
    """
    user, token, _expires_at = await login_user(
        session, email=payload.email, password=payload.password
    )
    set_session_cookie(response, token)
    return user


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    # 显式用 Response：204 按定义不能有响应体，而 JSONResponse 会把返回的 None
    # 序列化成字面量 `null` 塞进 body，那是个不合规的 204。
    response_class=Response,
    summary="退出登录",
)
async def logout(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    token: Annotated[str | None, Depends(session_token)],
) -> None:
    """删掉当前会话并清 Cookie（FR-AUTH-07）。

    刻意**不加 get_current_user 依赖**：那会让「会话已经过期后再点退出」返回 401，
    而用户想要的「让我退出登录」其实已经成立了，前端还得为此专门处理一个
    没有意义的错误分支。这里做成幂等的——没登录过、令牌无效，都照样 204。
    """
    await logout_user(session, token)
    clear_session_cookie(response)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="当前登录用户",
    responses={401: {"description": "未登录或会话已过期（AUTH_REQUIRED）"}},
)
async def read_current_user(user: CurrentUser) -> User:
    """FR-AUTH-09。前端启动时靠它判断「我是不是还在登录状态」。"""
    return user
