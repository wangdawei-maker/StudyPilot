"""从数据库完整性错误里认出「是哪条约束挂了」。

只有一个知识点，但它足够反直觉，值得单独成文件：

**asyncpg 的异常不在 `IntegrityError.orig` 上，而在它的 `__cause__` 上。**
`exc.orig` 是 SQLAlchemy 的适配层异常（`AsyncAdapt_asyncpg_dbapi.IntegrityError`），
它只转发 `sqlstate` / `pgcode` / `args`，**不带** `constraint_name`；真正的 asyncpg
异常挂在 `exc.orig.__cause__`。（第 3 天在注册接口的竞态分支上实测确认过。）

写错的表现不是报错，而是「判断永远为 False」：错误码静默退化成兜底分支，
测试退化成「什么都没验证」。所以这个洞只需踩一次。
"""

from typing import Any

from sqlalchemy.exc import IntegrityError

# PostgreSQL 的 SQLSTATE。
NOT_NULL_VIOLATION = "23502"


def raw_db_error(exc: IntegrityError) -> Any:
    """取出驱动层的原始异常（asyncpg 的那些）。"""
    return getattr(exc.orig, "__cause__", None) or exc.orig


def constraint_name_of(exc: IntegrityError) -> str | None:
    """这次违反的是哪条具名约束；不是约束类错误则返回 None。

    **NOT NULL 违反走的是另一条路**，这里会返回 None：asyncpg 的
    `NotNullViolationError` 没有 `constraint_name`，列名在 `column_name` 上。
    要断言 NOT NULL 请用 `raw_db_error` 配 NOT_NULL_VIOLATION。
    """
    return getattr(raw_db_error(exc), "constraint_name", None)
