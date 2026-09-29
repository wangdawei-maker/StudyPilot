"""认证接口的请求与响应模型。

这一层负责「形状和格式」：字段是否存在、长度够不够、格式对不对。
业务规则（邮箱是否已被占用、空间怎么建）不在这里。
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH

# 与 users.display_name 的 String(50) 对齐。
DISPLAY_NAME_MAX_LENGTH = 50


class _EmailPayload(BaseModel):
    """注册和登录共用的邮箱字段。

    抽出来不只是为了少写几行：邮箱归一化必须只有一处实现。两份各写各的话，
    哪天有人只改了注册那份，就会出现「注册时转了小写、登录时没转」——
    用户明明输对了邮箱却登不进去，而且注册和登录的代码看起来都是对的。
    """

    email: EmailStr

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        """统一转小写。

        users 表上有 CHECK (email = lower(email))，不转会被数据库直接拒绝。
        光靠 EmailStr 不够：它只把域名部分转成小写，本地部分原样保留，
        所以 Alice@Example.com 会带着大写 A 走到插入那一步。

        严格按 RFC 5321，本地部分是区分大小写的；但现实中没有任何邮件服务商这么做，
        而两种写法落在两个账号上才是真正的问题。
        """
        return value.lower()


class RegisterRequest(_EmailPayload):
    # min_length 来自 FR-AUTH-01（不少于 8 位），上限来自 security.py。
    # 密码既不做 strip 也不做其他规范化：空格是合法字符，悄悄改掉用户的密码会造成
    # 「注册时被改过、登录时没被改」——用户输对了却登不上，而且极难排查。
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    # FR-AUTH-01 只要求邮箱和密码，所以显示名可选。
    display_name: str | None = Field(default=None, max_length=DISPLAY_NAME_MAX_LENGTH)

    @field_validator("display_name")
    @classmethod
    def _blank_name_to_none(cls, value: str | None) -> str | None:
        """把空串和纯空白当成「没填」，而不是存一个空字符串进库。

        前端把输入框清空后往往提交的是 ""，存进去之后界面上会显示成空白，
        和真正没设置过的用户在数据上无法区分。
        """
        if value is None:
            return None
        return value.strip() or None


class LoginRequest(_EmailPayload):
    """登录请求。

    密码这里刻意**不设 min_length**，和注册不同。理由有两条：

    - 「密码不足 8 位」在注册时是格式问题（我们定的规则），在登录时不是——
      它就是一次失败的登录尝试，应当走 401 和统一的「邮箱或密码不正确」，
      而不是 422 附带一条「密码太短」的字段级提示。
    - 长度规则只在注册处定义一次就够了。登录再写一遍，两处就会各自演化，
      而登录侧的规则一旦比注册侧更严，合法用户会被挡在门外。

    但 max_length 必须保留，而且这里比注册更关键：注册给密码设上限是为了
    防止有人拿超长字符串烧 CPU，登录同样要过一遍 Argon2，同一个洞不能只堵一半。
    """

    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class UserResponse(BaseModel):
    """用户的对外表示。

    刻意不含 password_hash：FR-AUTH-02 要求接口响应里既不能有明文密码，
    也不能有哈希。用独立的响应模型而不是直接把 ORM 对象丢出去，
    就是为了让「哪些字段能被看到」是一个显式决定，而不是字段加漏了也没人发现。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str | None
    created_at: datetime
