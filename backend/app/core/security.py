"""密码哈希与会话令牌（FR-AUTH-02 / FR-AUTH-04 / FR-AUTH-10）。

这个模块是整个后端唯一碰得到明文密码和令牌原文的地方。两条贯穿全项目的约定：

- **明文只在函数参数里存在**：不写日志、不进异常消息、不放进任何返回值或数据结构。
- **出这个模块的只有哈希**：调用方拿到的 `password_hash` 是 Argon2 串，
  拿到的 `token_hash` 是摘要；原文由调用方当场用掉（写进 Cookie），绝不落库。

两种哈希用不同的算法，是有意的：
- 密码用 Argon2id（慢哈希）。密码是人选的，熵低，只能靠单次计算成本来拖住爆破。
- 令牌用 SHA-256（快哈希）。令牌是 32 字节密码学随机数，熵足够，不存在爆破风险，
  而认证路径上的每次请求都要算一次，付不起慢哈希的代价（FR-AUTH-10）。
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings

# FR-AUTH-01：密码长度不少于 8 位。
PASSWORD_MIN_LENGTH = 8
# 上限不是为了安全强度，是为了防止有人拿一个几百 MB 的「密码」来让我们做哈希，
# 那是白白烧 CPU 和内存。Argon2 不像 bcrypt 那样在 72 字节处截断，
# 不设上限就等于把请求体大小直接变成计算量。
PASSWORD_MAX_LENGTH = 128

# 会话令牌的原始随机字节数。32 字节 = 256 位，远超暴力枚举的范围。
SESSION_TOKEN_BYTES = 32

# 会话有效期（FR-AUTH-06）。抽成常量是因为它有两个消费方：库里 auth_sessions.expires_at
# 和 Cookie 的 Max-Age（app/api/cookies.py）。两处各算各的话，只要有人改了
# session_ttl_days 而漏改另一处，就会出现「Cookie 还在、但服务端已经不认」或者
# 反过来「浏览器已经丢掉、服务端还留着一条永远用不到的行」。
SESSION_TTL = timedelta(days=settings.session_ttl_days)
SESSION_TTL_SECONDS = int(SESSION_TTL.total_seconds())

# 参数显式写出来，而不是吃库的默认值：这几个数就是密码存储的安全强度，
# 应该一眼可见、集中可调。数值取自 argon2-cffi 的默认值，
# 也高于 OWASP 对 Argon2id 的最低建议（m=19MiB, t=2, p=1）。
#
# 调大这几个参数前先想清楚内存：单次哈希占用约 memory_cost KiB，
# 并发 N 个登录请求就是 N × memory_cost 的内存峰值。
_password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    """算密码哈希。返回的串里已经带了盐和参数，直接入库即可。"""
    return _password_hasher.hash(password)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """一个固定的假哈希，只用来「陪跑」。

    第一次用到时才生成，避免 import 时就白付 80ms。用随机明文而不是写死一个
    常量，是为了保证没人能拿这个明文去登录——它对应的账号不存在。
    """
    return _password_hasher.hash(secrets.token_urlsafe(SESSION_TOKEN_BYTES))


def verify_password(password: str, password_hash: str | None) -> bool:
    """校验密码。

    `password_hash` 传 None 表示「账号不存在」。这时仍然对着假哈希跑一遍完整的
    校验，而不是直接返回 False —— 直接返回会让未注册邮箱的登录响应快上几十毫秒，
    这个时间差足够拿来枚举哪些邮箱注册过（FR-AUTH-04）。
    """
    # 库里存了非法哈希（历史脏数据、手工改库）时不该让接口 500，
    # 当作校验失败即可。注意 InvalidHashError 继承的是 ValueError，
    # 不在 Argon2Error 之下，只捕 VerificationError 会漏掉它。
    target = _dummy_hash() if password_hash is None else password_hash
    try:
        matched = _password_hasher.verify(target, password)
    except (VerificationError, InvalidHashError):
        matched = False

    # 再 and 一道 password_hash is not None 是保险：假哈希理论上不可能被验过，
    # 但万一哪天有人把它换成一个已知口令的哈希，也不该让「不存在的账号」登录成功。
    return matched and password_hash is not None


def generate_session_token() -> str:
    """生成会话令牌原文。只在登录时调用一次，之后只存在于 Cookie 里。"""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """算令牌摘要。存库的是它，查会话也是拿它去查（FR-AUTH-10）。

    用 SHA-256 而不是 Argon2 是有意的：令牌是 32 字节随机数，熵足够，
    没有必要在每次请求的认证路径上付慢哈希的代价。快哈希在这里不降低安全性，
    因为攻击者拿到摘要也无法反推出原文。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expires_at(now: datetime | None = None) -> datetime:
    """会话到期时刻（FR-AUTH-06）。

    统一用带时区的 UTC。库里是 timestamptz，naive datetime 会在比较时踩坑。
    """
    return (now or datetime.now(UTC)) + SESSION_TTL
