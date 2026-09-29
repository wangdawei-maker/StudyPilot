"""v1 路由汇总。

每加一个功能模块（workspaces、documents、conversations…），在这里 include 一行。
`/api/v1` 这个前缀只在 app/main.py 里写一次（API-01）。
"""

from fastapi import APIRouter

from app.api.v1 import auth

api_router = APIRouter()
api_router.include_router(auth.router)
