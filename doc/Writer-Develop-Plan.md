小说

# AI Writer Pro —— 整体功能设计蓝图

---

# 第一部分：决策确认（请重点核对）

## 1.1 已敲定的技术决策

| #  | 项         | 决策                                                                                                             |
| -- | ---------- | ---------------------------------------------------------------------------------------------------------------- |
| 1  | Agent 引擎 | `deepagents` 单引擎（`create_deep_agent`），**不引入** claude-agent-sdk                                |
| 2  | 前端框架   | Vue 3 + Vite + TypeScript + Pinia + **Element Plus**                                                       |
| 3  | 正文编辑器 | **Tiptap（ProseMirror）**：支持选区改写、段落级 diff、AI 来源标记、对选中文字段落用AI改写                  |
| 4  | 用户体系   | **无登录**、**无后台管理端**；单用户本地使用，通用设置（主题/字号等）与 AI 工具配置持久化到 DB       |
| 5  | 知识库 RAG | **Markdown 文件为事实源 + SQLite FTS5 派生索引**（不用向量库，零 Embedding 费用）                          |
| 6  | 可观测性   | **Langfuse**（Trace / Span / Generation / Score）  用于检测Agent运行中状态                                 |
| 7  | 流式通道   | **SSE** 单向推送；打断与审批走 REST 控制接口                                                               |
| 8  | 沙箱隔离   | **小说项目间半隔离**：可互相 `read`，不可互相 `write`；项目区之外完全隔离                              |
| 9  | 天气数据源 | **接入开源免费 MCP**：内置 FastMCP 天气服务（封装 Open-Meteo，免 Key），并支持第三方 MCP                   |
| 10 | 交付节奏   | **分阶段 MVP**（M0 → M7）                                                                                 |
| 11 | 目录结构   | 严格按手册：`src/app`（后端）+ `src/web`（前端），调整 pyproject 构建配置                                    |
| 12 | 导出格式   | **Markdown、EPUB**                                                                                         |
| 13 | 其他       | UI 默认中文；LLM 支持主流厂商与主流调用方式（OpenAI 兼容 / Anthropic / Google / Azure / OpenRouter 等） |

## 1.2 我对你回答做的两处具体化解释（**若不同意请改这里**）

### 解释 A：「用户设置不要」的边界

- **仍然实现并持久化**（手册明确要求的内容）：
  - **通用设置**：页面主题颜色、暗色模式、小说字体大小、小说字体风格、阅读页宽、自动保存间隔；
  - **AI 工具设置**：大模型 API 设置、写作 sub-Agent、写作 Skill、MCP Server 管理。
- **去掉**：个人资料中心（头像/昵称/偏好管理页）、后台管理端（Admin Console）。
- **审计日志**：按 README 要求保留为 **数据表 + 只读查询接口**，不做后台 UI。

### 解释 B：「项目间半隔离」的技术落点

用 deepagents 原生 `permissions=[FilesystemPermission(...)]` 表达：

| 路径范围                        | read           | write          |
| ------------------------------- | -------------- | -------------- |
| 当前项目 `/projects/{pid}/**` | allow          | allow          |
| 其它项目 `/projects/**`       | allow          | **deny** |
| 项目区之外 `/**`              | **deny** | **deny** |

> ⚠️ **重要限制**：官方文档明确 `permissions` **只作用于内置文件系统工具**（`ls / read_file / write_file / edit_file / glob / grep / delete`），
> **不覆盖自定义工具与 MCP 工具**。因此本项目所有自研写作工具必须在服务层再走一遍统一校验（`WorkspaceGuard`），否则隔离会被绕过。
> 该项已列入「风险与对策」并要求配套越权单元测试。

### 其它默认假设（未特别询问，如不同意请标注）

- UI 语言默认**中文**，文案集中管理以便后续双语；
- **不开放 `execute`（任意命令执行）**，Agent 只能用文件工具与写作工具；
- 数据落盘位置：`data/app.db`、`data/agent_state.db`、`data/workspaces/{project_id}/`、`data/exports/`、`data/logs/`（`data/` 不入版本库）；
- 小说正文与知识库以 **Markdown 文件为唯一事实源**，SQLite `story_fts` 等表是从文件构建的**派生索引**（可随时删除重建）；
- AI Agent 运行时的消息/会话/运行日志以 **SQLite** 存储（`data/app.db`）；
- Web 应用运行日志以 **JSONL** 文本文件存放（`data/logs/`），按日期轮转；
- 依赖调整：保留但不使用 `lmnr`（改用 Langfuse）、保留但不使用 `claude-agent-sdk`（改用deepagents）；构建后端由 `uv_build` 改为 `hatchling`（因 `src/app` 不是合法 Python 包名，`uv_build` 的模块名约定不适配）；
- 运行环境：项目要求 Python ≥ 3.12，本机默认 `python` 为 3.8 / conda base，**必须使用 `uv sync` 生成的 `.venv` 或 `uv run`**，`.vscode/settings.json` 需由 conda 切换到 uv/.venv。

---

# 第二部分：现状与设计依据

## 2.1 仓库现状

- `src/` 为空（`src/ai_agent_writer_pro/__init__.py` 已在工作区删除），全新起步；
- `demo/` 下 4 个脚本已验证：原生 Agent-Loop 原理（`demo.py`）、deepagents 用法（`demo-deepagents.py`）、Langfuse 追踪范式（`demo-langfuse.py`）、Laminar（`demo-laminar.py`，本次不采用）；
- 依赖已锁定：`deepagents 0.7.23`、`langchain 1.4.7`、`langchain-openai 1.5.1`、`langgraph 1.2.16`、`langfuse 5.0.4`、`pydantic 2.14.1`、`uvicorn 0.54.0`（传递依赖）；
- `doc/web参考.jpg` 提供了三栏布局参考（左：宠物+城市天气 / 新建删除小说 / 项目列表 / 左下设置与 AI 工具；中：阅读模式+编辑模式 / 目录可展开 / 正文；右：Agent 图标 / 会话 / 输入框）；

## 2.2 deepagents 能力与本项目诉求的映射（已核对官方文档）

| 手册诉求   | deepagents 原生能力                                                                                                                       |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Agent-Loop | `create_deep_agent` 内置工具调用循环（基于 LangGraph），支持 `astream` / `stream(subgraphs=True)`                                   |
| Tools      | `tools=` 接受普通函数、LangChain Tool、**任意 MCP Server** 工具                                                                   |
| Skill      | `skills=["..."]`，遵循 Agent Skills 规范（目录 + `SKILL.md` YAML frontmatter：`name/description`）                                  |
| Sub-agent  | `subagents=[{name, description, system_prompt, tools, model, skills, interrupt_on, mode}]`，支持 `isolated` / `fork` 两种上下文模式 |
| 沙箱       | 可插拔 backend：`StateBackend` / `FilesystemBackend` / `StoreBackend` / `CompositeBackend`（按路径路由）/ sandbox                 |
| 半隔离     | `permissions=[FilesystemPermission(operations, paths, mode=allow                                                                          |
| 会话持久   | LangGraph checkpointer（`AsyncSqliteSaver`），按 `thread_id` 维度                                                                     |
| 人为介入   | `interrupt_on={tool: True                                                                                                                 |
| 上下文压缩 | 内置 summarization + 大工具输出卸载到文件                                                                                                 |

> **M0 必须先做 spike**：deepagents 0.7.23 与文档（部分行为标注需 ≥0.7.3）存在细节差异，需实测 `create_deep_agent` 的真实签名后再定型 `agent/loop/builder.py`。

---

# 第三部分：完整开发手册正文（核对通过后写入 `Writer-Develop.md`）

## 一、总体设计原则

1. **不自研 Agent 框架**：`deepagents` 已提供 Agent-Loop、子代理、技能、虚拟文件系统、上下文压缩、HITL、checkpointer 持久化与流式；本项目只做「写作领域化」封装与业务编排。
2. **C/S 分离**：前端 `src/web`（Vue SPA），后端 `src/app`（FastAPI）。REST 负责数据操作，SSE 负责 Agent 流式输出，控制类操作（打断 / 审批）走 REST。
3. **文件为事实源**：小说正文与设定集以 Markdown 文件为唯一事实源；SQLite 仅存 Agent 消息/会话/审计等结构化数据；应用运行日志用 JSONL 文件存放。
4. **单用户 + 项目级隔离**：无登录鉴权，单用户本地使用；Agent 对项目文件「半隔离」（跨项目可读、不可写，项目外完全隔离）。
5. **可观测**：Langfuse 全链路追踪（未配置 Key 时自动降级为本地结构化日志，不影响功能）。

## 二、怎么设计 AI Agent

### （1）LLM 模块

负责 LLM 的正常接入使用（参数可设置）与异常处理。

**多厂商适配**（统一走 LangChain ChatModel）：

| 类型                  | 厂商                                                                   | 实现                                |
| --------------------- | ---------------------------------------------------------------------- | ----------------------------------- |
| `openai_compatible` | DashScope/Qwen、DeepSeek、Moonshot、智谱、硅基流动、vLLM、任意自建网关 | `ChatOpenAI(base_url=...)`        |
| `openai`            | OpenAI                                                                 | `ChatOpenAI`                      |
| `anthropic`         | Claude                                                                 | `ChatAnthropic`                   |
| `google`            | Gemini                                                                 | `ChatGoogleGenerativeAI`          |
| `azure_openai`      | Azure OpenAI                                                           | `AzureChatOpenAI`                 |
| `openrouter`        | OpenRouter 聚合                                                        | `ChatOpenAI(base_url=openrouter)` |

**参数设置**：`temperature / max_tokens / top_p / timeout / max_retries / stream / extra_body`，四层覆盖优先级：**全局默认 → 项目级 → 会话级 → 子代理级**。

**异常处理（harness 关键点）**：

- **请求超时**：per-provider `timeout` + 全局兜底；
- **超时重试**：指数退避 + 抖动，仅对可重试错误（超时 / 429 / 5xx / 连接错误）；
- **最大调用次数**：LangGraph `recursion_limit` + 单次运行 LLM 调用计数器，超限优雅收尾并回报；
- **上下文压缩**：token 估算超阈值 → 触发内置 summarization + 长工具输出卸载为工作区文件 + 用「前情提要」替代逐章原文；
- **重复错误避免**：对 `(tool_name, args_hash, error_fingerprint)` 记忆，二次失败即注入告警，三次阻断该调用路径；
- **降级处理**：模型降级链（如 `qwen3.7-plus → qwen-flash → 备用 Provider`），全失败返回可读错误并落审计。

**密钥安全**：API Key 用 Fernet 加密入库（密钥来自 `.env`），接口返回与日志一律脱敏（`sk-****abcd`），提供「测试连接」接口（发一次极小请求验证）。

### （2）Agent-Loop 模块

掌管 Agent 的调用运行：接入 LLM、各类工具、各类钩子函数（节点检查、人为介入）。

- 由 `create_deep_agent(...)` 提供主循环（思考 → 工具 → 观察），异步执行；
  `thread_id = novel:{project_id}:session:{session_id}`。
- **流式**：LangGraph 事件流（`subgraphs=True` 以获取子代理事件），映射为 SSE 事件：
  `run.start / message.delta / message.complete / tool.start / tool.result / subagent.start|delta|end / plan.update / context.compact / interrupt.request / usage / error / run.end`
- **钩子（全局钩子模块）**：
  - `before_model / after_model`：注入前情提要、token 预算检查、敏感内容检查；
  - `before_tool / after_tool`：`WorkspaceGuard` 路径校验、审计埋点、耗时与结果大小统计、错误指纹记录；
  - `on_node_end`：章节被工具修改后实时推送前端（正文热更新）；
  - `on_run_end`：写入 Langfuse score（字数、工具成功率、是否降级）。
- **人为介入（HITL）**：`interrupt_on` 对高危操作暂停等待审批——删除卷/章、整章覆盖写入、批量改名、修改关键设定实体；前端渲染审批卡片，支持 **批准 / 编辑后批准 / 拒绝 / 回复补充**；`POST /agent/interrupts/{id}/resolve` 恢复运行。
- **取消**：`POST /agent/runs/{run_id}/cancel`，协作式取消 + `PatchToolCallsMiddleware` 自动修复消息历史。

### （3）Tools 模块

提供并接入各种三方能力：内置工具、Skill、MCP、Sub-agent。

**内置写作工具**（`@tool`，全部经 `WorkspaceGuard` 路径校验 + 文件操作后同步更新派生索引）：

- **目录**：`list_volumes / create_volume / rename_volume / delete_volume / reorder_volumes`
- **章节**：`list_chapters / create_chapter / rename_chapter / delete_chapter / reorder_chapters`
- **正文**：`read_chapter / write_chapter(mode=overwrite|append|insert_after) / edit_chapter(old_str,new_str) / get_chapter_diff`
- **设定**：`search_novel_settings / get_entity / upsert_character / upsert_location / upsert_item / upsert_plot_thread / get_outline / update_outline`
- **上下文**：`get_recent_summaries / build_recap / summarize_chapter / get_word_count / get_writing_stats`

**Skills（写作技能）**：`src/app/skills/<name>/SKILL.md`（YAML frontmatter：`name / description`）+ 可选参考资料与模板。
内置：`novel-planning`（大纲与分卷节奏）、`prose-writing`（文风与场景写法）、`line-editing`（润色去水）、`continuity-check`（伏笔 / 时间线 / 人物一致性）、`naming`（人名地名功法命名）、题材类技能（玄幻 / 都市 / 奇幻）。
用户可在「AI 工具 → 写作 Skill 设置」中新建 / 编辑 / 启停，存 DB 并物化到工作区 `skills/` 供 deepagents 加载。

**Sub-agents（写作子代理）**：

| 名称                   | 职责                                               |
| ---------------------- | -------------------------------------------------- |
| `planner`            | 策划：题材定位、三级大纲、分卷节奏、伏笔布局       |
| `writer`             | 正文撰写：按大纲 + 前情提要 + 文风约束生成章节     |
| `editor`             | 润色校对：去水词、修病句、统一称谓与语气           |
| `continuity-checker` | 连贯性审查：时间线、人物状态、设定冲突、未回收伏笔 |
| `researcher`         | 资料检索：通过 MCP 获取外部资料                    |

每个可配置：模型覆盖、专属提示词、工具白名单、可用技能、是否需审批。默认保留 `general-purpose`。

**MCP**：

- 内置**免费开源天气 MCP Server**（FastMCP 实现，封装 **Open-Meteo**：免费、无需 Key、覆盖中国城市，含 Geocoding），提供 `get_current_weather(city)` / `get_forecast(city, days)`，同时服务于左上角动态组件与 Agent；
- 「AI 工具」面板支持添加**第三方 MCP Server**（stdio 命令 或 streamable-http / SSE URL），启动时探测工具列表并可启停；
- MCP 工具与内置工具统一注册进 `tools=`。

### （4）Message 模块

记录并处理 Agent 运行时各种「消息」，实现会话持久（项目消息树）。

- **消息树**：`messages.parent_id + branch_index`，会话保存 `active_leaf_id`。
  - 「重新生成」= 同父节点下新增兄弟分支；「编辑用户消息后重发」= 新建分支；前端可切换分支查看历史版本；
  - 构造 LLM 上下文时只取 `root → active_leaf` 的活跃路径。
- **持久化双轨**：
  1. 业务库 `messages / tool_call_logs / agent_runs`：供前端渲染、审计、导出；
  2. LangGraph **AsyncSqliteSaver** checkpoint：供 Agent 恢复运行、HITL 中断续跑；
     以 `run_id / thread_id` 关联，运行结束做幂等一致性校验。
- **隔离**：会话严格按项目隔离（`agent_sessions.project_id`），右侧栏仅展示当前项目会话；一个项目可有多会话（如「大纲会话」「第 3 卷撰写会话」）。
- **运行日志**：`agent_runs`（状态、耗时、token 用量、模型、降级次数、错误、Langfuse trace id）。

### （5）其他模块

- **沙箱（半隔离）**：`CompositeBackend` 路由 + `FilesystemPermission` 规则

  ```python
  permissions=[
    FilesystemPermission(operations=["read","write"], paths=[f"/projects/{pid}/**"], mode="allow"),
    FilesystemPermission(operations=["write"],        paths=["/projects/**"],         mode="deny"),   # 其它项目只读
    FilesystemPermission(operations=["read"],         paths=["/projects/**"],         mode="allow"),  # 允许跨项目阅读
    FilesystemPermission(operations=["read","write"], paths=["/**"],                  mode="deny"),   # 项目区外完全隔离
  ]
  ```

  高危写入可进一步用 `mode="interrupt"` 转人工审批。**注意：permissions 只作用于内置文件系统工具**，自研工具必须由 `WorkspaceGuard` 二次强制同一套规则。不开放 `execute`（无任意命令执行）。
- **知识库 RAG**：见「四、故事知识库」。
- **提示词模块**：Jinja2 模板。主代理系统提示 = 角色设定 + 项目元信息 + 大纲 + 前情提要 + 已启用技能摘要 + 输出规范（**修改正文必须调用工具，不得只在回答里贴文本**）。
- **配置模块**：`.env`（端口、密钥、DB 路径、Langfuse、数据根目录）+ DB 运行期配置（Provider / 模型 / 子代理 / 技能 / MCP / 通用设置）；配置变更热生效（Agent 工厂按 session 惰性构建 + 版本号失效缓存）。
- **远程 API 模块**：统一 `httpx` 异步客户端（超时、重试、限流、缓存），供天气 / 地理编码等外部服务使用。

### 关键 harness 设计一览

`LLM 参数设置 + 异常处理` · `Agent-Loop 钩子与询问（HITL）` · `主 + 子智能体切换与上下文隔离` · `小说项目会话持久（消息树 + checkpointer）` · `小说项目沙箱半隔离（permissions + WorkspaceGuard）` · `知识库 RAG（文件事实源 + FTS5 派生索引 + 前情提要）` · `Langfuse 可观测`

## 三、怎么开发 Writer 应用

### （1）工程结构

- 前端网站页面放在 `src/web`，使用 **Vue 3 + Vite + TypeScript + Pinia + Element Plus**，正文编辑器用 **Tiptap（ProseMirror）**。
- 前端接口及 AI Agent 包装为后端服务，放在 `src/app`，采用 **FastAPI**。
- 前后端 C/S 架构通信（REST + SSE），便于生产环境分离部署（前端 Nginx / 后端 uvicorn，或 FastAPI 挂载静态资源单体部署）。
- 运行期数据统一放在 `data/`（不入版本库）：`data/app.db`、`data/agent_state.db`、`data/workspaces/{project_id}/`、`data/exports/`、`data/logs/`。
- Python ≥ 3.12，依赖用 `uv sync` 管理，必须使用项目 `.venv`（勿用系统 python）。

**后端目录（`src/app`）**

```
src/app/
├── main.py                     # 应用装配、lifespan、路由/静态资源挂载、CORS
├── core/
│   ├── config.py               # pydantic-settings，读取 .env
│   ├── security.py             # 密钥加密(Fernet)，用于 API Key 加密存储
│   ├── errors.py               # 统一异常 + 全局异常处理器
│   ├── response.py             # 统一响应包装 {code,message,data}
│   ├── logging.py              # 结构化日志（输出到 JSONL 文件）、敏感信息脱敏
│   └── deps.py                 # 依赖注入（当前用户、DB session、服务实例）
├── db/
│   ├── session.py              # async engine / sessionmaker (aiosqlite)
│   ├── base.py                 # DeclarativeBase、命名约定
│   ├── models/                 # ORM 模型（见「五、数据模型」）
│   ├── fts.py                  # FTS5 建表/触发器/降级方案
│   └── migrations/             # Alembic
├── schemas/                    # Pydantic DTO（请求/响应/流事件）
├── api/
│   ├── novels.py               # 项目 CRUD、导出
│   ├── volumes.py chapters.py  # 卷/章 CRUD、正文读写、批量重排
│   ├── story.py                # 小说设定、大纲、章节摘要
│   ├── agent.py                # 会话、发消息(SSE)、打断、审批、消息树、模型切换
│   ├── llm.py                  # Provider/Model 管理、连接测试
│   ├── skills.py subagents.py mcp.py
│   ├── settings.py             # 通用设置（主题/字号/字体）
│   ├── widgets.py              # 左上角动态组件数据
│   └── audit.py                # 审计日志（只读）
├── services/                   # 业务编排层（事务边界、审计埋点）
├── agent/                      # ★ AI Agent 核（对应五模块）
│   ├── llm/                    # registry / factory / resilience / budget
│   ├── loop/                   # builder / runner / events / hooks
│   ├── tools/                  # novel_tools / chapter_tools / story_tools / context_tools / registry
│   ├── subagents/  skills/  mcp/
│   ├── memory/                 # 消息树、会话持久化、上下文构建
│   ├── knowledge/              # 文件读写、派生索引（FTS5）更新、章节摘要生成、实体抽取
│   ├── sandbox/                # 项目工作区 + WorkspaceGuard + permissions 规则
│   ├── prompts/                # 提示词模板（主代理/子代理/摘要/续写）
│   └── tracing/                # Langfuse 集成
└── skills/                     # 内置写作技能（每个子目录一个 SKILL.md）
```

**前端目录（`src/web/src`）**

```
├── main.ts  App.vue  router/  stores/(pinia)  types/
├── api/            # axios 封装 + 各模块接口 + SSE 客户端（seq / Last-Event-ID 续传）
├── layouts/        # MainLayout：三栏可伸缩布局
├── views/          # WorkspaceView / SettingsView
├── components/
│   ├── sidebar/    ProjectList · NewNovelButton · DynamicWidget(宠物+城市+天气) · SidebarFooter(设置·AI工具入口)
│   ├── reader/     ReaderToolbar(阅读/编辑切换) · TocDrawer(目录展开收回) · ChapterReader
│   ├── editor/     TiptapEditor · VolumeChapterTree(增删改/重命名/拖拽) · WordCountBar · AutosaveIndicator
│   ├── agent/      AgentPanel · MessageList(消息树) · MessageBubble · ToolCallCard · SubAgentLane ·
│   │               InterruptApprovalCard · ContextChips(子代理/技能/章节) · ModelSwitcher · ChatInput
│   ├── settings/   ThemeSettings · FontSettings · LlmProviderForm · SubAgentManager · SkillManager · McpManager
│   └── common/     DiffViewer · ExportDialog · EmptyState
└── styles/         # CSS 变量驱动主题色/字号/字体（含暗色）
```

### （2）审计

- **审计日志**：`audit_logs` 记录关键动作（项目/卷/章增删改、导出、Agent 运行、工具调用、审批决定），仅提供只读查询接口，无管理 UI。
- **通用设置**保留（主题色、暗色、正文字号、字体风格、阅读页宽、自动保存间隔），按用户持久化。

### （3）页面功能

**左侧栏**

- 最上方动态组件：**宠物动态 icon**（Lottie/SVG 动画状态机：写作中→敲键盘、久无进展→打瞌睡、达成日目标→欢呼）+ **所在城市** + **天气**（数据来自内置 Open-Meteo MCP）。
- 组件下方：【新建小说】/【删除小说】。
- 再下方：各小说项目列表（标题、字数、更新时间；右键菜单：重命名、导出、复制、删除）。
- 左下角：【通用设置】与【AI 工具】按钮入口。
- **小说文件导出到本地**：
  - **Markdown**：整本单文件 / 分章 zip；
  - **EPUB 3**：自研 `zipfile` 生成器（`mimetype` / `META-INF/container.xml` / `OEBPS/content.opf` / `nav.xhtml` / 各章 xhtml），不引入重型依赖。

**通用设置**：页面主题颜色（预设 + 自定义主色 + 暗色）、小说字体大小、小说字体风格（宋体 / 黑体 / 楷体 / 自定义）、阅读页宽、自动保存间隔。

**AI 工具**：大模型 API 设置（Provider CRUD、连接测试、默认模型、降级链、参数）、写作 sub-Agent 管理、写作 Skill 管理、MCP Server 管理。

**主页面（中间栏）**

- 最上方：【阅读模式】/【编辑模式】切换。
- **阅读模式**：展示小说目录与正文（类阅读软件），目录可展开收回；支持章节翻页、进度记忆、夜间模式。
- **编辑模式**：Tiptap 修改正文（Markdown 快捷输入、选中浮层「AI 改写 / 扩写 / 缩写 / 润色」、AI 写入段落带来源标记与 diff 查看 / 接受 / 回滚）；增删卷章、修改卷章名称、拖拽排序；字数统计与自动保存。

**右侧栏（Agent 面板）**

- 点击右上角 Agent 图标展开 / 收回。
- 展示与 AI 沟通的会话内容，**不同小说项目会话隔离**，仅展示当前项目会话；支持一个项目多会话。
- 消息流渲染：用户消息、AI 回复（流式）、工具调用卡片（可折叠、含耗时与结果摘要）、子代理泳道、思考/计划摘要、审批卡片。
- 对话输入窗口：可**主动添加 写作 sub-Agent、写作 Skill、阅读过的小说章节**（以及人物卡 / 大纲）作为上下文 Chips；可**切换本次使用的模型**。
- 支持打断运行、重新生成（消息树分支）、复制、导出会话。

## 四、故事知识库（RAG，文件事实源 + FTS5 派生索引）

- **实体建模（全部以 Markdown 文件存放，YAML frontmatter 承载元数据）**：`world_build/<kind>/*.md`（kind：character / location / item / faction / plot_thread / setting；含别名、标签、正文卡）、`outline/*.md`（书 / 卷 / 弧线三级大纲）、`summaries/ch-{id}.md`（章节摘要 + 关键事件 + 出场实体）。
- **抽取**：章节定稿或 Agent 写完后触发 `summarize_chapter`（LLM 生成 200–400 字摘要），新实体自动 `upsert`（可人工确认）。
- **检索**：`search_novel_settings(query, kind?, limit)` 走 SQLite **FTS5**（`MATCH` + `bm25()`）；中文用**二元切分 / `trigram` tokenizer** 兜底，检索异常降级 `LIKE`。不使用向量库（零 Embedding 费用、零额外服务）。
- **前情提要 `build_recap`**：最近 N 章摘要 + 当前卷大纲 + 相关实体卡 + 未回收伏笔 → 组装上下文块注入系统提示；预算不足时按「**大纲 > 最近 3 章 > 相关实体 > 更早摘要**」优先级裁剪。
- **前置验证（M0/M1）**：确认运行环境 `sqlite3` 已编译 FTS5（`SELECT fts5(?)`）；不可用则自动切 `LIKE` 降级实现，接口不变。

## 五、数据模型（SQLite + SQLAlchemy 2.0 async）

### SQLite 存储（结构化数据）

```
audit_logs(id, action, entity_type, entity_id, detail_json, ip, user_agent, created_at)

llm_providers(id, name, type, base_url, api_key_enc, extra_json, enabled, priority, created_at)
llm_models(id, provider_id, model_id, display_name, max_context, supports_tools,
           default_params_json, is_default, enabled)

agent_sessions(id, project_id, title, active_leaf_id, model_ref, subagent_ids_json,
               skill_ids_json, context_chapter_ids_json, status, created_at, updated_at)
messages(id, session_id, parent_id, branch_index, role, content_json, tool_calls_json,
         status, tokens_in, tokens_out, model, created_at)
tool_call_logs(id, message_id, session_id, tool_name, args_json, result_excerpt,
               status, error, duration_ms, created_at)
agent_runs(id, session_id, project_id, status, model_ref, recursion_used,
           tokens_in, tokens_out, fallback_count, langfuse_trace_id, error, started_at, ended_at)
agent_interrupts(id, run_id, session_id, tool_name, payload_json, decision, decided_by, decided_at)

subagent_configs(id, name, description, system_prompt, model_ref,
                 tools_json, skills_json, enabled, is_builtin, created_at)
skill_configs(id, name, description, content_md, assets_json, enabled, is_builtin)
mcp_servers(id, name, transport(stdio|http|sse), command_json, url, headers_enc, enabled)
user_settings(id, theme, primary_color, font_size, font_family, reading_width,
              city, widget_enabled_json, updated_at)
```

### 文件存储（小说与知识库，事实源）

```
data/workspaces/{project_id}/
├── novel.yaml                 # 项目元信息：title, author_name, genre, synopsis, cover_url, status
├── volumes.yaml               # 卷列表：id, title, summary, sort_order
├── chapters/
│   └── v{vol_id}/c{chapter_id}.md  # 章节正文（带 frontmatter：title, sort_order, word_count, status, version, updated_at）
│
├── world_build/                # 小说设定
│   ├── characters/*.md         # 人物卡（frontmatter：kind, aliases, tags, first_chapter_id）
│   ├── locations/*.md
│   ├── items/*.md
│   ├── factions/*.md
│   ├── plot_threads/*.md
│   └── worldsettings/*.md
│
├── outline/                   # 大纲
│   ├── book.md                # 书级大纲
│   ├── vol-{id}.md            # 卷级大纲
│   └── arc-{id}.md            # 弧线大纲
│
└── summaries/                 # 章节摘要
    └── ch-{chapter_id}.md     # frontmatter：key_events, entities, model, created_at
```

### 派生索引（SQLite，从文件重建）

```
story_fts                      # FTS5 虚拟表(entity_id, kind, name, aliases, content)
                               # 从 world_build/summaries/ 文件构建，服务层文件变更时异步更新
project_index(project_id, chapter_count, total_words, last_updated)  # 项目统计缓存
```

### JSONL 文件存储（应用运行日志）

```
data/logs/
├── app-{date}.jsonl           # 应用运行日志（structlog 输出，按日期轮转）
├── error-{date}.jsonl         # 错误日志
└── audit-{date}.jsonl         # 审计日志备份（同时存 SQLite audit_logs 表）
```

**文件与 DB 的关系**：小说正文与知识库以 **Markdown 文件为唯一事实源**；工作区文件由 Agent 文件工具直接读写；服务层在文件变更时同步更新 SQLite 派生索引（`story_fts`、`project_index`）；前端渲染与导出从文件读取。Agent 消息、会话、审计等结构化数据存 SQLite，不涉及文件。

## 六、API 约定（REST + SSE，统一前缀 `/api/v1`）

```
认证     （无，单用户本地应用，无需认证）
项目     GET|POST /novels        GET|PATCH|DELETE /novels/{id}
         POST /novels/{id}/export?format=markdown|epub
卷章     /novels/{id}/volumes ... /volumes/{id}/chapters ... /chapters/{id}/content
         GET /chapters/{id}/revisions
小说设定 /novels/{id}/entities | /entities/search?q= | /outline | /summaries/rebuild
LLM      /llm/providers (+ /{id}/test) | /llm/models
Agent    /novels/{id}/sessions | /sessions/{sid}/messages | /sessions/{sid}/branch
         POST /sessions/{sid}/messages → 202 + run_id
         GET  /sessions/{sid}/runs/{run_id}/stream      ★ SSE
         POST /runs/{run_id}/cancel | /agent/interrupts/{id}/resolve
         PATCH /sessions/{sid}/context (模型/子代理/技能/上下文章节)
配置     /subagents | /skills | /mcp-servers (+ /{id}/probe) | /settings | /widgets/dashboard | /audit-logs
```

- 统一响应包装 `{code, message, data}`；
- SSE 事件信封 `{event, run_id, session_id, seq, ts, data}`，断线用 `Last-Event-ID` + `seq` 续传（服务端保留最近事件环形缓冲）。

## 七、分阶段实施路线

| 阶段         | 目标                                                                                                                                                                                                                                                                                                                             |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **M0** | 脚手架与地基：pyproject 改造（hatchling + `src/app`）、`uv sync`、FastAPI 骨架（统一响应/异常/日志/CORS/lifespan）、SQLite + Alembic + FTS5 可用性验证、工作区目录结构约定、JSONL 日志配置、Vue 脚手架（Vite+TS+Element Plus+Pinia+Router+axios+SSE 客户端）、三栏布局空壳、`.env.example`、**deepagents API spike** |
| **M1** | 小说基础功能：项目 CRUD（`novel.yaml` + `volumes.yaml` 文件读写）、卷/章 CRUD（`chapters/` 目录文件操作）、阅读模式（从文件读取）、编辑模式（Tiptap + 自动保存 + 字数统计 + 卷章树）、通用设置、Markdown & EPUB 导出                                                                                                       |
| **M2** | 审计与配置：审计日志埋点（SQLite `audit_logs` + JSONL 备份）与只读接口、通用设置页、AI 工具配置页                                                                                                                                                                                                                              |
| **M3** | Agent 核 v1（端到端打通）：LLM Provider/Model 管理 + 连接测试 + 降级链；deepagents 工厂（CompositeBackend、permissions 半隔离、AsyncSqliteSaver、WorkspaceGuard）；写作工具集（直接操作文件）；SSE 流式 + 右侧会话面板；消息树与运行日志（SQLite）；Langfuse 追踪                                                                |
| **M4** | 知识库与连贯性：小说设定（`world_build/` 目录文件读写）+ FTS5 派生索引 + 检索工具、章节摘要自动生成（`summaries/` 文件）、`build_recap` 注入、上下文压缩与 token 预算                                                                                                                                                            |
| **M5** | 子代理 / 技能 / HITL：写作子代理与管理 UI、内置与自建技能、审批流、子代理并行流与泳道展示                                                                                                                                                                                                                                        |
| **M6** | MCP 与动态组件：内置 Open-Meteo 天气 MCP + MCP 管理 UI、左上角动态组件（宠物动画 + 城市 + 天气 + 今日统计）                                                                                                                                                                                                                      |
| **M7** | 打磨与交付：Dockerfile + docker-compose、性能优化、错误边界与空态、端到端验收清单、文档定稿                                                                                                                                                                                                                                      |

## 八、依赖清单

**后端新增**：`fastapi`、`uvicorn[standard]`、`sse-starlette`、`sqlalchemy[asyncio]`、`aiosqlite`、`alembic`、`langgraph-checkpoint-sqlite`、`pydantic-settings`、`python-multipart`、`cryptography`、`httpx`、`jinja2`、`mcp`、`langchain-anthropic`、`langchain-google-genai`、`structlog`、`python-frontmatter`、`pyyaml`；构建后端改为 `hatchling`。
**移除**：`lmnr`（改用 Langfuse）、`claude-agent-sdk`（Anthropic 走 langchain-anthropic）。
**前端**：`vue@3`、`vite`、`typescript`、`pinia`、`vue-router`、`element-plus`、`axios`、`@tiptap/vue-3` + `starter-kit` + `placeholder` + `character-count` + `highlight`、`markdown-it`、`dompurify`、`lottie-web`、`dayjs`、`sass`。

## 九、风险与对策

| 风险                                                       | 对策                                                                                                                                                                                   |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| deepagents 版本 API 细节差异（文档部分行为标注 ≥0.7.3）   | **M0 先做最小 spike**：实测 `create_deep_agent` 的 `backend / permissions / checkpointer / subagents / skills / interrupt_on / stream` 真实签名与行为，再定型 `builder.py` |
| FTS5 中文检索效果差                                        | 二元切分 + `trigram` tokenizer + `LIKE` 降级；必要时后续追加可选向量检索（接口不变）                                                                                               |
| Agent 改文件与派生索引不一致                               | 文件为事实源，服务层文件写入后异步更新派生索引；提供索引重建命令（可删除后全量扫描重建）；前端渲染直接读文件                                                                           |
| 跨项目只读被自定义工具绕过（permissions 不覆盖自定义工具） | `WorkspaceGuard` 统一校验 + 越权用例单元测试                                                                                                                                         |
| 长篇上下文爆炸与成本                                       | token 预算 + 前情提要 + 摘要卸载 + 子代理上下文隔离 + Langfuse 用量看板                                                                                                                |
| SSE 断线丢事件                                             | `seq` + `Last-Event-ID` 续传 + 服务端环形缓冲                                                                                                                                      |
| Windows 路径 / 编码                                        | 统一 `pathlib`，文件读写显式 `encoding="utf-8"`                                                                                                                                    |
| 本机默认 python 为 3.8                                     | 强制 `uv sync` / `uv run`，`.vscode` 解释器切到 `.venv`，CI 与文档明示                                                                                                         |

## 十、参考资料

- Deep Agents 官方文档：overview / backends / permissions / subagents / skills / human-in-the-loop / streaming
- 仓库 demo：`demo.py`（原生 Agent-Loop 原理）、`demo-deepagents.py`（deepagents 用法）、`demo-langfuse.py`（Langfuse 追踪范式）
- Open-Meteo（免费开源天气 API）、Model Context Protocol（MCP）规范、Agent Skills 规范

---

# 第四部分：待办清单（已同步到会话 SQL 跟踪）

```
doc-draft    将本草案写入 doc/Writer-Develop-Plan.md      ✅ 完成
doc-review   你核对本草案并反馈修改意见                    ⬅ 当前阶段
doc-sync     按核对结果写入 doc/Writer-Develop.md + 更新 README
M0  m0-backend-scaffold   后端脚手架（FastAPI 骨架 / SQLite+Alembic / FTS5 验证）
    m0-frontend-scaffold  前端脚手架（Vite+Vue3+TS+Element Plus+SSE 客户端+三栏空壳）
    m0-agent-spike        deepagents 0.7.23 API spike
M1  m1-novel-crud / m1-reader / m1-editor / m1-export / m1-settings
M2  m2-audit-config         审计日志、通用设置、AI 工具配置
M3  m3-llm-module / m3-agent-core / m3-message-tree / m3-sse-panel / m3-langfuse
M4  m4-novel-settings      小说设定 + FTS5 + build_recap
M5  m5-subagents-skills / m5-hitl
M6  m6-mcp-weather        内置天气 MCP + MCP 管理 + 左上角动态组件
M7  m7-delivery           Docker、性能、验收清单、文档定稿
```

依赖关系：`doc-draft → doc-review → {doc-sync, M0 三项}`；`M0 → M1 → M2 → M3 → {M4, M5} → M6 → M7`（M1 内部后端接口与前端页面可并行）。

---

## 核对提示（建议按此顺序过一遍）

1. **第一部分 1.2 解释 A / 解释 B** —— 这两处是我对你简短回答做的具体化推断，最容易与你的本意有偏差；
2. **第三部分「二、(3) Tools」** —— 内置写作工具清单是否覆盖你想要的操作（尤其 `write_chapter` 的三种模式、`edit_chapter` 的精确替换）；
3. **第三部分「三、(3) 页面功能」** —— 三栏交互细节是否与 `web参考.jpg` 及你的预期一致；
4. **第三部分「五、数据模型」** —— 字段是否有缺漏（如是否需要「写作目标/日更字数」「章节定时发布」「多语言」「协作」等未纳入的能力）；
5. **第三部分「七、分阶段路线」** —— 里程碑切分与优先级是否需要调整。
