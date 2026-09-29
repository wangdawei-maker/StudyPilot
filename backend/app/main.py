from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging_setup import setup_logging
from app.core.request_id import REQUEST_ID_HEADER, RequestIDMiddleware

setup_logging()

app = FastAPI(
    title="StudyPilot API",
    version="0.1.0",
    description="StudyPilot learning platform API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
    # 跨域下 JS 默认读不到自定义响应头。前端报错时要把 request_id 显示出来，
    # 用户截图给我们才能对上服务端日志（FR-EVAL-05），所以必须显式放开。
    expose_headers=[REQUEST_ID_HEADER],
)

# 放在最后 = 最外层。Starlette 的中间件是「后添加的在外层」，
# 这样连 CORS 拒绝的请求也会带上 X-Request-ID，排查时不会出现没有 id 的响应。
app.add_middleware(RequestIDMiddleware)

register_exception_handlers(app)

# API-01：业务接口统一挂在 /api/v1 下。/health 刻意留在根路径，
# 它是给容器和负载均衡探活用的，不该跟着业务接口一起改版本。
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "studypilot-api"}
