# StudyPilot

面向 AI 应用开发学习者的个性化学习与复习平台。当前是项目初始化阶段，先搭建前后端开发环境和基础服务。

## 目录

```text
backend/   FastAPI 服务
frontend/  React + TypeScript + Vite 前端
docs/      需求文档
planandconversation/  项目计划与历史对话
docker-compose.yml  PostgreSQL 与 Redis
```

## 文档

- [需求文档](docs/需求文档.md)：功能需求、非功能需求、验收标准与范围边界
- [项目计划](planandconversation/StudyPilot项目计划.md)：技术方案与六周排期

## 环境要求

- Node.js 20.19+ 或 22.12+
- Python 3.11+
- Docker Desktop，支持 Docker Compose

## 首次启动

在项目根目录运行：

```powershell
Copy-Item .env.example .env
docker compose up -d postgres redis
```

启动后端（PowerShell）：

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

如果本机没有 Python 3.13，将 `py -3.13` 改为已安装的 Python 3.11 或 3.12 版本。

依赖统一声明在 `backend/pyproject.toml` 的 `dependencies` 和 `dev` extra 中，没有单独的 requirements 文件。`-e` 是可编辑安装，改完代码不用重装；只装运行时依赖时去掉 `[dev]` 即可。

依赖按“首次需要的开发周”分组。第 1 周只需要第一组（fastapi、uvicorn、pydantic、sqlalchemy、asyncpg、alembic、argon2-cffi、email-validator），其余在对应周次才使用。

运行测试与代码检查：

```powershell
python -m pytest
python -m ruff check .
```

数据库迁移（Alembic，需要 PostgreSQL 已启动）：

```powershell
cd backend
python -m alembic upgrade head                            # 应用全部迁移
python -m alembic current                                 # 查看当前版本
python -m alembic revision --autogenerate -m "add xxx"    # 改完模型后生成迁移
python -m alembic downgrade -1                            # 回退一个版本
```

迁移脚本位于 `backend/alembic/versions/`。连接串不在 `alembic.ini` 里，而是由 `alembic/env.py` 从根目录 `.env` 读取，避免密码进入版本库。

**`backend/alembic.ini` 必须保持纯 ASCII 内容。** Alembic 以系统 locale 编码读取该文件（中文 Windows 上是 GBK），configparser 又不支持文件内声明编码，写入任何中文都会直接导致 `UnicodeDecodeError`。中文说明写在 `alembic/env.py` 中。

启动前端（另开一个终端）：

```powershell
cd frontend
npm install
npm run dev
```

访问：

- 前端：http://localhost:5173
- API 健康检查：http://127.0.0.1:8000/health
- API 文档：http://127.0.0.1:8000/docs

## 常用命令

```powershell
docker compose ps
docker compose logs -f postgres
docker compose down
```

`docker compose down` 会停止并移除容器，但保留数据库卷。不要使用 `docker compose down -v`，它会删除本地数据库数据卷。

## 当前范围

- 已建立前端与后端最小骨架；
- 已提供 PostgreSQL、Redis 本地开发服务；
- 已提供 `/health` 探针；
- 已建立身份与学习空间的四张表（`users`、`workspaces`、`workspace_members`、`auth_sessions`）及第一个 Alembic 迁移；
- 注册、登录与 workspace 接口将在后续开发日实现。
