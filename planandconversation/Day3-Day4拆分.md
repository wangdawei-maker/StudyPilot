# Day 3 + Day 4 拆分

这份拆分原先只存在于对话里。上下文压缩后为了找回它翻了 6 MB 的 transcript，
所以落到文件里。

每步末尾的「**实测**」是完成之后回填的，记录了与当时预估的差异。原估的价值不在于准，
而在于当时就想清楚了依赖顺序和验证方式——那部分不会因为工时估算失准而失效。

---

## Day 3 —— 后端认证闭环（已完成）

| 步 | 内容 | 原估 | 状态 |
| --- | --- | --- | --- |
| 1 | 测试基建 | 2h | ✅ |
| 2 | 统一错误格式 | 1.5h | ✅ |
| 3 | security 模块 | 1h | ✅ |
| 4 | 注册接口 | 2h | ✅ |
| 5 | 登录 / 登出 / me | 2h | ✅ |
| 6 | 约束测试沉淀 | 1h | ✅ |

### 步骤 1：测试基建 ⭐ 建议从这里开始

**做什么**：`tests/conftest.py`——测试库配置、建库跑迁移、savepoint 事务回滚、`dependency_overrides[get_session]`。

**产出**：`Settings.test_database_url`、session 级 fixture（建库+`alembic upgrade head`）、function 级 fixture（每用例一个 SAVEPOINT，结束回滚）、`client` fixture。

**验证**：写一个探针测试——插一行、断言存在；再插同 email 的第二行、断言约束报错。然后**连跑两次**，第二次仍全绿才说明回滚真的生效（这是最容易假通过的地方）。

**依赖**：Postgres 跑着 · **约 2h**

**实测**：产出与预估一致。额外加了一条：每轮测试开始前 `DROP SCHEMA public CASCADE` 再迁移。
起因是后来发现降级用例一旦失败，会留下「版本表说在 base、表却还在」的矛盾状态，
下一轮 upgrade 撞上 `relation "users" already exists`，整个套件以看不懂的错误开头，
把真正的原因盖住。代价约 0.2 秒。

**另外**：`join_transaction_mode="create_savepoint"` 在当前代码里是**冗余的**——
SQLAlchemy 默认的 `conditional_savepoint` 在连接已开事务时会解析成同样的行为（实测去掉仍全绿）。
保留它只是为了不依赖「上面那行 `connection.begin()` 必须存在」这个隐含前提。

---

### 步骤 2：统一错误格式

**做什么**：`app/core/errors.py` + request_id 中间件。错误码枚举（`EMAIL_ALREADY_REGISTERED`、`INVALID_CREDENTIALS`、`UNAUTHENTICATED`…）、`AppError` 基类、三个全局处理器（`AppError` / `RequestValidationError` / 未捕获异常）。

**产出**：所有错误响应统一为 `{code, message, details, request_id}`（API-02），`code` 稳定可供前端判断（API-03）。

**验证**：打一个不存在的路径看 404 结构；故意触发一次校验错误看 422；确认响应头/体里有 request_id。

**依赖**：无 · **约 1.5h**

关键决策点：**未捕获异常不能让堆栈泄漏到响应里**，但必须完整记进日志，且日志里要带上同一个 request_id。

**实测**：拆成了 `errors.py` + `request_id.py` + `logging_setup.py` 三个文件。
- 错误码名字与上面草图不同，实际是 `AUTH_EMAIL_TAKEN` / `AUTH_INVALID_CREDENTIALS` /
  `AUTH_REQUIRED`——加 `AUTH_` 前缀是为了让领域码成组、不与通用码混淆。
- request_id 中间件必须写成**纯 ASGI**，不能用 `BaseHTTPMiddleware`：后者在单独的任务里
  跑下游，在里面绑定的 contextvar 传不回来。
- 全局处理器有 **4 个**不是 3 个，多出来的是 `StarletteHTTPException`（不接管的话
  404 的响应体是 `{"detail": "Not Found"}`，形状对不上 API-02）。
- 校验错误的 details 刻意剥掉 pydantic 的 `input` 字段，否则密码校验失败时明文密码
  会原样出现在响应里（FR-AUTH-02）。
- 踩过的坑：`alembic/env.py` 的 `fileConfig()` 默认 `disable_existing_loggers=True`，
  进程内跑迁移会把应用 logger 全部静默禁用。已在 env.py 里显式关掉。

---

### 步骤 3：security 模块

**做什么**：`app/core/security.py`——Argon2 哈希与校验、会话令牌生成（32 字节随机）、令牌 SHA-256 摘要。

**产出**：`hash_password` / `verify_password` / `generate_session_token` / `hash_session_token`。

**验证**：单元测试——同一密码两次哈希结果不同（盐生效）、正确密码通过错误密码拒绝、令牌摘要稳定且与原文不同。这类纯函数测试不需要数据库。

**依赖**：无 · **约 1h**

**实测**：与预估一致。两处补充：
- `SESSION_TTL` / `SESSION_TTL_SECONDS` 也放在这里，作为库里 `expires_at` 和 Cookie 的
  `Max-Age` 的唯一来源——两处各算各的话，改一处漏一处就会出现「Cookie 还在、服务端不认」。
- 坑：argon2-cffi 25 的 `PasswordHasher.verify` 是编译扩展，实例属性只读，
  测试里没法 patch 实例，只能 patch 模块属性。

---

### 步骤 4：注册接口

**做什么**：`app/schemas/auth.py`、`app/services/auth.py`、`POST /api/v1/auth/register`。

**产出**：注册接口。**重点是把 FR-AUTH-08 的双写封装成一个函数**——`workspaces.owner_id` 和 `workspace_members` 里 `role='owner'` 的行必须同一个事务写入，这是 CLAUDE.md 里标注的已知弱点，散在接口里写迟早写漏一处。

**验证**：注册成功；重复邮箱返回稳定错误码（含大小写不同的同一邮箱）；密码不足 8 位被拒；注册后个人空间和 owner 成员行**都在**。

**依赖**：步骤 1、2、3 · **约 2h**

**实测**：双写封装在 `app/services/workspaces.py::create_workspace_with_owner`，与预估一致。
- 竞态兜底比预估麻烦：`IntegrityError` 里认约束名时，asyncpg 的异常挂在
  `exc.orig.__cause__` 上而不是 `exc.orig`（后者是 SQLAlchemy 的适配层异常，
  只转发 sqlstate，不带 `constraint_name`）。抽成了 `app/db/integrity.py`。
- 约束名不写死字符串，从 `User.__table__.constraints` 里现取，命名规范一改就 import 时报错，
  而不是静默退化成 500。
- 注册后**不自动登录**（FR-AUTH-05 把写 Cookie 定义在登录上），这是个产品决定，已记在路由注释里。

---

### 步骤 5：登录 / 登出 / me

**做什么**：`POST /login`、`POST /logout`、`GET /me`，以及 `get_current_user` 依赖（读 Cookie、校验令牌、查过期）。

**产出**：完整的认证闭环 + Cookie 属性（`HttpOnly`、`SameSite=Lax`、按 `app_env` 决定 `Secure`）。

**验证**：注册→登录→me 通→登出→me 返回 401；伪造令牌 401；手工把 `expires_at` 改到过去再请求，返回 401。

**依赖**：步骤 4 · **约 2h**

这里有个容易漏的点：**FR-AUTH-04 要求登录失败不泄露账号是否存在**，光统一错误信息不够——邮箱不存在时直接返回会比走完 Argon2 快几十毫秒，能被计时区分。邮箱不存在时也要跑一次假的哈希验证。

**实测**：Cookie 策略单独成文件 `app/api/cookies.py`，身份依赖在 `app/api/deps.py`。
- 登出做成**幂等**（未登录调登出也返回 204，不报 401）。需求没写这条，是产品判断：
  会话过期后用户点退出，他想要的结果已经成立，报 401 只会让前端多一个没法解释的错误分支。
- `logout` 按 `token_hash` 删而非 `user_id`——后者会把该用户所有设备一起踢下线，
  而全端下线是 FR-AUTH-11（改密码）才需要的行为。
- 测试里踩的坑：`SimpleCookie` 对**没出现过**的标志位返回空串而不是 `False`，
  所以 `morsel["secure"] is False` 会失败并报 `assert '' is False`，看着像实现漏了属性。

---

### 步骤 6：把 Day 2 的约束测试沉淀成 pytest

**做什么**：把那 8 项一次性 SQL 改写成 pytest 用例。

**验证**：`pytest` 全绿，且删掉 `models.py` 里任意一个 `CheckConstraint` 后测试会失败（**改坏能测出来才算数**）。

**依赖**：步骤 1 · **约 1h**

**实测**：范围比预估大，最后是 27 项约束 + 2 项迁移，分成两个文件。
- 约束清单不是凭记忆写的，先把测试库里 `pg_constraint` / `pg_indexes` 的实际内容 dump 出来，
  按真实清单逐条落。
- 每条「应当被拒绝」附近都配了正向对照——一条把所有输入都拒之门外的约束也能让反向用例全绿。
- 额外加了 `tests/test_migrations.py`：漂移检查（`alembic check`）+ 降级往返
  （`downgrade base` → 断言表真的消失 → `upgrade head`）。
  「downgrade 没抛异常」不等于降级有效——一个空实现也不抛异常，而这条路径平时没人走，
  只有真出事要回滚版本时才用到。

**对照验证的做法**（值得沿用到后面）：直接在生产库结构上 `DROP CONSTRAINT` 掉每一条，
跑测试确认对应用例真的变红，再重建 schema。删掉 8 条具名约束 + `owner_id` 的 NOT NULL → 17 条红；
再删 3 个级联外键 → 又 4 条红；正向对照始终保持全绿。

---

## Day 4 —— 空间接口与前端

| 步 | 内容 | 原估 | 状态 |
| --- | --- | --- | --- |
| 7 | workspace 接口与权限校验 | 2h | ⬜ |
| 8 | 统一开发环境 host | 0.5h | ⬜ |
| 9 | 前端认证 | 3.5h | ⬜ |

### 步骤 7：workspace 接口与权限校验

`GET` / `PATCH /api/v1/workspaces/current`，权限校验放在**服务层**（FR-WS-04 明确不允许只在路由层拿 URL 里的 id 直接查）。验证：非成员访问返回 403 或 404。
**依赖步骤 1–5 · 约 2h**

**补充**：`/current` 这个路径设计从根上绕开了「按 id 查再校验」的问题——它不接收 id，
而是从当前用户反查成员关系，查询条件天然是 `user_id`，FR-WS-03 自动成立。
真正需要「按 id 取空间再验成员」的是 FR-WS-05（P2）和后续带 `workspace_id` 的
文档 / 问答模块，**那个机制今天不用建**，等有第一个真正按 id 访问资源的模块时再建。

### 步骤 8：统一开发环境 host

把 `CORS_ORIGINS` 改成 `http://127.0.0.1:5173`，与 vite 绑定地址一致，否则 Cookie 会被浏览器静默丢弃。**约 0.5h**

**理由需要修正**：在当前架构下 Cookie **不会**被丢弃——`frontend/vite.config.ts` 已经配了
`/api` 代理，浏览器看到的是 `127.0.0.1:5173` 上的**同源**请求，CORS 和 SameSite 都不参与。

只有当前端绕过代理、直接请求 `http://127.0.0.1:8000` 时才会变成跨站请求
（`127.0.0.1` 和 `localhost` 在 SameSite 眼里是两个站点），那时 `SameSite=Lax`
确实不发 Cookie，症状正是静默 401。

所以这一步该做，但它是**卫生问题**（配置与实际地址不符会误导人），不是步骤 9 的阻塞前置。

### 步骤 9：前端认证

注册页、登录页、auth 状态、路由保护。验证：浏览器里完成注册 → 登录 → 刷新仍保持 → 退出。
**依赖 7、8 · 约 3.5h**

**补充**：3.5h 偏乐观。当前前端只有 95 行骨架（`App.tsx` 42 行、`main.tsx` 10 行、
`style.css` 43 行），没有路由、没有 HTTP 客户端、没有状态管理，也没有任何测试基建。
实际需要新增路由库、API 客户端（要解析 API-02 的统一错误体并按 `code` 分支）、
认证 Context、两个页面、受保护路由。实际估 4~5h。

---

## 依赖关系与顺序

```text
1 测试基建 ──┬── 4 注册 ── 5 登录/登出/me ── 7 workspace ── 9 前端
             ├── 6 约束测试沉淀                        ↑
2 错误格式 ──┘                                   8 host 统一
3 security ──┘
```

- **步骤 2、3 互相独立，也不依赖 1**，可以先做或插空做（纯代码，不需要数据库）
- **步骤 1 是 4/5/6 的前置**，也是唯一一个「不先做就会拖累后面」的
- **步骤 6 只依赖 1**，任何时候都能做
- **步骤 8 必须在 9 之前**（见上面修正后的理由）

---

## Day 4 待拍板

1. **非成员访问返回 403 还是 404**。FR-WS-03 写的是「403 或 404」，二选一。
   倾向 404：不泄露「这个空间存在」。
2. **谁能重命名空间**。FR-WS-02 说的是「重命名**自己的**学习空间」，字面指向 owner。
   第一版没有 member（FR-WS-05 是 P2），代码里可以先写成「必须是 owner」，
   等 FR-WS-05 落地时再明确 member 能不能改名。
3. **前端走 vite 代理还是直连后端**。走代理则同源、步骤 8 可有可无；
   直连则步骤 8 和 `credentials: 'include'` 都变成必需。倾向走代理，
   生产环境再用反代保持同样的形状。
4. **前端要不要现在就引入测试基建**（vitest）。倾向先不引入——
   等有真实交互逻辑（文档上传状态机之类）再加，那时才知道该测什么。

另外，需求文档第 14 节的 6 个待确认问题**尚未处理**，其中第 3 问
（是否支持邀请同学进同一空间，影响 FR-WS-05 是否提前到 P1）与上面的第 1、2 条直接相关。
