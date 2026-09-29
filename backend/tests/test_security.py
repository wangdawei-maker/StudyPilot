"""密码哈希与会话令牌（FR-AUTH-02 / FR-AUTH-04 / FR-AUTH-10）。"""

import time
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher

from app.core import security
from app.core.config import settings
from app.db.models import AuthSession, User

PASSWORD = "correct horse battery staple"


class _CountingHasher:
    """套在真 hasher 外面数调用次数。

    不能直接 monkeypatch 到 `_password_hasher.verify` 上：argon2-cffi 25 的
    PasswordHasher 是编译扩展类型，实例属性是只读的。只能把模块级的
    `_password_hasher` 整个换掉。
    """

    def __init__(self, inner: PasswordHasher) -> None:
        self.inner = inner
        self.calls: list[str] = []

    def hash(self, password: str) -> str:
        return self.inner.hash(password)

    def verify(self, hash_: str, password: str) -> bool:
        self.calls.append(hash_)
        return self.inner.verify(hash_, password)


def test_hash_is_not_the_plaintext() -> None:
    """FR-AUTH-02：库里存的不能是明文。"""
    digest = security.hash_password(PASSWORD)

    assert PASSWORD not in digest
    assert digest != PASSWORD


def test_same_password_hashes_differently_each_time() -> None:
    """盐是随机的，否则相同密码会产生相同哈希，一眼看出谁和谁密码一样。"""
    first = security.hash_password(PASSWORD)
    second = security.hash_password(PASSWORD)

    assert first != second
    assert security.verify_password(PASSWORD, first)
    assert security.verify_password(PASSWORD, second)


def test_wrong_password_does_not_verify() -> None:
    digest = security.hash_password(PASSWORD)

    assert security.verify_password("wrong password", digest) is False
    assert security.verify_password("", digest) is False
    # 大小写敏感，不能靠大小写不敏感的比较蒙混过关
    assert security.verify_password(PASSWORD.upper(), digest) is False


def test_hash_fits_the_column() -> None:
    """哈希串长度必须塞得进 users.password_hash。

    Argon2 串的长度随参数变化，改参数（尤其 hash_len / salt_len）时可能悄悄超长，
    那时候报错会发生在写库那一刻，离改动点很远。这里提前卡住。
    """
    digest = security.hash_password(PASSWORD)
    column_length = User.__table__.c.password_hash.type.length

    assert len(digest) <= column_length, (
        f"Argon2 串 {len(digest)} 字符，超过 password_hash 的 {column_length}"
    )


def test_missing_account_still_performs_a_hash_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-AUTH-04 的机制：账号不存在时也要真跑一次校验。

    用计数而不是计时来断言：「查不到用户就提前 return」这种退化会留下
    零次校验的痕迹，而计数不受机器负载影响，不会变成随机失败的计时用例。
    """
    spy = _CountingHasher(security._password_hasher)
    monkeypatch.setattr(security, "_password_hasher", spy)

    assert security.verify_password(PASSWORD, None) is False
    assert len(spy.calls) == 1, "账号不存在时没有跑哈希校验，响应会明显更快，能被用来枚举账号"

    # 对照组：账号存在（密码错）时同样只跑一次。两边工作量一致，时间差才被抹平。
    spy.calls.clear()
    assert security.verify_password("wrong", security.hash_password(PASSWORD)) is False
    assert len(spy.calls) == 1


def test_missing_account_and_wrong_password_take_comparable_time() -> None:
    """上面那条断言的是机制，这条断言的是结果：两条路径耗时要接近。

    阈值放得很宽（一半即可）——两条路径本来就在做同一件事，实测差异在几个百分点内。
    留这么大余量是因为这里是真实的 CPU 计时，会受机器负载影响；
    它要抓的是「快了一个数量级」这种退化，不是几个百分点的抖动。
    """
    digest = security.hash_password(PASSWORD)
    security.verify_password(PASSWORD, None)  # 预热：_dummy_hash 是懒生成的

    def median_seconds(fn, rounds: int = 3) -> float:
        samples = []
        for _ in range(rounds):
            start = time.perf_counter()
            fn()
            samples.append(time.perf_counter() - start)
        return sorted(samples)[len(samples) // 2]

    existing = median_seconds(lambda: security.verify_password("wrong", digest))
    missing = median_seconds(lambda: security.verify_password("wrong", None))

    assert missing >= existing * 0.5, (
        f"账号不存在时只花了 {missing * 1000:.1f}ms，"
        f"账号存在时花了 {existing * 1000:.1f}ms，差距足以用来枚举账号（FR-AUTH-04）"
    )


def test_corrupt_stored_hash_is_rejected_not_raised() -> None:
    """库里存了非法哈希（脏数据、手工改库）时应当校验失败，而不是让接口 500。"""
    assert security.verify_password(PASSWORD, "not-an-argon2-hash") is False


def test_session_token_is_random_and_long_enough() -> None:
    tokens = {security.generate_session_token() for _ in range(50)}

    assert len(tokens) == 50, "令牌出现重复，随机源有问题"
    assert all(len(t) >= 40 for t in tokens)


def test_token_digest_matches_the_column_and_hides_the_token() -> None:
    """FR-AUTH-10：库里只存摘要。摘要长度还得对得上 auth_sessions.token_hash。"""
    token = security.generate_session_token()
    digest = security.hash_session_token(token)

    assert token not in digest
    assert digest != token
    assert len(digest) == AuthSession.__table__.c.token_hash.type.length
    assert all(c in "0123456789abcdef" for c in digest)


def test_token_digest_is_deterministic() -> None:
    """查会话是拿摘要去查的，同样的令牌必须给出同样的摘要。"""
    token = security.generate_session_token()

    assert security.hash_session_token(token) == security.hash_session_token(token)
    assert security.hash_session_token(token) != security.hash_session_token(
        security.generate_session_token()
    )


def test_session_expiry_is_aware_and_matches_the_configured_ttl() -> None:
    """库里是 timestamptz，naive datetime 在比较时会踩坑，所以必须是带时区的。"""
    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    expires_at = security.session_expires_at(now)

    assert expires_at.utcoffset() == timedelta(0), "应当用 UTC，不是本地时区"
    assert expires_at - now == timedelta(days=settings.session_ttl_days)
    assert settings.session_ttl_days == 7, "FR-AUTH-06：默认 7 天"
