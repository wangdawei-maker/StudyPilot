"""日志配置（FR-EVAL-05：所有日志携带 request_id）。

文件名带 `_setup` 后缀是为了躲开 stdlib 的 `logging`：同包内叫 logging.py 的话，
`from app.core import logging` 这类写法迟早会有人写错，不值得留这个坑。
"""

import logging
from logging.config import dictConfig

from app.core.request_id import get_request_id

# 没有请求上下文时（Celery worker、启动期、脚本）用这个占位。
# 用占位符而不是让字段缺省，是为了让格式串恒定——否则 formatter 会直接抛 KeyError。
NO_REQUEST_ID = "-"


class RequestIDFilter(logging.Filter):
    """给每条 LogRecord 挂上当前请求的 id。

    必须挂在 handler 上，不能挂 logger 上：Logger.callHandlers 只对**记录产生处**
    那个 logger 应用 filter，往上传给父 logger 的 handler 时不再过父 logger 的
    filter。挂在 `app` logger 上，`app.services.auth` 发出的日志就带不上 id。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or NO_REQUEST_ID
        return True  # 只做标注，不过滤任何记录


LOG_CONFIG = {
    "version": 1,
    # False 才不会把 uvicorn 自己的 logger 关掉。uvicorn 的 logger 自带 handler
    # 且 propagate=False，所以这里改 root 不影响它的输出格式。
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "app.core.logging_setup.RequestIDFilter"},
    },
    "formatters": {
        "default": {
            "format": "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "filters": ["request_id"],
            "stream": "ext://sys.stderr",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    # 把 uvicorn 自己的 logger 也收编进来。
    #
    # uvicorn 默认给这几个 logger 各配了 handler 且 propagate=False，我们的过滤器
    # 挂在自己的 handler 上，够不着它们，于是访问日志里没有 request_id——
    # 而那恰恰是最需要 id 的一行：并发时只有它能和错误日志对上号（FR-EVAL-05）。
    #
    # 清空它们自带的 handler 并打开 propagate，日志就会走 root 的 console handler，
    # 格式和 request_id 都统一了。代价是失去 uvicorn 默认的彩色输出。
    #
    # 注意：这段会覆盖 `uvicorn --log-config` 指定的配置。真要自定义 uvicorn 日志，
    # 应该改这里，而不是两边各写一份。
    "loggers": {
        "uvicorn": {"handlers": [], "propagate": True, "level": "INFO"},
        "uvicorn.error": {"handlers": [], "propagate": True, "level": "INFO"},
        "uvicorn.access": {"handlers": [], "propagate": True, "level": "INFO"},
    },
}


def setup_logging() -> None:
    """在进程启动时调用一次。重复调用是幂等的（dictConfig 会整体替换配置）。"""
    dictConfig(LOG_CONFIG)
