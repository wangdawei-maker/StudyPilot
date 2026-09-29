"""路由层共用的依赖。

这一层只做一件事：把 HTTP 上的东西（Cookie、请求）翻译成服务层认的参数，
再把服务层的「查无此人」翻译成 401。判断规则本身在 app/services/auth.py。
"""

from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cookies import SESSION_COOKIE_NAME
from app.core.errors import UnauthorizedError
from app.db.models import User
from app.db.session import get_session
from app.services.auth import resolve_session_user


def session_token(
    token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> str | None:
    """取出 Cookie 里的令牌原文。

    单独做成依赖，是为了让「令牌存在哪个 Cookie 里」只写一遍：需要身份的接口用
    get_current_user，不需要身份但要碰令牌的（登出）用这个。两处各写一遍 alias
    的话，改 Cookie 名时漏改一处，现象是登出静默失效——接口照样 204，会话行却还在。
    """
    return token


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    token: Annotated[str | None, Depends(session_token)],
) -> User:
    """FR-AUTH-06/09：受保护接口的身份来源，未登录或会话过期一律 401。"""
    user = await resolve_session_user(session, token)
    if user is None:
        raise UnauthorizedError()
    return user


# 路由签名里直接用 CurrentUser，比重复写 Annotated[User, Depends(...)] 清楚。
CurrentUser = Annotated[User, Depends(get_current_user)]
