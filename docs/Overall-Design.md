# AI Agent Writer Pro —— 项目整体设计

> 本文从 `Project-Develop.md` 中提取，聚焦项目的**整体架构与运行逻辑**：项目大纲、模块设计、
> 组织架构、运行流程图、前后端交互。面向需要快速理解项目全貌的工程师。

---

## 目录

- [一、项目定位](#一项目定位)
- [二、项目大纲层次](#二项目大纲层次)
- [三、项目架构](#三项目架构)
- [四、API 架构与前后端交互](#四api-架构与前后端交互)
- [五、项目运行逻辑流程](#五项目运行逻辑流程)
- [六、数据架构](#六数据架构)

---

## 一、项目定位

**AI Agent Writer Pro** 是一个大纲驱动的 AI 小说写作工作台，**单用户本地应用**
（无登录鉴权、无后台管理端、审计日志只读）。

它本质上是一个小说编辑器，支持两种写作模式：

1. **手动撰写** —— 人写正文，走常规 CRUD + 乐观锁 + 修订快照；
2. **AI 生成** —— Agent 写正文，但**必须以大纲章简介为前提**（四阶段固定流程 S0→S3 硬编排）。

### 设计铁律

1. 不自研 Agent 框架，只做写作领域化封装；
2. C/S 分离（REST 取数 + SSE 流式 + REST 控制）；
3. **文件为内容事实源，DB 为登记与索引**，不一致时以文件为准（可 `rebuild-index` 重建）；
4. 单用户 + 项目级隔离（Agent 跨项目**可读不可写**）；
5. Langfuse 可观测，未配置自动降级；
6. **显式命名**（禁止「未命名小说」「第 1 卷」等默认名，违规 `42201`）；
7. UI 默认中文，文案集中管理；
8. permissions **首条匹配生效**、兜底 `deny /**` 必须最后；
9. **ID 稳定、顺序由「插入」维护**（大纲降级为参考资料，不再回填 `sort_order`）；
10. 时间戳一律 UTC ISO-8601 应用层写入；
11. 文件读写一律 `pathlib` + 显式 `encoding="utf-8"`（Windows 兼容）。

---

## 二、项目大纲层次

### 2.1 功能层次（自顶向下）

```
AI Agent Writer Pro
│
├─ 写作业务层（人工撰写，不经 Agent）
│   ├─ 项目管理：小说 CRUD / 软删除 / 复制 / 导出
│   ├─ 回收站：被删章节归档（最多 20 本）/ 卷章浏览 / 回收 / 彻底删除
│   ├─ 卷章管理：卷章 CRUD / 短 ID 恒定 / 顺序由「插入」维护（无 reorder）
│   ├─ 正文编辑：乐观锁 / 修订快照 / 自动保存 / 回退编辑（≤5 次）/ 选区 AI 改写
│   ├─ 大纲维护：单文件大纲 / 严格解析 / 格式校验（★大纲是参考资料，不回填顺序）
│   ├─ 阅读模式：目录 / 翻页 / 进度记忆 / 夜间模式
│   └─ 检索：FTS5 二元切分 / LIKE 降级
│
├─ AI Agent 层（AI 生成与对话）
│   ├─ LLM 接入：多厂商 / 四层参数 / 降级链 / 密钥安全
│   ├─ Agent-Loop：主循环装配 / 流式 / 中断恢复 / 取消 / 收尾
│   ├─ Tools：文件工具（受 permissions 约束）/ web_search / outline_grep / ask_user / 结构工具
│   ├─ Sub-agents：5 个内置子代理（planner/writer/reviewer/web-searcher/recaller）
│   ├─ Skills：用户自建（虚拟路径挂载 /skills/user/）
│   ├─ Chapter Workflow：S0→S3 四阶段固定流程 + 咨询卡片
│   ├─ Memory：消息树 / 分支 / 双轨持久化
│   ├─ Knowledge：前情提要 / 章节摘要 / 大纲解析
│   ├─ Sandbox：CompositeBackend 路由 + 8 条有序权限
│   ├─ Prompts：Jinja2 模板（独立版本化）
│   └─ Tracing：Langfuse v5（未配 Key 自动降级）
│
└─ 平台支撑层
    ├─ 统一响应包装 / 错误码 / 全局异常处理
    ├─ 审计日志（SQLite + JSONL 双写，只读 API）
    ├─ 通用设置（user_settings 单行表）
    ├─ 动态组件（宠物两态 / 城市定位 / 天气）
    └─ 数据一致性（派生索引 + dirty 标记 + 全量重建）
```

### 2.2 代码层次（自顶向下）

```
仓库根
├── main.py                 本地调试一键启动器（后端 + 前端 Vite 双进程）
├── pyproject.toml          依赖与构建唯一事实源（uv）+ ruff / pytest 配置
├── alembic.ini             迁移配置
├── Dockerfile              多阶段构建（node:24 构建前端 → python:3.12-slim 运行）
├── docker-compose.yml      单体部署（writer-data 卷持久化）
├── src/
│   ├── app/                后端（FastAPI）
│   │   ├── api/            ① 路由层：15 模块 / 95 端点
│   │   ├── services/       ② 业务编排层：17 模块
│   │   ├── agent/          ③ AI Agent：7 个顶层目录（本项目技术核心）
│   │   ├── db/             ④ 数据访问：ORM 模型（按域拆 4 文件）+ 迁移 + FTS
│   │   ├── schemas/        ⑤ DTO：按域拆 4 文件
│   │   ├── core/           ⑥ 基础设施：配置 / 错误码 / 日志 / 路径 / 安全
│   │   ├── utils/          ⑦ 工具函数：时间 / ID 生成
│   │   ├── main.py         应用装配（lifespan / 路由 / CORS / 异常 / 静态资源）
│   │   └── cli.py          writer CLI（dev / migrate / rebuild-index / doctor）
│   └── web/                前端（Vue 3 + Vite + TS）
├── tests/                  pytest（23 文件，假模型驱动）
└── data/                   运行期数据（不入版本库）
```

### 2.3 依赖方向与边界

```
                    ┌──────────────────────────────────────┐
   浏览器（Vue 3）  │  三栏布局：左侧项目栏 / 中间阅读编辑 / 右侧 Agent 面板
                    └───────────────┬──────────────────────┘
                        REST /api/v1 │ SSE /agent/runs/{id}/stream
                    ┌───────────────▼──────────────────────┐
                    │ api/        路由层（薄，只做协议转换）│
                    └───────────────┬──────────────────────┘
                                    │ 取数一律经 services
                    ┌───────────────▼──────────────────────┐
                    │ services/   业务编排层                │
                    └──────┬────────────────────────┬──────┘
                           │                        │ agent 可用 services
              ┌────────────▼───────────┐            │ （单向，无环）
              │ core/ + db/ + schemas/ │   ┌────────▼────────┐
              │ 基础设施 / ORM / DTO   │◀──┤ agent/          │
              └────────────────────────┘   │ ★AI Agent 全部  │
              │   逻辑（7 目录）│
                                           └─────────────────┘
```

**边界约定**：

| 方向                    | 允许性                        | 说明                                                                                                                                       |
| ----------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `api/ → services/`   | ✅ 唯一取数路径               | 15 个 api 模块全部经 services                                                                                                              |
| `api/ → agent/`      | ✅ **仅运行入口**       | 只 `api/agent.py` 持有 `run_manager` + `sse_bus`（起 run / 订阅流）；`api/chapters.py` 持 `rewrite`（服务层直连 LLM 的选区改写） |
| `agent/ → services/` | ✅ 允许方向                   | workflows / knowledge / loop / more/mcp 用业务服务                                                                                         |
| `services/ → agent/` | ⚠️ **两处文档化例外** | ① `services/agent.py` → `agent.tracing.messages`（消息树读写）② `services/search.py` → `agent.loop.knowledge`（章节摘要生成）  |
| `core/ → agent/`     | ✅ 仅惰性访问器               | `core/catalog.py` 提供函数级惰性读取内置子代理目录                                                                                       |

---

## 三、项目架构

### 3.1 后端分层职责

| 层            | 模块数     | 职责                                                                       | 不做什么                                |
| ------------- | ---------- | -------------------------------------------------------------------------- | --------------------------------------- |
| `api/`      | 15         | 协议转换：路径/查询/请求体解析 → 调 services → `ok()` 包装；SSE 流订阅 | 不写业务规则、不直接查 ORM              |
| `services/` | 17         | 业务编排：校验、事务、审计、文件写入、派生索引同步                         | 不碰 HTTP 概念（无 Request/Response）   |
| `agent/`    | 7 顶层目录 | AI Agent 全部逻辑                                                          | 不承载写作业务 CRUD（那些是 REST 接口） |
| `db/`       | —         | ORM 模型 + async engine + 迁移 + FTS                                       | 不含业务规则                            |
| `schemas/`  | —         | Pydantic DTO（请求体与响应 `data`）                                      | 不含行为逻辑                            |
| `core/`     | —         | 配置 / 错误码 / 响应包装 / 日志 / 路径 / 密钥                              | 零业务、零 Agent 知识                   |

### 3.2 前端结构

```
src/web/src/
├── views/          WorkspaceView（三栏主工作台）/ SettingsView（通用设置）
│                   / AiSettingsView（AI 工具：模型 / 子代理 / 技能）
├── layouts/        三栏骨架
├── components/
│   ├── sidebar/    ProjectList / WeatherWidget / ZodiacPet
│   ├── reader/     ChapterReader / OutlineReader / TocDrawer / ReaderToolbar
│   ├── editor/     TiptapEditor / aiSourceMark / EditorActionBar
│   │               / RewriteDiffDialog / OutlineEditor / ConflictBanner / WordCountBar
│   ├── agent/      AgentPanel / ApprovalCard / ConsultCard
│   │               / WorkflowStatusBar / PlanTodoList / ContextPicker / ToolsPicker
│   ├── common/     ExportDialog / ★RecycleBin（回收站抽屉）
│   └── aitools/    ModelManager / SubagentManager / SkillManager
├── stores/         project / editor / reader / agent / settings（Pinia）
├── api/            http（axios 封装）/ index（端点封装）/ sse（EventSource 续传）
├── i18n/           中文文案集中管理
├── styles/         主题变量
└── types/          DTO 类型定义
```

三栏布局：**左侧**动态组件 + 项目列表 + 设置入口；**中间**阅读模式 / 编辑模式；
**右侧** Agent 面板（会话流、审批与咨询卡片、流程状态条、子代理泳道、上下文 Chips）。

---

## 四、API 架构与前后端交互

### 4.1 API 端点总览

统一前缀 `/api/v1`，**95 个端点**，分 15 组：

| 组            | 端点数 | 代表路径                                                                                                                                                                                                                                  |
| ------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent`     | 19     | `GET/POST /agent/sessions`、`/{sid}/messages`(202)、`/{sid}/branch`、`/{sid}/context`、`/runs/{id}`、`/runs/{id}/stream`(SSE)、`/runs/{id}/cancel`、`/runs/{id}/steer`(CRUD)、`/interrupts/{id}/resolve`、`/usage`(3) |
| `novels`    | 15     | `GET/POST /novels`、`PATCH/DELETE /novels/{id}`、`/duplicate`、`/export`、`/index/rebuild`、`/reading-progress`、`/workspace-summary`、export-tasks(CRUD)                                                                   |
| `llm`       | 12     | `/llm/providers` CRUD + `/test`、`/llm/models` CRUD + `/llm/overview`、`/config/reload`                                                                                                                                         |
| `volumes`   | 9      | `/novels/{id}/volumes`、`/volumes/renumber`、`/volumes/{vid}/chapters`、`/chapters/{cid}`                                                                                                                                         |
| `mcp`       | 6      | `/mcp-servers` CRUD + `/probe` + `/confirm`                                                                                                                                                                                         |
| `chapters`  | 5      | `GET/PUT /{cid}/content`（乐观锁）、`/locks`、`/revisions`、`/restore`                                                                                                                                                            |
| `skills`    | 5      | `/skills` CRUD + `/resync`                                                                                                                                                                                                            |
| `subagents` | 5      | `/subagents` CRUD + `/reset`                                                                                                                                                                                                          |
| `recycle`   | 4      | 回收站：`GET /recycle`、`GET /recycle/{id}`、`POST /recycle/{id}/chapters/{cid}/restore`、`DELETE /recycle/{id}/chapters/{cid}`                                                                                                   |
| `story`     | 4      | `/search`、`/summaries/rebuild`、`/audit`、`/export-preflight`                                                                                                                                                                    |
| `outline`   | 3      | `GET/PUT /outline`、`/outline/validate`                                                                                                                                                                                               |
| `settings`  | 3      | `GET/PUT /settings`、`POST /settings/reset`                                                                                                                                                                                           |
| `widgets`   | 3      | `/widgets/city`、`/pet-state`、`/weather`                                                                                                                                                                                           |
| `audit`     | 1      | `GET /audit-logs`（只读）                                                                                                                                                                                                               |
| `health`    | 1      | `GET /health`                                                                                                                                                                                                                           |

### 4.2 通用约定

- **统一响应包装** `{code, message, data}`，HTTP 状态码与 `code` 对齐（成功 `code=0` + 2xx）；
- **错误码常量**集中在 `core/errors.py`，配 `CODE_TO_HTTP` / `CODE_TO_MESSAGE` **双映射表**：

| code      | HTTP | 含义                                                    |
| --------- | ---- | ------------------------------------------------------- |
| `0`     | 2xx  | 成功                                                    |
| `40001` | 400  | 参数格式错误                                            |
| `40301` | 403  | 越权（WorkspaceGuard 拒绝 / 跨项目写入）                |
| `40401` | 404  | 资源不存在                                              |
| `40901` | 409  | **版本冲突**（乐观锁失败，`data` 回传最新内容） |
| `40902` | 409  | 运行状态冲突                                            |
| `41001` | 410  | SSE 事件缓冲丢失                                        |
| `42201` | 422  | **显式命名校验失败**                              |
| `42202` | 422  | 大纲解析失败                                            |
| `50001` | 500  | 服务端内部错误                                          |
| `50201` | 502  | LLM Provider 调用失败                                   |
| `50301` | 503  | 依赖不可用                                              |

- **SSE 信封** `{event, run_id, session_id, seq, ts, data}`，`seq` 每 run 内单调递增。

### 4.3 前后端交互模型

后端对前端提供两种通道：

| 通道           | 用途                             | 特点                  |
| -------------- | -------------------------------- | --------------------- |
| **REST** | 取数、控制（发消息、保存、配置） | 请求-响应，有明确结果 |
| **SSE**  | 推送运行过程（AI 正在写什么）    | 单向长连接，事件流    |

**「REST 下单 + SSE 收货」**：

```
POST /messages  →  立刻返回 202 + run_id（不等 AI 写完）
GET  /runs/{id}/stream  →  订阅这个 run 的事件流
```

**SSE 断线续传**：每个 run 在服务端留一份 500 条的环形缓冲 + 单调递增的 `seq`。
重连时带 `Last-Event-ID`，服务端补发漏掉的事件；缓冲淘汰或进程重启 → `41001` 让前端重拉消息树。

**17 类 SSE 事件**：`run.start` / `message.delta` / `message.complete` / `tool.start` /
`tool.result` / `subagent.start` / `subagent.delta` / `subagent.end` / `plan.update` /
`file.changed` / `context.compact` / `workflow.update` / `consult.request` / `interrupt.request` /
`usage` / `error` / `run.end`。

### 4.4 前端状态与通信设计

**Pinia 五 store 分工**：

| store        | 职责                                                                                                                                                         |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `project`  | 项目列表 / 当前项目 / 卷章树 / 展平章序列 / 卷 CRUD                                                                                                          |
| `editor`   | 打开章的 draft + base_version / dirty / 自动保存 / 409 冲突态 / 修订快照列表与回滚 / **回退编辑栈**（上限 5 条）                                       |
| `reader`   | 模式（read/edit）/ 目录抽屉开关 / 当前章或大纲节点 / 夜间模式                                                                                                |
| `agent`    | 会话列表与当前会话 / 消息 + 流式增量合并 / SSE 客户端 / 运行·取消·追加引导·流程生成 / 待审批 interrupt 与 consult / todos / 流程状态 / fileChanged / 用量 |
| `settings` | user_settings 加载与保存 / health 自检                                                                                                                       |

**通信约定**：

- `api/http.ts`（axios）：统一拆 `{code,message,data}` 信封；按业务码分发 UI 行为；
- `api/sse.ts`（EventSource）：断线自动重连带 `Last-Event-ID`；收到 `error(41001)` 回落重拉消息树；
- `i18n/index.ts`：全部中文文案集中管理；
- `styles/main.css` + CSS 变量：主题色/字号/字体/页宽由 settings 写入 `:root` 即时生效。

---

## 五、项目运行逻辑流程

### 5.1 应用启动（lifespan）

```
uvicorn 启动 → app/main.py lifespan
  ├─ init_database()：create_all 建表 → 打 alembic 版本戳
  │                   → FTS5 两步探测建 story_fts（不可用则永久降级 LIKE）
  ├─ seed_builtins()：播种内置子代理（幂等，不覆盖用户改动）
  ├─ 用户技能一致性检查（skill_configs ↔ data/skills/ 物化目录）
  ├─ _mount_frontend()：挂载 src/web/dist（SPA history 回落）
  └─ 统一异常处理 / CORS / 结构化日志（key|token|secret 字段自动掩码）
```

### 5.2 普通对话运行（发消息 → 流式回复）

```
前端 POST /agent/sessions/{sid}/messages
  │  （该会话有活跃 run → 409 / 40902）
  │  （全局已有其他会话在运行 → 202 + rejected="global_busy" + AI 友好提示）
  ▼
api/agent.py：run_manager.start()
  ▼
loop/runner.py：用户消息入消息树
  → 建 agent_runs(status=pending) → 会话置 running → sse_bus.open()
  → _spawn()（per-run asyncio.Lock 串行化）→ 立即返回 202 + run_id
  ▼
loop/resolve.py：解析主模型 + 降级链
  → make_recap_provider（前情提要 + 会话「已读章节」Chips）
  ▼
loop/builder.py：build_agent → create_deep_agent(backend, permissions, checkpointer,
                  tools, subagents, skills, 17 层 middleware, context_schema=AgentContext)
  ▼
graph.astream(stream_mode=["updates","messages"], subgraphs=True, context=ctx)
  ▼
loop/streaming.py：逐 chunk 映射为 SSE 事件
  ▼
17 层中间件并行生效（固定顺序）：
  WriteApproval → ModelCallBudget → ModelRetry → ModelSwitchOnFailure
  → TodoList → RecapInjection → HiddenContext → ContextGuard
  → PairingRepair → WastefulLoopGuard → SteerInjection → ReadScopeGuard
  → WorkspaceGuard → FileChangeNotify → ContentLock → PromptCache
  ▼
loop/lifecycle.py：finalize 收尾（agent_runs 汇总 + 会话状态 + 刷新字数索引 + Langfuse score）
  → publish_run_end → sse_bus.close()
```

**网络故障 30s 宽限续跑**（`loop/netprobe.py`）：run 因网络或模型接口异常失败后，
每 3s 探测一次连通性，30s 内恢复则以 checkpoint 续跑原任务，超时则落库通知。

### 5.3 流程生成运行（「生成本章」→ S0→S3）

```
用户请求生成第 N 章（POST messages 带 target_chapter_id）
  ▼
loop/runner.py 检测 target_chapter_id 非空 → 分流到 writing_steps/runner.py
  ▼
S0 大纲存在性（服务层直连 ChatModel + JSON 结构化输出）
  │──无简介──▶ ConsultRequired → 弹咨询卡片
  │有简介
  ▼
S1 衔接预检（服务层直连 LLM）
  │──不合理──▶ ConsultRequired → 弹咨询卡片
  │合理
  ▼
S2 生成（★唯一进入 deep agent 的阶段）
  │  writer 子代理用文件工具写入 chapters/v{卷}/c{章}.md
  │  写入前 WriteApproval 判定整章覆盖 → 先快照旧正文
  ▼
S3 匹配后检（服务层直连 LLM）
  │──不匹配──▶ 自动重生成（≤2 次）→ 仍不匹配 → 咨询卡片
  │匹配
  ▼
完成：summarize_chapter → 更新派生索引 → stage=done → run.end
```

### 5.4 中断与恢复（HITL）

```
Agent 触发中断（三类来源）
  ① 参数级：WriteApprovalMiddleware.classify_risk 命中 → interrupt()
  ② 调用预算：ModelCallBudgetMiddleware 超限 → 先总结再 consult
  ③ 流程咨询：orchestrator 抛 ConsultRequired
  ▼
落 agent_interrupts → 发 interrupt.request | consult.request
  → run.status = awaiting_*，会话同状态
  ▼
前端渲染 ApprovalCard / ConsultCard → POST /agent/interrupts/{id}/resolve
  │  approval: approve | edit | reject | respond
  │  consult : option | text
  ▼
run_manager.resume() → sse_bus.reopen() → 续跑同一 run
```

### 5.5 取消

```
POST /agent/runs/{id}/cancel
  → 置 cancel_flags（协作式）→ 给 3 秒优雅窗口 → 超时硬 cancel
  → run 置 cancelled → 发 run.end → sse_bus.close()
```

### 5.6 文件写入的两条路径

```
路径 A：前端编辑 → PUT /chapters/{cid}/content（乐观锁 base_version）
        services/volumes.write_content
路径 B：Agent 工具 → write_file / edit_file / delete
        permissions（第一道）+ WorkspaceGuardMiddleware（第二道）
                        │
                        ▼
        services/workspace.WorkspaceGuard（★服务层唯一文件写入口）
        ├─ resolve() 归一化后必须严格落在白名单根内
        ├─ 拒绝 ..、逐段检查符号链接、跨项目拒绝
        └─ 写入成功后同步更新派生索引（失败标 dirty=1）
                        │
                        ▼
        FileChangeNotifyMiddleware → 前端热更新 + 索引同步
```

---

## 六、数据架构

三层数据模型，职责严格分离：

```
① 文件（内容事实源）        data/workspaces/{project_id}/
                             ├── novel.yaml         项目元信息
                             ├── volumes.yaml       卷列表
                             ├── chapters/v{vol}/c{ch}.md   正文（frontmatter 含 version 乐观锁）
                             ├── outline/{小说名称}-大纲.md  大纲（参考资料）
                             └── summaries/ch-{id}.md        章节摘要

② SQLite（结构化登记）      data/app.db —— 16 张表
                             data/agent_state.db —— LangGraph checkpoint

③ 派生索引（可从①重建）     story_fts（FTS5 虚拟表）
                             project_index（章数/总字数/dirty 脏标记）
```

**关键设计**：正文与知识库以 Markdown 文件为唯一事实源；数据库只做登记与索引；
派生索引失败不阻断写入，改标 `dirty=1` 待重建。

16 张表按域拆分：

| 域         | 表                                                                                                                                                          |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `novel`  | `novels` / `chapter_revisions` / `reading_progress` / `project_index`                                                                               |
| `agent`  | `agent_sessions` / `messages` / `tool_call_logs` / `agent_runs` / `agent_interrupts` / `subagent_configs` / `skill_configs` / `mcp_servers` |
| `llm`    | `llm_providers` / `llm_models`                                                                                                                          |
| `config` | `user_settings` / `audit_logs`                                                                                                                          |

---

> 本文提取自 [`Project-Develop.md`](./Project-Develop.md)，完整技术细节（模块实现、开发重难点、
> 测试策略、技术栈版本）请参见原文。
