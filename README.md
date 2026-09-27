# StudyPilot

面向 AI 应用开发学习者的个性化学习与复习平台。当前是项目初始化阶段，先搭建前后端开发环境和基础服务。

## 目录

```text
backend/   FastAPI 服务
frontend/  React + TypeScript + Vite 前端
docker-compose.yml  PostgreSQL 与 Redis
```

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
python -m pip install -e .
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

如果本机没有 Python 3.13，将 `py -3.13` 改为已安装的 Python 3.11 或 3.12 版本。

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
- 登录、workspace、数据库模型和迁移将在后续开发日实现。
