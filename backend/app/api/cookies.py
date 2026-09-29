"""会话 Cookie 的读写策略（FR-AUTH-05）。

放在 API 层而不是 core：这里管的是「令牌怎么在 HTTP 上传输」，属于 Web 传输细节；
core/security.py 管的是「令牌长什么样、怎么哈希」，属于密码学实现。两者分开之后，
改 Cookie 属性不会碰到哈希算法，反过来也一样。
"""

from fastapi import Response

from app.core.config import settings
from app.core.security import SESSION_TTL_SECONDS

# 带项目名前缀，避免和同一域名下别的服务的 Cookie 撞名。
# 改名等于让所有已登录用户掉线，定下之后别轻易动。
SESSION_COOKIE_NAME = "studypilot_session"


def _is_secure() -> bool:
    """是否给 Cookie 加 Secure（只走 HTTPS）。

    按 FR-AUTH-05，生产环境必须开。这里读的是配置而不是「请求是不是 https」，
    因为它要在设置 Cookie 的那一刻决定，而那一刻拿不到可靠的前置信息——
    反代后面 uvicorn 看到的是 http，靠 X-Forwarded-Proto 判断则取决于反代配没配对。

    ⚠️ 部署时的坑：app_env 默认值是 development，所以忘了把 APP_ENV 设成
    production 的话，线上会静默地发出不带 Secure 的 Cookie。这是本项目里
    「配置写错不会有任何报错、只会悄悄更不安全」的地方之一。
    """
    return settings.app_env == "production"


def set_session_cookie(response: Response, token: str) -> None:
    """把令牌写进 Cookie。token 是原文，只在这里出现这一次。"""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        # 和库里 auth_sessions.expires_at 同源（都来自 SESSION_TTL），
        # 不会出现「浏览器还留着、服务端已经不认」的错位。
        max_age=SESSION_TTL_SECONDS,
        # HttpOnly：JS 读不到（document.cookie 取不出来），XSS 就拿不走令牌。
        # 这是本项目最主要的令牌保护措施，不要为了前端方便去掉它。
        httponly=True,
        # SameSite=Lax：跨站发起的 POST/PUT/DELETE 不会带上这个 Cookie，
        # 于是别的站点没法借用户的身份调我们的写接口——这就是本项目的 CSRF 防线。
        # 选 Lax 而不是 Strict：Strict 会让「从外部链接点进本站」的首次访问
        # 不带 Cookie，用户会看到自己莫名其妙是未登录状态。
        samesite="lax",
        secure=_is_secure(),
        # 必须是 "/"。前端页面路径是 /workspaces/xxx 之类的深层路径，
        # 只作用在 /api 下的话同源页面照样能带上（Cookie 的 path 只看请求路径），
        # 但写死 "/" 可以省掉「以后把接口挪到别的前缀下就掉登录」这类问题。
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """删除 Cookie。

    浏览器是按 (name, domain, path) 三元组匹配删除的，path 必须和设置时一致，
    写错的话现象是「退出了但 Cookie 还在」——服务端已经删了会话行所以接口是对的，
    但用户下次请求还会带上一个死令牌。
    """
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=_is_secure(),
        path="/",
    )
