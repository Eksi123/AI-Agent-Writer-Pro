# AI Agent Writer Pro

大纲驱动的 AI 小说写作工作台 —— **单用户本地应用**（无登录鉴权、无后台管理端）。

基于 [deepagents](https://github.com/langchain-ai/deepagents) + LangGraph + FastAPI + Vue 3 构建，
支持**手动撰写**与 **AI 生成**两种写作模式。AI 生成正文以大纲章简介为**必要前提**
（四阶段门禁 G0→G3 硬编排），正文以 Markdown 文件为事实源，SQLite 只存登记与派生索引。

> 技术全景（架构 / 模块实现逻辑 / 运行逻辑 / 技术体系 / 开发重难点）见 [`Writer-Develop.md`](./Writer-Develop.md)。

---

## 一、功能介绍

### 1.1 写作业务

| 域 | 能力 |
| --- | --- |
| **项目管理** | 小说 CRUD（强制显式命名，禁「未命名小说」）、软删除入回收站、整项目复制、zip 导出（markdown / epub / mobi / txt + 大纲原件） |
| **卷章** | 卷 / 章 CRUD（短 ID 生成后永不变）、**无重排接口**（顺序只由大纲小节先后派生）、章不可跨卷移动 |
| **正文编辑** | Tiptap 富文本、自动保存、字数统计、卷章树（只读顺序）、**乐观锁**（`base_version` 不符返回 409 + 最新内容）、版本冲突横幅（diff 合并 / 强制覆盖）、修订快照与一键回滚 |
| **选区 AI 改写** | 改写 / 扩写 / 缩写 / 润色四模式，只转换不落盘 → 句级 diff 预览 → 接受后打 **AI 来源标记**（可点击回滚原文） |
| **大纲** | 单文件大纲（`# 卷` → `## 章` → `- 简介：` / `- 衔接要点：` + 附录 A~D 设定）、严格层级解析、卷章名归一化匹配、格式校验（含错误行号）、`sync_order_from_outline` 顺序唯一写入口 |
| **阅读** | 目录（顶端「大纲」节点）、翻页、阅读进度记忆、夜间模式 |
| **检索** | FTS5 全文检索（中文**二元切分** + `unicode61`），FTS5 不可用自动降级 `LIKE` |

### 1.2 AI Agent

| 域 | 能力 |
| --- | --- |
| **主循环** | deepagents `create_deep_agent`（LangGraph 图）+ 9 层中间件栈；文件工具（`ls / read_file / write_file / edit_file / delete / glob / grep`）+ `web_search`（Wikipedia + DuckDuckGo 免 Key 双源） |
| **写作子代理** | 5 个内置：`planner`（策划大纲）/ `writer`（撰写正文）/ `editor`（润色校对）/ `continuity-checker`（连贯性审查）/ `researcher`（资料检索）+ deepagents 自动追加的 `general-purpose`；每个可配模型覆盖、专属提示词、可用技能、启停 |
| **写作技能** | 8 个内置 `SKILL.md`（novel-planning / prose-writing / line-editing / continuity-check / naming + 玄幻 / 都市 / 奇幻题材）+ 用户自建技能（物化到 `data/skills/`）；靠模型 `read_file` 渐进式披露 |
| **门禁生成** | **G0** 大纲存在性 → **G1** 衔接预检 → **G2** 生成（写前快照可回滚）→ **G3** 匹配后检（不匹配自动重生成 ≤2 次）；每阶段可下发咨询卡片，全程 `gate.update` 事件驱动前端状态条 |
| **人为介入（HITL）** | 工具级审批（`delete` 无条件）+ **参数级**审批（整章覆盖 / 大范围 edit / 批量删除，由自定义中间件调 `interrupt()`）；支持 批准 / 编辑后批准 / 拒绝 / 回复补充 四决策 |
| **会话** | 按小说项目隔离的多会话、**消息树分支**（重新生成 / 编辑重发 = 同父兄弟分支，可切换查看）、协作式取消、导出会话为 Markdown |
| **流式输出** | SSE 17 类事件（token 增量 / 工具调用 / 子代理泳道 / 待办计划 / 文件变更 / 门禁流转 / 审批与咨询卡片 / token 用量），环形缓冲 500 + `Last-Event-ID` 断线续传 |
| **上下文 Chips** | 主动添加子代理 / 技能 / 已读章节作为会话上下文，会话级持久化并真实注入装配 |
| **半隔离沙箱** | `CompositeBackend` 三路由（`/projects/` `/skills/builtin/` `/skills/user/`）+ 8 条**有序** `FilesystemPermission`：当前项目可读写、其它项目**只读**、技能只读、兜底 `deny /**`；`execute` 不开放（自动对模型隐藏） |
| **上下文治理** | 前情提要注入（近 N 章摘要 + 跨卷锚点）、token 预算检查、summarization 驱逐卸载到 `/conversation_history/` |

### 1.3 模型与集成

| 域 | 能力 |
| --- | --- |
| **LLM 多厂商** | OpenAI / OpenAI 兼容（DashScope·Qwen、DeepSeek、Moonshot、智谱、硅基流动、vLLM、自建网关）/ Anthropic / Google / Azure OpenAI / OpenRouter |
| **韧性** | 超时重试（仅超时 / 429 / 5xx / 连接错误）、降级链（有序 `model_ref`，最多 3 级）、单次 run 调用上限软收尾 + `recursion_limit` 硬上限、**重复错误避免**（错误指纹记忆：二次告警、三次阻断） |
| **四层参数覆盖** | 全局默认（`.env` + DB）→ 小说项目级（`novel.yaml.llm_params`）→ 会话级（`agent_sessions.model_ref` 等）→ 单条消息级（body 内 `model_ref`，不回写） |
| **密钥安全** | API Key **只存 `.env`**，DB 仅记环境变量名；UI 展示掩码（`sk-****abcd`）；写回 `.env` 走白名单键 + 临时文件原子替换 + `.env.bak` 备份 + 进程内互斥锁；日志字段自动脱敏 |
| **MCP** | 内置 Open-Meteo 天气 MCP（免费无 Key，含 Geocoding）；第三方 MCP（stdio / http / sse）。stdio 型三重约束：**可执行文件白名单**（`npx` / `uvx` / `python`）+ 用户**二次显式确认** + 命令全文落审计 |
| **可观测** | Langfuse v5 全链路追踪（自动埋点 CallbackHandler + 手动 observation + score），**未配 Key 自动降级**本地结构化日志；审计日志 SQLite + JSONL 双写，只读查询 API |
| **动态组件** | 左上角宠物两态动画（写作中敲键盘 / 久无进展打瞌睡）+ 浏览器定位城市（拒绝时回落设置城市）+ 当前天气 |

### 1.4 通用设置

主题颜色、字体大小、字体风格、阅读页宽、自动保存间隔、回落城市、宠物发呆阈值 —— 持久化于 `user_settings` 单行表。

---

## 二、快速启动

### 2.1 环境要求

| 项 | 要求 |
| --- | --- |
| Python | 3.12+ |
| Node.js | 20+（仅前端开发 / 构建需要） |
| uv | 推荐（`pyproject.toml` + `uv.lock` 是依赖唯一事实源） |
| calibre | 可选，未安装则 mobi 导出置灰（markdown / epub / txt 不受影响） |

### 2.2 一键启动（推荐，日常调试）

```powershell
python main.py                 # 同时拉起后端(8000) + 前端 Vite(5173)，Ctrl+C 一起停
python main.py --prod          # 单体模式：构建前端后只开后端 8000
python main.py --backend-only  # 只启动后端
python main.py --frontend-only # 只启动前端（需后端已在 8000）
python main.py --port 9000     # 自定义后端端口（前端代理自动跟随）
```

访问 `http://localhost:5173`（开发模式，前端热更新）或 `http://127.0.0.1:8000`（单体模式）。
脚本会自动 `npm install` / 构建前端、探测端口就绪；无 uv 时回落 `PYTHONPATH=src` 直跑。

### 2.3 常规手动启动

```powershell
# 1. 安装后端依赖（自动创建 .venv）
uv sync

# 2. 初始化 / 迁移数据库
uv run writer migrate

# 3. 启动后端（默认 http://127.0.0.1:8000）
uv run writer dev

# 4. 另开一个终端，启动前端开发服务器
cd src/web
npm install
npm run dev
```

**首次使用**：左下角「AI 工具」→「大模型 API」添加 Provider 与模型（密钥会写入 `.env`，DB 只存变量名），
之后即可在右侧 AI 助手面板对话，或在正文页用「生成本章」触发门禁生成。

### 2.4 单体部署（生产）

```powershell
cd src/web; npm ci; npm run build   # 产出 dist/，FastAPI 自动挂载（SPA history 回落）
uv run writer dev --no-reload       # 访问 http://127.0.0.1:8000
```

### 2.5 Docker

```powershell
docker compose up -d          # 多阶段构建（node:24 构建前端 → python:3.12-slim 运行）+ 启动
docker compose logs -f app    # 看日志；数据持久化在 writer-data 卷
```

### 2.6 CLI

```powershell
uv run writer dev             # 启动开发服务器（uvicorn --reload）
uv run writer migrate         # alembic upgrade head（无迁移文件时回落 create_all）
uv run writer rebuild-index   # 全量重建 story_fts / project_index（DB 与文件不一致时）
uv run writer doctor          # 环境自检（FTS5 / 二元切分 / calibre / Langfuse / 依赖版本）
```

### 2.7 `.env` 配置

复制 `.env.example` 为 `.env`，按需填写：

```ini
APP_HOST=127.0.0.1
APP_PORT=8000
DATA_ROOT=./data
LOG_LEVEL=INFO

# LLM（密钥只进 .env，DB 只存变量名）
DASHSCOPE_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
AZURE_OPENAI_API_KEY=
OPENROUTER_API_KEY=

# Langfuse（三项全空则追踪自动降级为本地日志）
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

### 2.8 测试

```powershell
uv run python -m pytest -q     # 全量测试（399 用例，假模型驱动，不触网 / 不打真实 LLM）
uv run ruff check src tests    # lint
cd src/web; npm run build      # vue-tsc 类型检查 + vite 构建
```

---

## 三、项目大纲层次、架构与运行逻辑

### 3.1 项目大纲层次（功能自顶向下）

```
AI Agent Writer Pro
│
├─ 写作业务层（人工撰写，不经 Agent）
│   ├─ 项目管理：小说 CRUD / 软删除 / 复制 / 导出
│   ├─ 卷章管理：卷章 CRUD / 短 ID 恒定 / 顺序由大纲派生
│   ├─ 正文编辑：乐观锁 / 修订快照 / 自动保存 / 选区 AI 改写
│   ├─ 大纲维护：单文件大纲 / 严格解析 / 格式校验 / 顺序回填
│   ├─ 阅读模式：目录 / 翻页 / 进度记忆
│   └─ 检索：FTS5 二元切分 / LIKE 降级
│
├─ AI Agent 层（AI 生成与对话）
│   ├─ LLM 接入：多厂商 / 四层参数 / 降级链 / 密钥安全
│   ├─ Agent-Loop：主循环装配 / 流式 / 中断恢复 / 取消 / 收尾
│   ├─ Tools：文件工具（受 permissions 约束）/ web_search / MCP
│   ├─ Sub-agents：5 个写作子代理 + general-purpose
│   ├─ Skills：8 个内置 + 用户自建（虚拟路径挂载）
│   ├─ Gates：G0→G3 四阶段门禁 + 咨询卡片
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
    ├─ 动态组件（宠物 / 城市 / 天气）
    └─ 可观测与数据一致性（派生索引 + dirty 标记 + 全量重建）
```

**贯穿全局的设计铁律**：

1. 不自研 Agent 框架，只做写作领域化封装；
2. C/S 分离（REST 取数 + SSE 流式 + REST 控制）；
3. **文件为内容事实源，DB 为登记与索引**，不一致时以文件为准（可 `rebuild-index` 重建）；
4. 单用户 + 项目级隔离（Agent 跨项目**可读不可写**）；
5. **显式命名**（禁止默认名，违规 `42201`）；
6. **ID 稳定、顺序由大纲派生**（`sort_order` 唯一写入口 `sync_order_from_outline`，无 reorder / move 接口）；
7. permissions **首条匹配生效**、兜底 `deny /**` 必须最后；
8. 时间戳一律 UTC ISO-8601 应用层写入；文件读写一律 `pathlib` + 显式 `encoding="utf-8"`。

### 3.2 项目架构

#### 分层与依赖方向

```
                    ┌──────────────────────────────────────┐
   浏览器（Vue 3）  │  三栏布局：左侧项目栏 / 中间阅读编辑 / 右侧 Agent 面板
                    └───────────────┬──────────────────────┘
                        REST /api/v1 │ SSE /agent/runs/{id}/stream
                    ┌───────────────▼──────────────────────┐
                    │ api/        路由层（14 模块，81 端点）│
                    └───────────────┬──────────────────────┘
                                    │ 取数一律经 services
                    ┌───────────────▼──────────────────────┐
                    │ services/   业务编排层（16 模块）     │
                    └──────┬────────────────────────┬──────┘
                           │                        │ agent 可用 services
              ┌────────────▼───────────┐            │ （单向，无环）
              │ core/ + db/ + schemas/ │   ┌────────▼────────┐
              │ 基础设施 / ORM / DTO   │◀──┤ agent/          │
              └────────────────────────┘   │ ★AI Agent 全部  │
                                           │   逻辑（12 子包）│
                                           └─────────────────┘
```

依赖约束：`agent/ → services/ → core/ + db/` 单向；`api/` 只额外持有 Agent 的**运行入口**
（`run_manager` + `sse_bus`）。`services/` 不静态 import `agent/`，仅两处**文档化例外**
（消息树读写、章节摘要生成），另有 `core/catalog.py` 提供惰性访问器读取 Agent 内置目录数据。

#### 后端目录（`src/app/`）

```
app/
├── api/          # 14 个路由模块：novels / volumes / chapters / outline / story / settings
│                 #   / audit / llm / agent / subagents / skills / mcp / widgets / health
├── core/         # config（pydantic-settings）/ errors（错误码 + 双映射表）/ response（统一包装）
│                 #   / logging（structlog）/ paths（路径解析 + BUILTIN_SKILLS_DIR）
│                 #   / security（密钥读写与掩码）/ deps（依赖注入）/ catalog（Agent 目录惰性访问器）
├── db/           # base（DeclarativeBase + 命名约定 + utcnow/new_id）/ session（async engine）
│                 #   / bootstrap（建表 + alembic 版本戳 + FTS5 探测）/ fts（二元切分 + 降级）
│                 #   / models（novel·agent·llm·config 按域拆分）/ migrations（alembic）
├── schemas/      # Pydantic DTO（novel·agent·llm·config 按域拆分，__init__ 只做 re-export）
├── services/     # 业务编排（16 模块）：novels / volumes / story / search / outline / exporters / files
│                 #   / naming / audit / workspace（WorkspaceGuard）/ weather
│                 #   / llm / agent / skills / subagents / mcp
└── agent/        # ★AI Agent 全部代码逻辑
    ├── llm/      #   factory（多厂商 ChatModel + 降级链 + LRU 缓存）/ params（四层覆盖）
    │             #   / budget（token 预算）/ rewrite（选区改写，服务层直连 LLM）
    ├── loop/     #   builder（create_deep_agent 装配）/ runner（run 生命周期）
    │             #   / streaming（LangGraph→SSE 映射 + 中断持久化）/ message_sync（消息树增量落库）
    │             #   / resolve（模型与会话上下文解析）/ lifecycle（收尾与状态工具）
    │             #   / events（SSE 环形缓冲）/ checkpointer（AsyncSqliteSaver）
    │             #   / context（AgentContext）/ emitter（事件发射器）
    │             #   / middleware（Recap·WorkspaceGuard·FileChangeNotify·WriteApproval）
    ├── gates/    #   orchestrator（G0-G3 状态机）/ runner（门禁运行编排）/ state（GateState）
    ├── sandbox/  #   backend（CompositeBackend 三路由）/ permissions（8 条有序规则常量）
    ├── tools/    #   web_search（免 Key 双源）/ registry（内置工具清单）
    ├── subagents/#   builtin（5 个内置定义）/ builder（SubAgent 装配）
    ├── skills/   #   8 个内置技能（<name>/SKILL.md）
    ├── mcp/      #   server（内置天气 MCP + LangChain tool）/ adapter（第三方 MCP 封装 + 白名单）
    ├── memory/   #   tree（消息树：parent_id + branch_index + active_leaf）
    ├── knowledge/#   chapters（章节取数）/ recap（前情提要）/ summarize（章节摘要）
    ├── prompts/  #   renderer（Jinja2 StrictUndefined）+ 5 个模板
    └── tracing/  #   langfuse（v5 客户端 / CallbackHandler / observation / score）
```

#### 前端目录（`src/web/src/`）

```
views/       WorkspaceView（三栏主工作台）/ SettingsView（通用设置）/ AiToolsView（AI 工具）
layouts/     三栏骨架
components/
├── sidebar/    ProjectList（项目列表 + 新建/删除/导出）/ WeatherWidget（宠物 + 城市 + 天气）
├── reader/     ChapterReader / OutlineReader / TocDrawer / ReaderToolbar
├── editor/     TiptapEditor / aiSourceMark（AI 来源标记）/ RewriteDiffDialog（选区改写 diff）
│               / VolumeChapterTree / OutlineEditor / ConflictBanner / AutosaveIndicator / WordCountBar
├── agent/      AgentPanel（会话面板）/ ApprovalCard（审批）/ ConsultCard（咨询）
│               / GateStatusBar（门禁状态条）/ PlanTodoList（待办计划）
└── aitools/    SubagentManager / SkillManager / McpManager
stores/      project / editor / reader / agent / settings（Pinia）
api/         http（axios 封装）/ index（81 端点）/ sse（EventSource 续传）
i18n/        中文文案集中管理     styles/  主题变量     types/  DTO 类型
```

#### 数据存储（`data/`，备份 = 停服后整目录复制）

```
data/
├── app.db                       # 业务库（16 张表：登记 / 快照 / 会话 / 消息 / 运行 / 中断 / 审计 / 派生索引）
├── agent_state.db               # LangGraph checkpoint（Agent 恢复与 HITL 续跑，跨库不建外键）
├── workspaces/{project_id}/     # ★小说内容（最重要，事实源）
│   ├── novel.yaml               #   title / author / genre / synopsis / status / llm_params / outline_file
│   ├── volumes.yaml             #   卷列表：id(短 uuid 恒定) / title / summary / sort_order(★派生值)
│   ├── chapters/v{vol_id}/c{chapter_id}.md
│   │                            #   frontmatter：title / sort_order(★派生值) / word_count
│   │                            #     / status(draft|finalized) / version(乐观锁) / updated_at
│   ├── outline/{小说名称}-大纲.md #   单文件大纲（卷章简介 + 附录设定），创建时写入模板
│   └── summaries/ch-{chapter_id}.md
│                                #   章节摘要（仅 summarize_chapter 可写，WorkspaceGuard 强制）
├── skills/{name}/SKILL.md       # 用户自建技能物化目录（Agent 只读，服务层写）
├── session-skills/{sid}/        # 会话级技能精选目录（上下文 Chips 物化后挂载）
├── exports/                     # 导出 zip 产物
├── trash/{project_id}-{ts}/     # 软删除项目回收站（保留可恢复窗口）
└── logs/                        # JSONL 日志：app-{date} / error-{date} / audit-{date}
```

### 3.3 运行逻辑

#### ① 普通对话运行（发消息 → 流式回复）

```
前端 POST /agent/sessions/{sid}/messages
  │  （若该会话有活跃 run → 409 / 40902）
  ▼
api/agent.py：services.agent 取会话行 → run_manager.start()
  ▼
loop/runner.py：用户消息入消息树（接 active_leaf 或按 parent_id 建分支）
  → 建 agent_runs(status=pending) → 会话置 running → sse_bus.open() → 起后台任务 → 立即返回 202 + run_id
  ▼
loop/resolve.py：解析主模型 + 降级链（未配模型 → 50201）
  → 构建 recap provider（前情提要 + 会话「已读章节」Chips）
  → 解析会话级子代理/技能选择（id → name）
  ▼
loop/builder.py：create_deep_agent(backend, permissions, checkpointer, tools,
                  subagents, skills, middleware 栈, context_schema=AgentContext)
  ▼
发 run.start 事件 → graph.astream(stream_mode=["updates","messages"], subgraphs=True)
  ▼
loop/streaming.py：逐 chunk 映射为 SSE
  ├─ messages 模式 → message.delta（token 增量）/ subagent.delta / usage
  └─ updates 模式 → __interrupt__ → 落 agent_interrupts + interrupt.request/consult.request
                  → model 节点完成 → message.complete / subagent.end
                  → write_todos → plan.update
                  → 每次 updates 后 message_sync 增量落库消息树
  ▼
中间件并行生效：RecapInjection（注入前情提要 + 预算检查）
  → WorkspaceGuard（路径校验 + 审计 + 错误指纹阻断）
  → FileChangeNotify（发 file.changed + 同步派生索引；改大纲则触发顺序回填）
  → WriteApproval（参数级高危写入 → interrupt）
  ▼
loop/lifecycle.py：finalize 收尾（agent_runs 汇总 + 会话状态 + 刷新字数索引
  + Langfuse score）→ 发 run.end → sse_bus.close()
```

前端 `GET /agent/runs/{run_id}/stream` 订阅事件（先建流再发消息、或先发消息再建流都支持，
环形缓冲会补齐）。断线重连带 `Last-Event-ID`（或 `?last_event_id=`）从缓冲补发；
缓冲丢失 / 进程重启 → `error(41001)` 并关流，前端回落重拉消息树。

#### ② 门禁生成运行（「生成本章」→ G0→G3）

```
用户请求生成第 N 章（POST messages 带 target_chapter_id）
  ▼
loop/runner.py 检测到 target_chapter_id → 分流到 gates/runner.py
  ▼
G0 大纲存在性 ──无简介──▶ consult.request（让 Agent 代写大纲 / 我自己去补 / 取消）
  │有简介                    → 运行置 awaiting_consult，暂停
  │                          （选「代写大纲」→ 跑一次 agent 流写大纲 → 重查 G0）
  ▼
G1 衔接预检（orchestrator 服务层直连 LLM，带上一卷末章锚点）
  │──不合理──▶ consult.request（问题清单 + 修改建议 / 显式确认强制跳过 → 落审计）
  │合理
  ▼
G2 生成（★唯一进入 deep agent 的阶段）
  │  writer 子代理：章简介 + build_recap → 正文，用文件工具写入
  │  ★写入前先把旧正文快照进 chapter_revisions(source="agent")，保证可回滚
  │  期间可触发 WriteApproval 参数级审批 → awaiting_approval
  ▼
G3 匹配后检（服务层直连 LLM，比对正文与简介的要点覆盖 / 偏离）
  │──不匹配──▶ 自动重生成（≤2 次，每次重生前同样先快照）
  │            仍不匹配 → consult.request（认可现状 / 手动修改 / 补充指示后再生成）
  │匹配
  ▼
完成：summarize_chapter 生成摘要 → 更新派生索引 → run.end
```

**执行主体分工**：G0 / G1 / G3 由 `gates/orchestrator.py` 服务层直连 ChatModel 完成
（可预测、可单测、成本低，**不进入 Agent 循环**）；只有 G2 进 deep agent。
`agent_runs` 的 `gate_stage / gate_retry_count / gate_result_json` 全程流转，前端据此渲染门禁状态条。

#### ③ 中断与恢复（HITL）

```
Agent 触发中断（工具级 interrupt_on / 参数级 WriteApproval / 门禁咨询）
  ▼
落 agent_interrupts(type=approval|consult) + 发 interrupt.request|consult.request
  → run.status = awaiting_approval|awaiting_consult，会话同状态，run.end(paused=true)
  ▼
前端渲染审批卡片 / 咨询卡片 → POST /agent/interrupts/{id}/resolve
  │  approval: approve | edit(带 edited_args) | reject(带 message) | respond(带 message)
  │  consult : option(带 choice) | text(带 text)
  │  （对已结束 run 的 resolve → 409 / 40902）
  ▼
services.agent 归一化为 langchain batched resume 形状 {"decisions":[Decision]}
  → 落库决定 + 审计 → run 置 running → 异步 run_manager.resume()
  ▼
sse_bus.reopen()（finished=False，seq 续增，缓冲保留 → 客户端无缝续传）
  → Command(resume=...) 续跑同一 run（thread_id 不变）
  → 门禁运行按 state.pending_consult 路由：consult 走门禁 resume，approval 走 graph Command
```

#### ④ 取消

`POST /agent/runs/{id}/cancel` → 置取消标志（**协作式**：当前工具 / 模型调用返回后停）
→ 给 3 秒优雅窗口，超时硬取消任务 → deepagents 内置 `PatchToolCallsMiddleware` 自动修复被截断的消息历史
→ run 置 `cancelled`，会话回 `idle`，发 `run.end`。

#### ⑤ 文件写入的两条路径（都收敛到同一处）

```
路径 A：前端编辑 → PUT /chapters/{cid}/content（乐观锁 base_version）
路径 B：Agent 工具 → write_file / edit_file（permissions + WorkspaceGuard）
                        │
                        ▼
        services/workspace.py：WorkspaceGuard（服务层唯一文件写入口）
        ├─ resolve() 归一化后必须严格落在白名单根内，否则 403 / 40301
        ├─ 拒绝含 .. 的路径、拒绝解析后越界的符号链接（逐段检查）
        ├─ 跨项目写一律拒绝；summaries/ 只允许 summarize_chapter 写
        └─ 写入成功后同步更新派生索引（story_fts / project_index）
             失败不阻断写入 → 标 project_index.dirty=1 + 告警
             → writer rebuild-index / POST /novels/{id}/index/rebuild 全量重建
```

#### ⑥ 应用启动（lifespan）

```
uvicorn 启动 → app/main.py lifespan
  ├─ init_database()：create_all 建表 + 打 alembic 版本戳（两条初始化路径互相幂等）
  │                   + FTS5 两步探测建 story_fts（不可用则永久降级 LIKE）
  ├─ 播种内置子代理（seed_builtins，幂等：已存在按 name 跳过，不覆盖用户改动）
  ├─ 内置技能一致性检查（skill_configs ↔ 包内 SKILL.md）
  ├─ 挂载前端静态资源（src/web/dist，SPA history 回落）
  └─ 统一异常处理 / CORS / 结构化日志（字段名含 key|token|secret 自动掩码）
```
