# StudyPilot 项目计划

## 1. 项目定位

StudyPilot 是一个面向 AI 应用开发学习者的个性化学习与复习平台。它帮助用户完成从资料理解、知识问答、章节练习、错题分析到间隔复习的完整闭环。

```text
设定学习目标 -> 导入课程资料 -> 带引用问答 -> 生成练习
-> 提交答案 -> 分析薄弱点 -> 生成复习任务
```

第一版聚焦四项能力：课程资料管理、带引用的 RAG 问答、章节练习和答案解析、错题记录和复习计划。社交、积分、付费和复杂多 Agent 协作放到后续版本。

## 2. 目标用户与核心场景

目标用户是正在学习 Python、RAG、Agent、LangChain 等技术的个人学习者。

用户上传课程资料并按课程组织；针对当前章节提问；查看文件、章节和页码来源；根据章节生成练习；提交答案后查看解析；系统记录错题并安排下一次复习。

项目成功的标准不是模型能聊天，而是用户能否更快理解资料、完成练习，并愿意持续使用。

## 3. 推荐技术栈

### 前端

```text
React + TypeScript + Vite
TanStack Query、Zustand、React Router
Tailwind CSS 或现有组件库
SSE 流式响应
```

### 后端

```text
Python、FastAPI、SQLAlchemy 2、Pydantic v2、Alembic
```

### 数据和基础设施

```text
PostgreSQL：业务数据、会话、学习记录
pgvector：向量检索
PostgreSQL Full Text Search：关键词检索
Redis：缓存、队列和限流
Celery：文档解析和向量化异步任务
MinIO 或本地对象存储：原始文件
Docker Compose：本地和线上部署
```

### 模型服务

使用 OpenAI-compatible LLM、Embedding 和可选 Reranker，并封装统一的 `ModelProvider` 接口。第一版采用 PostgreSQL + pgvector，验证产品后再按需要替换为 Milvus、Elasticsearch 或独立向量服务。

## 4. 系统架构

```text
React Web
   | REST / SSE
   v
FastAPI API
   +-- AuthService
   +-- DocumentService
   +-- IngestionService
   +-- RetrievalService
   +-- ChatService
   +-- QuizService
   +-- ReviewService
   +-- EvaluationService
   +-- PostgreSQL + pgvector
   +-- Redis + Celery
   +-- Object Storage
   +-- LLM / Embedding / Reranker
```

采用模块化单体架构，不急于拆分微服务。后端目录建议如下：

```text
backend/app/
  api/ core/ models/ schemas/ repositories/
  services/
    auth.py document.py ingestion.py retrieval.py
    chat.py quiz.py review.py evaluation.py
  clients/llm.py embedding.py reranker.py
  workers/ prompts/ main.py
```

培训资料中可重点参考：

- `D:\hmstudy\ragProject\Code\edu_rag_project_rrf\services\chunk_store.py`：父子块、去重和入库；
- `D:\hmstudy\ragProject\Code\edu_rag_project_rrf\services\rag.py`：召回、父块恢复和生成；
- `D:\hmstudy\ragProject\Code\edu_rag_project_rrf\services\hybrid.py`：混合检索和 RRF；
- `D:\hmstudy\ragProject\Code\edu_rag_project_rrf\pipeline.py`：问答流程编排；
- `D:\hmstudy\ragProject\Code\edu_rag_project_rrf\commands\eval_ragas.py`：评测集和 RAGAS 评估。

## 5. 数据库设计

```text
users
- id, email, password_hash, created_at

workspaces
- id, user_id, name

documents
- id, workspace_id, file_name, file_type, file_hash
- status, version, error_message, created_at

document_chunks
- id, document_id, parent_id, content, embedding
- title_path, page_number, chunk_index, metadata

conversations
- id, workspace_id, title, created_at

messages
- id, conversation_id, role, content, citations
- latency_ms, token_usage, created_at

quizzes
- id, workspace_id, document_id, question, options
- answer, explanation, knowledge_point, difficulty, source_chunk_ids

quiz_attempts
- id, quiz_id, user_answer, is_correct, score, created_at

review_tasks
- id, workspace_id, knowledge_point, source_type, source_id
- review_date, interval_days, status
```

所有业务查询必须带 `workspace_id` 或 `user_id` 条件，防止用户之间的数据串用。

## 6. 文档处理流程

第一阶段支持 Markdown、TXT、PDF、DOCX；第二阶段支持 HTML、CSV、JSON；第三阶段再考虑代码文件和网页抓取。

```text
上传文件 -> 校验大小和格式 -> 计算 SHA-256 -> 保存原始文件
-> 创建 ingestion_job -> 异步解析 -> 清洗文本 -> 保留标题层级
-> 父块切分 -> 子块切分 -> 生成 Embedding
-> 写入 PostgreSQL -> 更新任务状态
```

初始参数建议：父块 500 到 800 个中文字符，子块 150 到 300 个中文字符，重叠 30 到 50 个字符。每个块保留 `document_id`、文件名、标题路径、页码和块序号。

```json
{
  "document_id": 12,
  "file_name": "rag课程.md",
  "title_path": ["RAG", "混合检索", "RRF"],
  "page_number": 8,
  "chunk_index": 23
}
```

通过 `file_hash` 实现幂等处理，避免重复上传导致重复生成向量。文档状态建议使用 `pending`、`processing`、`ready`、`failed`。

## 7. RAG 检索流程

```text
用户问题 -> 意图判断 -> 查询改写 -> 向量检索
-> 关键词检索 -> RRF 融合 -> Reranker 重排
-> 相似度阈值判断 -> 上下文压缩 -> LLM 生成
-> 返回答案和引用
```

意图至少区分：知识问答、练习生成、错题解析、学习计划和普通寒暄。只有知识问答进入 RAG 流程，其他任务进入对应服务。

初始检索参数：向量召回 Top 20，关键词召回 Top 20，RRF 合并 Top 10，Reranker 重排 Top 5，最终送入模型 Top 3 到 Top 5。

系统提示词要求：

1. 只能依据提供的资料回答；
2. 资料不足时明确说明；
3. 不补充没有来源的结论；
4. 每个关键结论附带引用；
5. 引用包含文件名、章节和页码；
6. 先给结论，再给解释和学习建议。

引用结构示例：

```json
{
  "text": "RAG 通过外部知识检索补充模型上下文。",
  "document_id": 12,
  "file_name": "rag课程.md",
  "title_path": ["RAG", "基本原理"],
  "page_number": 3,
  "chunk_id": 88
}
```

前端点击引用后展开原文片段；当检索分数低于阈值时返回“不确定”，不要让模型自行补全。

## 8. SSE 流式输出

```text
event: start
data: {"conversation_id": 1}

event: token
data: {"text": "RAG"}

event: citation
data: {"citation_id": 3}

event: end
data: {"message_id": 20, "latency_ms": 1800}
```

前端处理连接建立、token 追加、引用展示、网络中断、重新生成、用户停止生成、服务异常、空答案和低置信度答案。

## 9. 练习生成与复习

题目使用结构化输出：

```json
{
  "question_type": "single_choice",
  "question": "RAG 的主要作用是什么？",
  "options": ["...", "...", "...", "..."],
  "answer": "B",
  "explanation": "...",
  "knowledge_point": "RAG基本原理",
  "difficulty": 2,
  "source_chunk_ids": [88, 91]
}
```

生成后校验选项数量、答案存在性、知识库来源、章节相关性、题目重复和多正确答案问题。选择题和判断题使用确定性评分，简答题使用评分标准和模型辅助判断，并保留原始答案及评分理由。

第一版采用简化间隔复习：首次答错 1 天后复习，连续答对 3 天后复习，再次答对 7 天后复习，持续答对 14 天后复习，答错后重新回到 1 天。

## 10. 前端页面

```text
登录 / 注册
首页仪表盘
课程空间
文档上传和处理状态
知识库问答
练习页面
答案解析
错题本
复习任务
学习进度
```

首页展示当前学习目标、已完成章节、待复习知识点、最近错题和继续学习按钮。问答页面提供当前课程范围、相关章节、引用来源、检索状态、重新生成、加入错题和生成练习等操作。

## 11. 评估方案

准备 50 到 100 条真实问题，覆盖事实查询、概念解释、多轮追问、跨章节问题、资料中没有答案的问题和容易混淆的问题。

检索指标：`Recall@K`、`Precision@K`、`MRR`、`NDCG`。

生成指标：`Faithfulness`、`Answer Relevancy`、`Context Precision`、`Context Recall`。

产品指标：首次完成提问时间、答案引用点击率、练习完成率、答题正确率、次日回访率和主动重新提问率。

评估数据必须来自真实课程问题和真实使用记录。简历中填写实际测量结果，不虚构用户量和提升比例。

## 12. 六周开发计划

### 第 1 周：产品和基础工程

- 确定用户、场景和功能边界；
- 建立 React 和 FastAPI 项目；
- 完成数据库设计、登录和 workspace；
- 完成 Docker Compose。

### 第 2 周：文档和知识库

- 完成文件上传；
- 完成 PDF、Markdown、DOCX 解析；
- 完成父子块切分和异步入库；
- 完成文档状态展示。

### 第 3 周：RAG 问答

- 完成向量检索和关键词检索；
- 完成 RRF 融合、上下文构建和引用格式；
- 完成 SSE 流式回答。

### 第 4 周：练习和错题

- 完成题目生成和结构化校验；
- 完成选择题评分、错题保存和知识点统计。

### 第 5 周：复习和产品体验

- 完成复习任务和学习进度；
- 优化空状态、加载、重试、错误提示和移动端适配。

### 第 6 周：评估和交付

- 建立真实问题集；
- 比较检索参数；
- 记录响应时间和调用成本；
- 找 5 到 10 名同学试用；
- 修复高频问题；
- 完成部署、截图和项目文档。

## 13. 测试策略

### 单元测试

测试文档切分、文件哈希和幂等入库、RRF 融合、引用格式化、题目结构校验和复习日期计算。

### 集成测试

覆盖文件上传到文档入库、文档入库到检索、问题到 SSE 完整响应、练习生成到答题记录、错题到复习任务。

### 人工验收

验证无答案问题是否拒答、引用是否支持结论、多轮追问是否理解指代、不同用户是否隔离、失败任务能否重试、手机端是否能完成问答和答题。

## 14. 部署与运维

本地使用 Docker Compose 启动 `frontend`、`backend`、`postgres`、`redis` 和 `minio`。生产环境配置 HTTPS、环境变量管理、数据库备份、日志和错误追踪、上传大小限制、API 限流、模型超时重试和用户数据删除能力。

记录以下指标：请求延迟、首 token 延迟、模型 token 用量、单次请求成本、检索命中率、文档处理失败率和 SSE 中断率。

## 15. 简历呈现

项目名称：

> StudyPilot：面向 AI 应用开发学习者的个性化学习与复习平台

简历描述示例：

> 独立设计并实现面向 AI 应用开发学习者的 C 端学习平台，支持课程文档管理、带引用的 RAG 问答、章节练习、错题分析和间隔复习。使用 FastAPI、React、PostgreSQL、pgvector、Redis 和 Celery 构建文档异步处理与流式问答链路，实现向量检索、关键词检索、RRF 融合、重排和引用溯源，并基于真实课程问题建立评测集验证召回质量和回答一致性。

最终交付物：线上地址、GitHub 仓库、系统架构图、数据库设计图、RAG 流程图、评测报告、测试用户反馈、项目 README 和简历项目描述。

## 16. 实施原则

1. 先完成稳定的确定性工作流，再考虑 Agent；
2. 先用真实课程资料和真实问题验证，再扩展数据范围；
3. 先做一个完整闭环，再增加模型和基础设施；
4. 所有回答保留来源和必要的置信度信息；
5. 所有指标来自真实测试，不虚构结果；
6. 技术方案服务于用户完成学习任务，而不是堆叠框架。
