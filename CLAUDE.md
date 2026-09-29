# StudyPilot

面向「AI 应用开发学习者」的个性化学习与复习平台。个人作品集项目，C 端产品。
模块化单体（不是微服务）：React + TypeScript 前端，FastAPI + SQLAlchemy 后端，PostgreSQL + pgvector，Redis + Celery。

## 权威文档

- `docs/需求文档.md` —— 需求唯一来源。功能需求 `FR-*`、非功能 `NFR-*`、数据需求 `DR-*`。
  **写代码和提交信息时引用这些编号**，别另起一套说法。
- `planandconversation/StudyPilot项目计划.md` —— 周次计划。
- `planandconversation/历史对话.md` —— 历史对话存档。

## 常用命令

后端（工作目录 `backend/`，venv 在 `backend/.venv`，Python 3.13）：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
python -m pytest
python -m ruff check .

python -m alembic upgrade head
python -m alembic current
python -m alembic revision --autogenerate -m "add xxx"
python -m alembic downgrade -1
```

基础设施（工作目录为仓库根目录）：

```powershell
docker compose up -d postgres redis
docker compose ps
```

前端（工作目录 `frontend/`）：`npm run dev`（127.0.0.1:5173）、`npm run build`。

## 环境约定（这几条最容易踩）

- **依赖只声明在 `backend/pyproject.toml`**，没有 requirements.txt。运行时依赖在
  `dependencies`，开发依赖在 `dev` extra。
- **`.env` 在仓库根目录，不在 `backend/` 下**。因为 `docker-compose.yml` 在根目录，
  compose 的变量替换只认它旁边的 `.env`。后端从 `backend/` 启动，所以
  `app/core/config.py` 用 `Path(__file__).resolve()` 显式解析到根目录，
  不依赖当前工作目录。
- **改了 `.env` 必须重启服务**。`settings` 是模块级单例，在 import 时构造；
  `uvicorn --reload` 只监听 `.py`，不监听 `.env`。
- **`backend/alembic.ini` 必须保持纯 ASCII 内容**。Alembic 用系统 locale 编码读它
  （中文 Windows 上是 GBK），configparser 又不支持文件内声明编码，写中文会直接
  导致 `UnicodeDecodeError`。中文说明一律写在 `alembic/env.py` 里。
- **数据库连接串不在 `alembic.ini`**，由 `alembic/env.py` 从 `.env` 注入。
  `alembic.ini` 里那份是占位符，作用是让「env.py 没生效」立刻暴露。
- `backend/.venv` 是在 `--without-pip` 下创建的。当前 pip 已通过
  `python -m ensurepip --upgrade --default-pip` 引导好了（26.1.2）；
  若哪天重建 venv 后发现 `No module named pip`，用同一条命令引导即可。

## 代码约定

- `app/core/config.py` 是配置唯一入口，新增配置项加到 `Settings` 里，不要散落 `os.getenv`。
- `app/db/base.py` 定义 `Base`、`UUIDPrimaryKeyMixin`、`TimestampMixin` 和约束命名规范。
  **命名规范必须在第一次迁移之前定好**，之后改要手工重命名已有约束。
- 表定义集中在 `app/db/models.py`。
- 会话用 `app/db/session.py` 的 `get_session` 依赖；事务边界由接口自己控制，
  依赖只负责开关会话。
- **异步下不要依赖懒加载**：模型暂时没有 `relationship()`，是有意的——异步里访问
  未加载的关联属性会抛 `MissingGreenlet`。需要关联时再加，并带上 `selectinload`。
- `pyproject.toml` 里 ruff 的 `src = []` 和 `known-first-party = ["app"]` 要一起写，
  少一个都不起作用（`backend/alembic/` 目录会被误判为第一方模块）。
- `alembic/versions/` 不参与 lint（生成产物，空迁移必然触发 F401/PIE790）。

## 已定下的设计决策

理由写在代码注释里，这里只列结论和位置：

- **UUID 主键**（`app/db/base.py`）：C 端产品，自增 id 可被顺序遍历。
- **`workspaces.owner_id` 必须存在**（`app/db/models.py`）：DR-06 要求删用户级联删空间，
  需要一条从 `workspaces` 直达 `users` 的外键。
  ⚠️ 已知弱点：它和 `workspace_members` 里 `role='owner'` 的行**冗余**，理论上会不一致。
  部分唯一索引只挡住「两个 owner 行」。维持一致只能靠应用层在**同一个事务**里写两处——
  实现 FR-AUTH-08（注册建个人空间）时要封装成一个函数。
- **`email` 有小写 CHECK 约束**：Postgres 比较区分大小写，不归一化会一个邮箱两个账号。
- **`workspace_members.role` 只有 `owner` / `member`**（DR-03），取值 SQL 从常量生成。
- **会话令牌只存 SHA-256 摘要**（FR-AUTH-10）。用快哈希而非 Argon2 是有意的：
  令牌是 32 字节随机数，熵足够，不必在每次请求的认证路径上付慢哈希代价。
- **密码哈希用 `argon2-cffi`**，不用 passlib（已停维护，且与 bcrypt>=4.1 不兼容）。

## 协作方式

- 单人开发，按天推进，每天结束时提交到 `main`。
- 提交信息用中文，格式 `feat(dayN): ...` / `chore(dayN): ...`，正文列改动清单。
- 需求文档第 14 节列了 6 个**待确认问题**（例如邀请码 FR-WS-05 是否提前到 P1），
  动手实现对应功能前先翻一下。

## 当前进度

- **第 1 天**：前端 + 后端骨架、依赖声明、`/health` 探针、需求文档。
- **第 2 天**：配置层、数据库基类与四张表（`users`、`workspaces`、`workspace_members`、
  `auth_sessions`）、Alembic 接入、首个迁移 `2a5c41bae41c`。
- **第 3 天**：认证全链路（FR-AUTH-01~10）+ 测试基础设施。
  - `app/core/`：`errors.py`（API-02 统一错误体 + 全局处理器）、`request_id.py`
    （纯 ASGI 中间件，FR-EVAL-05）、`logging_setup.py`、`security.py`
    （Argon2 密码 + SHA-256 会话令牌）。
  - `app/api/`：`v1/auth.py`（register / login / logout / me）、`cookies.py`
    （Cookie 策略）、`deps.py`（`get_current_user`）。
  - `app/services/`：`auth.py`、`workspaces.py`（FR-AUTH-08 双写的唯一入口）。
  - `app/db/integrity.py`：从 `IntegrityError` 认约束名（asyncpg 的坑在
    `exc.orig.__cause__` 上，不在 `exc.orig`）。
  - 测试：`tests/conftest.py`（测试库 fixture，**每轮清空 schema 再迁移**）、
    `test_db_constraints.py`（27 项约束回归）、`test_migrations.py`
    （漂移检查 + 降级往返），全套 100 个用例。
  - 需求文档第 14 节的 6 个待确认问题**尚未处理**，实现 FR-WS-05 前要翻。
- **下一步（第 4 天）**：学习空间接口与权限判定（FR-WS-01~04），统一开发环境主机，
  前端认证实现。
