# AI Agent Writer Pro —— 项目技术全景

> 项目开发已完成。本文是**从顶层到底层**的技术全景说明：项目大纲、架构、模块实现逻辑、运行逻辑，
> 以及技术体系与开发重难点。不是开发手册（无待办、无里程碑、无实施步骤）。
>
> 面向读者：需要理解 / 维护 / 二次开发本项目的工程师。
> 快速上手与启动方式见同目录 [`README.md`](./README.md)。
>
> ★**关于代码里的「手册」引用**：`src/app/` 的模块 docstring 中大量出现
> `手册「二、(2) 流式」` 这类章节引用，指的是项目早期的设计手册（已随开发完成而移除）。
> 这些引用仍可按编号对应到本文：手册「二、(1)~(6)」= 本文 [4.6 agent/](#46-agent--ai-agent12-子包)
> 与 [七、开发重难点](#七开发重难点)；手册「三、(1) 工程结构 / 目录边界」= 本文
> [三、项目架构](#三项目架构)；手册「三、(3) 页面功能」= 本文 [3.2 前端结构](#32-前端结构)
> 与 [3.5 前端页面与应用功能设计](#35-前端页面与应用功能设计)；
> 手册「四、知识库」= 本文 [7.5 中文全文检索](#75-难点五中文全文检索) 与
> [4.4 services/](#44-services--业务编排16-模块)；手册「五、数据模型」= 本文
> [3.3 数据架构](#33-数据架构)；手册「六、API 约定」= 本文 [3.4 API 架构](#34-api-架构)。

---

## 目录

- [一、项目定位](#一项目定位)
- [二、项目大纲层次](#二项目大纲层次)
- [三、项目架构](#三项目架构)
- [四、模块实现逻辑](#四模块实现逻辑)
- [五、运行逻辑](#五运行逻辑)
- [六、技术体系](#六技术体系)
- [七、开发重难点](#七开发重难点)
- [八、测试与质量保障](#八测试与质量保障)
- [九、已知边界与可演进方向](#九已知边界与可演进方向)

---

## 一、项目定位

**AI Agent Writer Pro** 是一个大纲驱动的 AI 小说写作工作台，**单用户本地应用**
（无登录鉴权、无后台管理端、审计日志只读）。

它本质上是一个小说编辑器，支持两种写作模式：

1. **手动撰写** —— 人写正文，走常规 CRUD + 乐观锁 + 修订快照；
2. **AI 生成** —— Agent 写正文，但**必须以大纲章简介为前提**（四阶段门禁 G0→G3 硬编排）。

项目要同时回答两个命题：

| 命题 | 答案落点 |
| --- | --- |
| **怎么设计 AI Agent？** | 不自研框架，用 deepagents（LangGraph 之上）做**写作领域化封装**：LLM 接入与韧性、Agent-Loop、工具/技能/子代理/MCP、消息树、沙箱半隔离、门禁编排、可观测 |
| **怎么在 Agent 上开发 Writer 应用？** | C/S 分离：Vue 3 三栏工作台 + FastAPI 后端；**文件为内容事实源，DB 为登记与派生索引**；写作业务下沉为 REST 接口，Agent 只用文件工具碰内容 |

### 设计铁律

1. 不自研 Agent 框架，只做写作领域化封装；
2. C/S 分离（REST 取数 + SSE 流式 + REST 控制）；
3. **文件为内容事实源，DB 为登记与索引**，不一致时以文件为准（可 `rebuild-index` 重建）；
4. 单用户 + 项目级隔离（Agent 跨项目**可读不可写**）；
5. Langfuse 可观测，未配置自动降级；
6. **显式命名**（禁止「未命名小说」「第 1 卷」等默认名，违规 `42201`）；
7. UI 默认中文，文案集中管理；
8. permissions **首条匹配生效**、兜底 `deny /**` 必须最后；
9. **ID 稳定、顺序由大纲派生**（`sort_order` 唯一写入口 `sync_order_from_outline`，无 reorder / move 接口）；
10. 时间戳一律 UTC ISO-8601 应用层写入（不用 SQLite 触发器 / `CURRENT_TIMESTAMP`）；
11. 文件读写一律 `pathlib` + 显式 `encoding="utf-8"`（Windows 兼容）。

其中 8、9、6、11 四条都被**编码成常量 + 配套回归测试**，而非只写在文档里靠人工自觉
（见 [八、测试与质量保障](#八测试与质量保障)）。

---

## 二、项目大纲层次

### 2.1 功能层次（自顶向下）

```
AI Agent Writer Pro
│
├─ 写作业务层（人工撰写，不经 Agent）
│   ├─ 项目管理：小说 CRUD / 软删除 / 复制 / 导出
│   ├─ 卷章管理：卷章 CRUD / 短 ID 恒定 / 顺序由大纲派生
│   ├─ 正文编辑：乐观锁 / 修订快照 / 自动保存 / 选区 AI 改写
│   ├─ 大纲维护：单文件大纲 / 严格解析 / 格式校验 / 顺序回填
│   ├─ 阅读模式：目录 / 翻页 / 进度记忆 / 夜间模式
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
│   │   ├── api/            ① 路由层：14 模块 / 81 端点
│   │   ├── services/       ② 业务编排层：16 模块
│   │   ├── agent/          ③ AI Agent：12 子包（本项目技术核心）
│   │   ├── db/             ④ 数据访问：ORM 模型（按域拆 4 文件）+ 迁移 + FTS
│   │   ├── schemas/        ⑤ DTO：按域拆 4 文件
│   │   ├── core/           ⑥ 基础设施：配置 / 错误码 / 日志 / 路径 / 安全
│   │   ├── main.py         应用装配（lifespan / 路由 / CORS / 异常 / 静态资源）
│   │   └── cli.py          writer CLI（dev / migrate / rebuild-index / doctor）
│   └── web/                前端（Vue 3 + Vite + TS）
├── tests/                  pytest（18 文件 / 399 用例，假模型驱动）
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
                                           │   逻辑（12 子包）│
                                           └─────────────────┘
```

**边界约定**（这是本项目最重要的组织特征，任何改动都应保持）：

| 方向 | 允许性 | 说明 |
| --- | --- | --- |
| `api/ → services/` | ✅ 唯一取数路径 | 14 个 api 模块全部经 services |
| `api/ → agent/` | ✅ **仅运行入口** | 只 `api/agent.py` 持有 `run_manager` + `sse_bus`（起 run / 订阅流）；`api/chapters.py` 持 `rewrite`（服务层直连 LLM 的选区改写） |
| `agent/ → services/` | ✅ 允许方向 | gates / knowledge / loop / mcp 用业务服务（大纲解析、文件写入、索引重建） |
| `services/ → agent/` | ⚠️ **两处文档化例外** | ① `services/agent.py` → `agent.memory`（消息树读写）② `services/search.py` → `agent.knowledge`（章节摘要生成） |
| `core/ → agent/` | ✅ 仅惰性访问器 | `core/catalog.py` 提供函数级惰性读取内置子代理目录，模块加载期不触碰 agent |

`services/ → agent/` 原本有 9 处反向依赖，现已收敛到 6 处（含 P2 范畴的 `llm.factory` /
`tracing` / `mcp.adapter`——它们是 LLM/MCP/可观测**基础设施**，未来若要彻底消环需下沉到 `core/`，
但那会把 Agent 基础设施迁出 `agent/`，与「把 agent 当整体使用」的项目定位冲突，故保留现状）。

---

## 三、项目架构

### 3.1 后端分层职责

| 层 | 模块数 | 职责 | 不做什么 |
| --- | --- | --- | --- |
| `api/` | 14 | 协议转换：路径/查询/请求体解析 → 调 services → `ok()` 包装；SSE 流订阅 | 不写业务规则、不直接查 ORM |
| `services/` | 16 | 业务编排：校验、事务、审计、文件写入、派生索引同步 | 不碰 HTTP 概念（无 Request/Response） |
| `agent/` | 12 子包 | AI Agent 全部逻辑 | 不承载写作业务 CRUD（那些是 REST 接口） |
| `db/` | — | ORM 模型 + async engine + 迁移 + FTS | 不含业务规则 |
| `schemas/` | — | Pydantic DTO（请求体与响应 `data`） | 不含行为逻辑 |
| `core/` | — | 配置 / 错误码 / 响应包装 / 日志 / 路径 / 密钥 | 零业务、零 Agent 知识 |

### 3.2 前端结构

```
src/web/src/
├── views/          WorkspaceView（三栏主工作台）/ SettingsView（通用设置）/ AiToolsView（AI 工具）
├── layouts/        三栏骨架
├── components/
│   ├── sidebar/    ProjectList（项目列表 + 新建/删除/导出）/ WeatherWidget（宠物 + 城市 + 天气）
│   ├── reader/     ChapterReader / OutlineReader / TocDrawer / ReaderToolbar
│   ├── editor/     TiptapEditor / aiSourceMark（AI 来源标记 Tiptap Mark 扩展）
│   │               / RewriteDiffDialog（选区改写句级 diff）/ VolumeChapterTree（只读顺序）
│   │               / OutlineEditor / ConflictBanner（版本冲突合并）/ AutosaveIndicator / WordCountBar
│   ├── agent/      AgentPanel（会话面板）/ ApprovalCard（审批卡片）/ ConsultCard（咨询卡片）
│   │               / GateStatusBar（门禁状态条）/ PlanTodoList（待办计划）
│   └── aitools/    SubagentManager / SkillManager / McpManager
├── stores/         project / editor / reader / agent / settings（Pinia）
├── api/            http（axios 封装）/ index（81 端点）/ sse（EventSource 续传）
├── i18n/           中文文案集中管理
├── styles/         主题变量（主题色/字号/字体/页宽由 user_settings 驱动）
└── types/          DTO 类型定义
```

三栏布局：**左侧**动态组件 + 项目列表 + 设置入口；**中间**阅读模式 / 编辑模式；
**右侧** Agent 面板（会话流、审批与咨询卡片、门禁状态条、子代理泳道、上下文 Chips）。

### 3.3 数据架构

三层数据模型，职责严格分离：

```
① 文件（内容事实源）        data/workspaces/{project_id}/
                            ├── novel.yaml         项目元信息 + 项目级 llm_params + outline_file
                            ├── volumes.yaml       卷列表（id 短 uuid 恒定 / title / sort_order★派生）
                            ├── chapters/v{vol}/c{ch}.md
                            │                      frontmatter: title / sort_order★派生 / word_count
                            │                        / status(draft|finalized) / version(乐观锁) / updated_at
                            ├── outline/{小说名称}-大纲.md   单文件大纲（知识库之纲）
                            └── summaries/ch-{chapter_id}.md 章节摘要（仅 summarize_chapter 可写）

② SQLite（结构化登记）      data/app.db —— 16 张表
                            登记 / 修订快照 / 会话 / 消息树 / 运行 / 中断 / 审计 / 配置 / 派生索引
                            data/agent_state.db —— LangGraph checkpoint（跨库不建外键）

③ 派生索引（可从①重建）     story_fts（FTS5 虚拟表，二元切分 + unicode61）
                            project_index（章数 / 总字数 / last_updated / dirty 脏标记）
```

**关键设计**：正文与知识库以 Markdown 文件为唯一事实源；`novels` 表只做项目登记与外键锚点
（可从工作区扫描全量重建）；派生索引失败不阻断写入，改标 `dirty=1` 待重建。

16 张表按域拆分：

| 域 | 表 |
| --- | --- |
| `novel` | `novels` / `chapter_revisions` / `reading_progress` / `project_index` |
| `agent` | `agent_sessions` / `messages` / `tool_call_logs` / `agent_runs` / `agent_interrupts` / `subagent_configs` / `skill_configs` / `mcp_servers` |
| `llm` | `llm_providers` / `llm_models` |
| `config` | `user_settings` / `audit_logs` |

**全局 ORM 约定**：所有 `id` 为 TEXT(uuid4) 应用层生成（不用自增整数）；时间戳一律 UTC ISO-8601
文本应用层写入；JSON 列统一命名 `*_json` 存 TEXT；枚举用 TEXT + 应用层校验；
连接建立时 `PRAGMA foreign_keys=ON` + WAL；命名约定集中在 `Base.metadata.naming_convention`
（使 alembic autogenerate 稳定生成约束名）。

### 3.4 API 架构

统一前缀 `/api/v1`，**81 个端点**，分 14 组：

| 组 | 端点数 | 代表路径 |
| --- | --- | --- |
| `novels` | 14 | `GET/POST /novels`、`PATCH/DELETE /novels/{id}`、`/duplicate`、`/export`、`/index/rebuild`、`/reading-progress` |
| `agent` | 13 | `GET/POST /agent/sessions`、`/{sid}/messages`(202)、`/{sid}/branch`、`/{sid}/context`、`/runs/{id}`、`/runs/{id}/stream`(SSE)、`/runs/{id}/cancel`、`/interrupts/{id}/resolve` |
| `llm` | 13 | `/llm/providers` CRUD + `/test`、`/llm/models` CRUD + `/fallback-chain`、`/llm/overview`、`/config/reload` |
| `volumes` | 8 | `/novels/{id}/volumes`、`/volumes/{vid}/chapters`、`/chapters/{cid}` |
| `mcp` | 6 | `/mcp-servers` CRUD + `/probe` + `/confirm` |
| `chapters` | 5 | `GET/PUT /{cid}/content`（乐观锁）、`/{cid}/rewrite`、`/revisions`、`/restore` |
| `skills` | 5 | `/skills` CRUD + `/resync` |
| `subagents` | 5 | `/subagents` CRUD + `/reset`（恢复出厂） |
| `outline` | 3 | `GET/PUT /novels/{id}/outline`、`/outline/validate` |
| `widgets` | 3 | `/widgets/city`、`/pet-state`、`/weather` |
| `story` | 2 | `/novels/{id}/search`、`/summaries/rebuild` |
| `settings` | 2 | `GET/PUT /settings` |
| `audit` | 1 | `GET /audit-logs`（只读） |
| `health` | 1 | `GET /health` |

**通用约定**：

- 统一响应包装 `{code, message, data}`，HTTP 状态码与 `code` 对齐（成功 `code=0` + 2xx）；
- 错误码常量集中在 `core/errors.py`，配 `CODE_TO_HTTP` / `CODE_TO_MESSAGE` **双映射表**：

| code | HTTP | 含义 |
| --- | --- | --- |
| `0` | 2xx | 成功 |
| `40001` | 400 | 参数格式错误 |
| `40301` | 403 | 越权（WorkspaceGuard 拒绝 / 跨项目写入） |
| `40401` | 404 | 资源不存在 |
| `40901` | 409 | **版本冲突**（乐观锁失败，`data` 回传最新内容） |
| `40902` | 409 | 运行状态冲突（如对已结束 run 发 resolve） |
| `41001` | 410 | SSE 事件缓冲丢失（前端应重拉消息树） |
| `42201` | 422 | **显式命名校验失败** |
| `42202` | 422 | 大纲解析失败 |
| `50001` | 500 | 服务端内部错误 |
| `50201` | 502 | LLM Provider 调用失败（含降级链耗尽） |
| `50301` | 503 | 依赖不可用（calibre 缺失 / MCP 探测失败 / FTS5 不可用） |

- SSE 信封 `{event, run_id, session_id, seq, ts, data}`，`seq` 每 run 内单调递增。

### 3.5 前端页面与应用功能设计

本节从**页面设计**与**应用功能**视角描述前端：页面导航、每个页面/区块的布局与交互设计、
关键用户流程，以及支撑这些设计的前端状态与通信约定。代码层面的组件实现要点见 [4.7 前端模块要点](#47-前端模块要点)。

#### 3.5.1 页面导航结构

SPA（Vue Router history 模式，单体部署由后端 SPA 回落承接），共 3 个页面：

| 路由 | 页面 | 定位 |
| --- | --- | --- |
| `/` | **工作台**（WorkspaceView） | 主界面，写作全流程都在这里完成（三栏布局） |
| `/settings` | **通用设置**（SettingsView） | 外观 / 编辑 / 动态组件偏好 + 运行状态自检 |
| `/ai-tools` | **AI 工具**（AiToolsView） | 大模型 Provider·Model / 子代理 / 技能 / MCP 四类 AI 配置 |

入口关系：工作台左栏底部「设置」「AI 工具」按钮 → 对应页面；两个配置页均有「‹ 返回」回工作台。
未匹配路由一律重定向 `/`。

#### 3.5.2 工作台（三栏主界面）

三栏从左到右：

- **左栏 sidebar**：WeatherWidget（宠物 + 城市 + 天气）→ ProjectList（项目列表，右键菜单）→ 底部「设置」「AI 工具」入口；
- **中栏 main-panel**：顶部 ReaderToolbar（目录开关 / 阅读·编辑切换 / 项目名 / 自动保存状态 / Agent 面板开关）；
  下方为 TocDrawer（目录抽屉：≡大纲 + 卷章树）+ 内容区（阅读模式：ChapterReader / OutlineReader；
  编辑模式：【正文】【卷章】两页签，大纲经目录「大纲」节点进入）；
- **右栏 agent-panel（可收起）**：AgentPanel（会话管理 / 运行状态条 / 消息流 / 计划待办 / 门禁状态条 /
  审批与咨询卡片 / token 用量 / 上下文 Chips / 「生成本章」与发送）。

**左栏 —— 项目与陪伴**

| 区块 | 功能设计 |
| --- | --- |
| WeatherWidget | 宠物**两态** SVG 动画（项目近期有写作进展→敲键盘；静默超过 `pet_idle_minutes`→打瞌睡，1 分钟轮询）+ 城市与当前天气（浏览器定位，拒绝/失败回落设置城市；5 分钟刷新；坐标不入库） |
| ProjectList | 项目列表（标题 + 字数，万字缩写「x.x 万字」）；顶部「＋新建小说」对话框（书名必填 / 作者 / 题材 / 简介，**显式命名**校验）；每项右键菜单：重命名 / 导出 / 复制（预填「{名}（副本）」）/ 删除（软删除，二次确认）；选中项驱动全局（中栏内容 + 右栏会话按项目切换） |
| 底部入口 | 「设置」「AI 工具」两个跳转按钮 |

**中栏 —— 阅读 / 编辑双模式**

顶部 ReaderToolbar：目录抽屉开关、**阅读/编辑**模式切换（radio）、当前项目名、后端异常提示
（未连通 / FTS5 降级）、编辑模式下的自动保存指示器、右栏 Agent 面板开关。

阅读模式：

| 区块 | 功能设计 |
| --- | --- |
| TocDrawer | 顶端固定「≡ 大纲」节点（虚线分隔），其下按 `sort_order` **只读**渲染卷（标题+章数）→ 章（定稿章带「定稿」绿标）；点击章=阅读该章，点击大纲=看大纲；★无拖拽/无重排按钮（顺序由大纲派生） |
| ChapterReader | Markdown 渲染（markdown-it + DOMPurify 消毒）；上一章/下一章翻页（按展平序列）；**进度记忆**（滚动比例入库，回到同章自动定位）；夜间模式独立于全局暗色主题 |
| OutlineReader | 大纲只读渲染（未选章或点大纲节点时的默认视图） |

编辑模式（两页签；大纲经目录「大纲」节点进入，不单设页签）：

| 区块 | 功能设计 |
| --- | --- |
| 【正文】页签 | ConflictBanner（版本冲突横幅，见 3.5.4）+ TiptapEditor（富文本：Markdown 快捷输入、工具栏、**选区 AI 改写**四按钮）+ WordCountBar（实时字数）；自动保存按 `autosave_interval_sec`（自动保存不产生修订快照）；关闭页面有未保存修改时浏览器拦截提醒 |
| 【卷章】页签 | VolumeChapterTree：卷/章增删改（新建一律追加末尾）；删除卷级联删章，确认框列出将删除的章名与字数；★**只读顺序**——顶部提示「顺序由大纲决定」+「去大纲调整」按钮直达大纲编辑 |
| 大纲节点 | OutlineEditor：Markdown **源码**编辑（保证 `# 卷 / ## 章` 层级不被富文本破坏）+ 编辑/预览切换；保存后显示校验结果（错误/警告分行号列出，不阻断保存）与顺序回填统计（「已同步 x 卷 / y 章顺序」） |

**右栏 —— Agent 面板（AgentPanel）**

| 区块（自上而下） | 功能设计 |
| --- | --- |
| 面板头 | 会话下拉（按项目隔离，非 idle 会话带状态后缀）+「＋」新建 +「⋯」菜单（重命名 / **导出会话为 Markdown**（浏览器端拼 Blob 下载，含工具调用摘要）/ 删除） |
| 引导条 | 未配置任何模型时显示警告条，指引去「AI 工具」页添加 Provider |
| 运行状态条 | 运行中脉冲圆点 + 活跃子代理泳道 tag + 门禁阶段 tag +「停止」按钮（协作式取消） |
| 消息流 | 用户/AI/工具三类气泡；AI 消息流式渲染（打字光标）；工具调用折叠卡片（名称+截断参数）；消息级操作：复制 / 重新生成（产生**兄弟分支**，可切换对比）；流式中断线重连由 SSE 层透明处理，缓冲丢失时回落重拉全量消息 |
| PlanTodoList | Agent 待办计划（`write_todos` 驱动，勾选态实时更新） |
| GateStatusBar | 门禁 G0→G3 阶段流转 + 重试计数 + 各阶段判定摘要（订阅 `gate.update`） |
| ApprovalCard | 高危操作审批：工具名+参数+风险说明，**四决策**（批准 / 编辑后批准（参数可改）/ 拒绝（可附说明）/ 回复补充） |
| ConsultCard | 门禁咨询：问题 + 选项按钮 + 自由输入框（`allow_text` 时） |
| 用量行 | 最近一次运行的 token 输入/输出 |
| 输入区 | **上下文 Chips**：「＋上下文」popover 三类可选（子代理/技能/已读章节，含「＋当前章」快捷），增删即 `PATCH /context` 持久化、会话切换回填；模型下拉（单条消息级覆盖，不回写会话）；**「生成本章」**按钮（选中章时出现，确认框说明四阶段门禁与快照可回滚）；发送框（Enter 发送） |

#### 3.5.3 通用设置页（SettingsView）

单列表单 + 右上「保存」，全部持久化到 `user_settings` 单行表，保存即全局生效（CSS 变量驱动主题）：

| 分组 | 设置项 | 设计要点 |
| --- | --- | --- |
| 外观 | 主题（浅色/深色/跟随系统）、主题色（取色器+预览文字）、字体大小（12–32 滑杆）、字体风格（宋/黑/楷/系统）、阅读页宽（24–70 滑杆） | 即时预览，保存后写入 CSS 变量 |
| 编辑 | 自动保存间隔（5–600 秒） | ★有意**不设「每日目标字数」** |
| 动态组件 | 回落城市（定位被拒时用）、宠物发呆阈值（1–1440 分钟） | 与 WeatherWidget 联动 |
| AI 工具 | 提示条 +「前往 AI 工具」按钮 | 配置项已迁出本页，只留导航 |
| 运行状态 | 后端版本 / SQLite / FTS5（可用性+降级原因）/ calibre（mobi 导出）/ Langfuse / 数据目录 | 只读自检面板（`GET /health`） |

#### 3.5.4 AI 工具页（AiToolsView）

四页签：**大模型 API / 子代理 / 技能 / MCP**；页头「重载配置」按钮（使后端模型工厂缓存失效）。

| 页签 | 功能设计 |
| --- | --- |
| 大模型 API | 顶部概览（当前默认模型）；**Provider 卡片**列表：名称+类型 tag、密钥状态 tag（已配置显示掩码 `sk-****abcd` / 未配置红色警示）、测试连接、编辑、删除（提示将级联删除 N 个模型）；卡片内**模型行**：model_id、默认标、启停、上下文长度、降级链长度，可编辑/删除/追加。Provider 对话框：6 种类型下拉、base_url、密钥环境变量（可创建，给出建议 env 列表）、**密钥输入只在编辑态**（保存写入 `.env` 后仅回显掩码，留空=不修改）、优先级、启停。Model 对话框：model_id、显示名、max_context、supports_tools、is_default、降级链（每行一个 model_ref，附全部可用 ref 提示）、默认参数 JSON |
| 子代理 | 内置 5 个 + 用户自建。**内置不可删除/改名**，但可停用、改提示词、改模型覆盖、「恢复出厂」；自建支持完整 CRUD。编辑对话框：名称/描述/系统提示词/模型覆盖（下拉全部模型，默认模型带 ★）/模式（isolated/fork，fork 不带技能）/可用技能（按行）/启停 |
| 技能 | 内置 8 项**只读**（可预览全文 SKILL.md）+ 用户自建 CRUD（name==目录名、小写连字符 ≤64，description ≤1024）；编辑对话框：name/description/正文（frontmatter 自动生成）/allowed_tools/启停；「重新同步」按钮对齐 DB 与包内文件 |
| MCP | 内置 Open-Meteo 天气（只读展示，免配置）；第三方添加：stdio（命令**白名单** npx/uvx/python，保存前弹窗展示完整命令**二次显式确认**）/ http / sse（URL 必填）；每服务可**探测**（列出 tools）、启停、删除 |

#### 3.5.5 关键功能流程设计

**① 正文保存与版本冲突（乐观锁 UX）**

```
打开章 → 记录 version 为 base_version → 编辑（dirty）→ 保存（手动/自动）携 base_version
  ├─ 200 → 更新本地 version，冲突清除
  └─ 409/40901 → ConflictBanner 弹出：展示服务端最新内容/版本/更新时间/最后写入方(manual|agent)
       ├─ 「查看差异合并」→ 对比后手工整合再保存
       └─ 「以我的为准」→ 携服务端最新 version 强制覆盖
```

**② 选区 AI 改写**：选中文字 → 四按钮（改写/扩写/缩写/润色）启用 → 调后端取结果（**不落盘**）
→ RewriteDiffDialog 句级 LCS 三色 diff 预览 → 「接受」替换选区并打 **AI 来源标记**
（Mark 扩展，点击标记可回滚为原文）/「放弃」丢弃结果。

**③ AI 改动章节的热更新**：Agent 写文件 → SSE `file.changed` → 工作台刷新卷章树；
若改的正是打开中的章：本地无未保存修改 → 静默重载；有 → 确认框「载入 AI 版本 / 保留本地修改」
（保留本地则后续保存自然进入 ① 的 409 冲突流程，不丢稿）。

**④ 门禁生成（生成本章）**：目录选中章 → 右栏「生成本章」→ 确认框（说明四阶段与快照可回滚）
→ 门禁状态条实时显示 G0→G3 流转；G0/G1/G3 不过 → ConsultCard 让用户拍板
（代写大纲 / 去补简介 / 强制跳过 / 认可现状…）；G2 期间高危写入 → ApprovalCard。

**⑤ 对话运行与分支**：发消息 → 202+run_id → SSE 流式渲染（token/工具/泳道/计划/用量）→
run.end 后重拉落库消息合并；「重新生成」/「编辑重发」= 以同一条消息为父建**兄弟分支**，
消息树 UI 可切换查看各分支；运行中可「停止」（协作式取消）。

**⑥ 导出成书**：项目右键「导出」→ ExportDialog 选格式（markdown/epub/mobi/txt，mobi 在
calibre 未装时置灰+提示）→ 启动任务显示进度条 → 完成后「下载」zip（含大纲原件）。

#### 3.5.6 前端状态与通信设计

**Pinia 五 store 分工**（跨组件共享的最小状态集）：

| store | 职责 |
| --- | --- |
| `project` | 项目列表 / 当前项目 / 卷章树（`volumeTree` 按 sort_order 只读排序）/ 展平章序列 `flatChapters`（翻页用）/ 卷 CRUD |
| `editor` | 打开章的 draft + base_version（乐观锁）/ dirty / 自动保存 / 409 冲突态 `conflict` / 修订快照列表与回滚 / 定稿状态 |
| `reader` | 模式（read/edit）/ 编辑页签 / 目录抽屉开关 / 当前章或大纲节点 / 夜间模式 / 滚动比例 |
| `agent` | 会话列表与当前会话 / 已落库消息 + 流式增量合并 / SSE 客户端生命周期 / 运行·取消·重新生成·门禁生成 / 待审批 interrupt 与 consult / todos / 门禁状态 / fileChanged 事件暴露给工作台 / 最近用量 |
| `settings` | user_settings 加载与保存 / health 自检 / calibre 可用性 |

**通信约定**：

- `api/http.ts`（axios）：统一拆 `{code,message,data}` 信封；按业务码分发 UI 行为
  （40901→ConflictBanner、40902→「运行已结束」提示、42201→命名错误、50301→依赖不可用警告）；
- `api/sse.ts`（EventSource）：断线自动重连带 `Last-Event-ID`；手动重连走 `?last_event_id=`；
  收到 `error(41001)`（缓冲丢失）回落重拉消息树；
- `api/index.ts`：81 个端点的类型化封装（与 `types/index.ts` DTO 一一对应）；
- `i18n/index.ts`：全部中文文案集中管理（组件内不写死文案，`t(key)` 取用）；
- `styles/main.css` + CSS 变量：主题色/字号/字体/页宽由 settings 写入 `:root` 变量即时生效。

---

## 四、模块实现逻辑

### 4.1 `core/` —— 基础设施

| 模块 | 实现要点 |
| --- | --- |
| `config.py` | pydantic-settings 读 `.env`（`extra="ignore"`）；`get_settings()` 进程内 `lru_cache` 单例；`data_root` / `db_path` / `agent_state_path` 派生属性；`langfuse_enabled` = 两 Key 均非空。**配置版本是进程级单调计数器** `current_config_version()`（`/config/reload` 时 +1，使 Agent 工厂缓存失效；★不随 Settings 重载归零） |
| `errors.py` | `ErrorCode` 常量 + `CODE_TO_HTTP` / `CODE_TO_MESSAGE` 双映射；`ApiError` 基类携 `code/message/data`，`http_status` 属性查表；8 个子类（`BadRequestError` / `ForbiddenError` / `NotFoundError` / `VersionConflictError` / `NamingInvalidError` / `OutlineParseError` / `DependencyUnavailableError` / `LlmProviderError`） |
| `response.py` | `ok(data)` → `{code:0, message:"success", data}` |
| `logging.py` | structlog JSON 输出；**processor 对键名匹配 `key\|token\|secret` 的字段自动掩码**；按日期轮转写 `data/logs/{app,error,audit}-{date}.jsonl` |
| `paths.py` | 路径解析集中地：`data_root()` / `workspace_dir()` / `chapters_dir()` / `volume_dir()` / `chapter_file()` / `outline_dir()` / `summaries_dir()` / `skills_root()` / `session_skills_dir()` / `exports_root()` / `trash_root()`；`safe_filename()`（替换 Windows 非法字符 `\/:*?"<>\|` + 压缩空白）→ `outline_filename()` = `{名}-大纲.md`；`write_text()` / `read_text()` 统一 utf-8 写入口。另含 `BUILTIN_SKILLS_DIR` 路径常量（指向 `agent/skills/`，下沉此处以消除 services→agent 反向依赖） |
| `security.py` | `get_api_key(env_name)`（读取 + 缺失检测）、`mask(key)` → `sk-****abcd`、`set_env_key(env, value)`：UI 保存密钥时写回 `.env`，**只允许白名单键**（`*_API_KEY`、`LANGFUSE_*`），流程为「读全文 → 替换/追加目标键 → 临时文件 + `os.replace` 原子写 → 写前备份 `.env.bak`」+ 进程内互斥锁 |
| `deps.py` | FastAPI 依赖注入：`get_session()` 提供 AsyncSession |
| `catalog.py` | Agent 内置目录数据的**惰性访问器**：`builtin_subagents()` / `builtin_subagent_names()`。core 在模块加载期不触碰 agent，只在运行期解析 → import 图保持无环 |

### 4.2 `db/` —— 数据访问

| 模块 | 实现要点 |
| --- | --- |
| `base.py` | `Base(DeclarativeBase)` + `NAMING_CONVENTION`（ix/uq/ck/fk/pk）；`new_id()` uuid4；`short_id(existing)` 卷章短 ID（uuid 前 8 位，冲突重取，**一旦生成永不改变**）；`utcnow()` → `YYYY-MM-DDTHH:MM:SS.sssZ`；`parse_ts()` 容错解析；`TimestampMixin`（created_at / updated_at + onupdate）；`enable_sqlite_pragmas()` 每连接开 `foreign_keys=ON` + WAL + `synchronous=NORMAL` |
| `session.py` | 进程内单例 async engine（`sqlite+aiosqlite:///`，Windows 路径转正斜杠）；`session_scope()` 异步上下文管理器；`reset_engine()` 供测试释放 |
| `bootstrap.py` | `init_database()` 幂等三步：`create_all` 建表 → **打 alembic 版本戳** → FTS5 探测建 `story_fts`。★建表后必须 stamp：否则后续 `writer migrate` 会因「表已存在」失败；stamp 后两条初始化路径（应用启动 / 手工 migrate）互相幂等 |
| `fts.py` | `CREATE_FTS_SQL`：`story_fts(doc_ref, project_id, kind, title, content)` `tokenize='unicode61'`，★`project_id` 必须入表且 UNINDEXED（查询时附加过滤，否则跨项目串数据）；`bigram_tokenize()` 中文二元切分（`少年走出山村` → `少年 年走 走出 出山 山村`），非 CJK 片段原样保留；`probe_fts5()` **两步探测**：先 `SELECT fts5(NULL)`（未编译报 `no such function: fts5`），再建临时表验证 `unicode61` 与 `bm25()`；结果进程内缓存，任一步失败即永久降级 `LIKE` |
| `models/` | 按域拆 `novel.py` / `agent.py` / `llm.py` / `config.py`，`__init__.py` 只做 re-export + `ALL_MODELS` 汇总（供 alembic env.py 与 `create_all` 发现全部表） |
| `migrations/` | alembic：`env.py` 导入 `ALL_MODELS` 注册表；`versions/` 两个迁移（M1 初始 schema、M3 agent schema） |

### 4.3 `schemas/` —— DTO

按域拆 `novel.py`（项目/卷章/大纲 21 个）/ `agent.py`（会话/消息/中断/SSE 7 个）/
`llm.py`（Provider/Model 5 个）/ `config.py`（设置/导出/检索/健康 6 个），共 39 个 model。
`__init__.py` re-export 全部，故 `from app.schemas import X` 的既有写法不受影响。

关键约束编码在 DTO 上：`ContentIn.base_version` 是 `Field(ge=1)` **必填**（乐观锁）；
`RewriteIn.mode` 是 `Literal["rewrite","expand","condense","polish"]`、`selected_text` 限
`min_length=1, max_length=8000`；`LlmProviderCreate.type` 是 `Literal[_PROVIDER_TYPES]`
（6 种厂商类型）；★**没有任何 reorder / move 相关 DTO**（顺序是大纲派生值）。

### 4.4 `services/` —— 业务编排（16 模块）

| 模块 | 实现要点 |
| --- | --- |
| `novels.py` | 项目 CRUD。**创建必须显式命名**（`validate_novel_title`，违规 42201）+ 初始化工作区 + 写入大纲模板文件；软删除移工作区到 `trash/{pid}-{ts}/`；重命名是**事务**：同步重命名 outline 文件 + 更新 `novel.yaml.title/outline_file` + `novels.title/outline_file`（DB 值是文件值的缓存），任一步失败整体回滚；`refresh_project_index()` 刷新派生统计 |
| `volumes.py` | 卷章 CRUD + 正文读写（乐观锁）+ 修订快照与回滚。★**无任何 reorder / move 函数**，并有 `assert_no_manual_reorder()` 设计守卫（测试断言 `forbidden` 集合与 `globals()` 无交集）。写入流程：比对 `base_version` 与文件 frontmatter `version` → 不符抛 `VersionConflictError`（409/40901，`data` 含最新 content/version/updated_at/last_writer）→ 符合则按策略快照（手动保存/Agent 写前/章节定稿快照；**自动保存不快照**，防版本爆炸）→ `version += 1` → `_prune_revisions` 保留最近 `chapter_revision_keep`(20) 条 |
| `story.py` | 大纲读写 + `sync_order_from_outline`（★卷章 `sort_order` **唯一写入口**）+ 章节定稿。顺序回填规则：按卷节 H1 先后写 `volumes.yaml.sort_order`(1..N)，按各卷下章简介 H2 先后写章节 frontmatter `sort_order`（卷内 1..N）；匹配以归一化后「卷名+章名完全一致」为准；大纲未出现的卷/章保持原值并追加末尾（validate 给警告）；★回填**不产生修订快照、不递增 version**（顺序是元数据而非正文变更，避免与乐观锁/自动保存互相干扰） |
| `outline.py` | `parse_outline()` **严格层级解析**：H1 用 `^#(?!#)\s+(.*)$`（`#` 后不接 `#`），H2 用 `^##(?!#)\s+(.*)$`，H3+ 忽略。★宽松正则会把 `## 第三章 卷土重来` 误判为卷节，故必须严格。H1 中匹配 `^附录` 的为附录节（不参与章匹配），其余为卷节；章简介小节按行解析 `- 简介：`（必需）与 `- 衔接要点：`（可选）；`normalize_title()` 归一化：去首尾空白 → 全角空格转半角 → 压缩连续空白 → 去序数词前缀（`第X卷/章/回/节`，X 支持阿拉伯与中文数字）→ 去标点（`·、,，.。:：-—`）；`render_template()` 生成创建时的大纲模板 |
| `search.py` | FTS5 检索：`bigram_tokenize` 处理查询词（★查询侧必须用与索引侧同一函数，否则永远查不到）+ `MATCH` + `bm25()` 排序 + `project_id` 过滤；异常/不可用降级 `LIKE '%q%'`（返回结构不变，`data.degraded=true`）；`reindex_project()` 全量重建（数据源 = 大纲单文件 + `summaries/`）；`rebuild_summaries()` 先补 LLM 摘要再重建索引 |
| `exporters.py` | 4 种格式导出为 zip：markdown（分卷分章）/ txt（自研拼接，不依赖外部工具）/ epub（自研生成器：`mimetype` + `META-INF/container.xml` + `content.opf` + `nav.xhtml` + 各章 xhtml，markdown 段落转 xhtml）/ mobi（EPUB 产物经 **calibre `ebook-convert`** 转换，`find_calibre()` 探测 `shutil.which` + Windows 常见安装路径，带超时；未装则 `50301` 置灰）。异步任务模型：`create_task` → 后台线程 `run_export` → 轮询 `GET /export-tasks/{id}` → `download`。zip 内附 `manifest.json` + 大纲原件 |
| `files.py` | 工作区文件读写（`novel.yaml` / `volumes.yaml` / 章节 frontmatter+正文，用 `python-frontmatter`）；`list_chapter_files()` / `read_chapter()` / `find_volume()` |
| `workspace.py` | ★`WorkspaceGuard`：**服务层唯一文件写入口**。6 条规则：① 入参接受工作区相对路径，内部换算宿主路径；② `resolve()` 归一化后必须严格落在白名单根内（`workspaces`/`skills`/`exports`/`trash`/`logs`），否则 `ForbiddenError`(403/40301)；③ 拒绝含 `..` 的路径、**逐段检查**符号链接解析后是否越界；④ 跨项目写一律拒绝；⑤ `summaries/` 只允许 `summarize_chapter` 写（`allow_summaries=True` 显式放行）；⑥ 写入成功后同步更新派生索引 |
| `naming.py` | `validate_novel_title()` / `validate_volume_title()` / `validate_chapter_title()`：拒绝空名、默认名（「未命名小说」「第 1 卷」）、纯序号名 → 42201 |
| `audit.py` | `record(session, action, entity_type, entity_id, detail)`：写 `audit_logs` 表 + JSONL 备份（双写） |
| `llm.py` | Provider/Model CRUD + 连接测试 + 降级链解析 + `/config/reload`（重读 `.env` + `bump_config_version` + `invalidate_model_cache` + `reset_client`）。密钥经 `security.set_env_key` 写 `.env`，DB 只存 `api_key_env`；响应回 `api_key_masked` |
| `agent.py` | Agent 业务规则：会话 CRUD（严格按 `project_id` 隔离，默认标题按现有会话数派生）、会话级上下文（model_ref / subagent_ids / skill_ids / context_chapter_ids，变更落审计）、`load_message_tree` / `switch_branch`（经 `agent.memory`）、`run_dto`、`resolve_interrupt`（审批与咨询**统一入口**）、数据访问（`list_sessions` / `list_tool_calls` / `require_session` / `project_word_count`）。★运行编排（astream/SSE）不在这里，在 `agent/loop/runner.py` |
| `skills.py` | `skill_configs` CRUD + **物化**到 `data/skills/{name}/SKILL.md`（frontmatter `name` 必须等于目录名、`description` ≤1024、SKILL.md ≤10MB）；改名迁移目录；停用移除物化；`/skills/resync` 一致性修复；与内置技能重名拒绝；`materialize_session_skills()` 把会话选中的技能物化到 `data/session-skills/{sid}/`（内置从包内复制，用户技能从 `data/skills/` 或 DB `content_md` 复制） |
| `subagents.py` | `subagent_configs` CRUD + `seed_builtins()`（幂等：按 name 跳过，**不覆盖用户改动**）+ `reset_builtin()`（恢复出厂：提示词/描述/mode/skills/model_ref）。校验：`name` 小写字母开头 + 仅小写字母数字连字符 + ≤64；`mode=fork` 时 `skills_json` 必须为空（deepagents 实测会抛错）；内置不可删除（可停用） |
| `mcp.py` | `mcp_servers` CRUD + `probe`（探测工具列表）+ `confirm`（stdio 二次确认，写 `confirmed_at`）。约束：仅开源免费无认证（无认证头字段）；stdio 命令走 `validate_stdio_command` 白名单；**命令全文落审计** |
| `weather.py` | Open-Meteo 封装（Geocoding 城市 → 坐标 → 当前天气/预报），同时服务动态组件 API 与内置天气 MCP，避免两份实现漂移 |

### 4.5 `api/` —— 路由层

14 个模块与 services **同名配对**（agent / audit / llm / mcp / novels / outline / skills / story /
subagents / volumes + chapters / settings / widgets / health），分层命名一致、无缩写。

`api/agent.py` 是唯一持有 Agent 运行入口的模块（`run_manager` + `sse_bus`），其 SSE 端点实现：
读 `Last-Event-ID` 请求头（EventSource 自动重连）或 `?last_event_id=`（手动重连，EventSource
无法自定义 header）取较大值 → `sse_bus.subscribe()` 拿 `(queue, backlog, lost, finished)` →
`lost` 则发 `buffer_lost_event`(41001) 并关流 → 先补发 backlog（遇 `run.end`/`error` 即返回）→
`finished` 则直接关流 → 否则循环 `wait_for(queue.get(), timeout=15)`，超时发心跳注释行
（`{"comment":"ping"}`，不占 seq）→ `finally` 里 `unsubscribe`。

`POST /sessions/{sid}/messages` 返回 **202 + `{run_id}`**（事件走独立 SSE 端点）；
若该会话已有活跃 run（`run_manager.running_ids()` + `session_of()` 匹配）→ 409/40902。

### 4.6 `agent/` —— AI Agent（12 子包）

这是本项目的技术核心。子包划分与「Agent 能力维度」1:1 对应。

#### `llm/` —— LLM 接入

| 模块 | 实现 |
| --- | --- |
| `factory.py` | `resolve_model_ref()` 解析 `"{provider_id}:{model_id}"` → (provider 行, model 行)；`build_chat_model()` 按 `provider.type` 装配 6 种 ChatModel（`openai_compatible` → `ChatOpenAI(base_url=...)`，`openai` → `ChatOpenAI`，`anthropic` → `ChatAnthropic`，`google` → `ChatGoogleGenerativeAI`，`azure_openai` → `AzureChatOpenAI`，`openrouter` → `ChatOpenAI(base_url=openrouter)`）；`_KNOWN_BASE_URLS` 为 6 家常见厂商提供 `base_url` 兜底；密钥经 `security.get_api_key(provider.api_key_env)` 读取（未配置 → `LlmProviderError` 50201）；**LRU 缓存**按 `(model_ref, config_version)` 为键，上限 32 条，`invalidate_model_cache()` 清空；`get_default_chat_model()` 取 `is_default` 模型；`get_fallback_chain()` 解析有序降级链；`provider_key_status()` 报告密钥配置状态；`register_model_override()` / `clear_model_overrides()` 供测试注入假模型 |
| `params.py` | **四层参数覆盖**（后者覆盖前者，未设置项逐层回落）：① 全局默认（`.env` + `llm_models.default_params_json`）② 小说项目级（`novel.yaml.llm_params`）③ 会话级（`agent_sessions.model_ref` 等）④ 单条消息级（发消息 body 的 `model_ref`，仅本次 run 生效、不回写会话）。支持 `temperature / max_tokens / top_p / timeout / max_retries / stream / extra_body` |
| `budget.py` | token 预算：预算 = `max_context × 阈值(默认 0.5)`；`budget_report(current_tokens)` 产出 `{used, budget, ratio, need_compact}`，供 `RecapInjectionMiddleware` 与前端 `context.compact` 事件使用；计数默认 `count_tokens_approximately`，可切 `tiktoken` |
| `rewrite.py` | 选区 AI 改写（**服务层直连 ChatModel，不进 Agent 循环**——单次转换无需工具与多轮，同门禁 G1/G3）。四模式各有提示词要求；选区 ≤8000 字符；上下文前后各截 600 字；SystemMessage 强制「只输出处理后正文，绝不输出解释/标题/引号包裹」；`_strip_wrappers()` 剥离模型可能包裹的代码块/前缀/成对引号（★中文引号是**不对称对** `“ ”`/`「 」`/`『 』`/`‘ ’`，不能用首尾相等判断）；返回原文 + 改写文 + 字数对比，**不落盘**（前端 diff 预览后由用户「接受」再走 `PUT content`） |

#### `loop/` —— Agent-Loop（10 模块 + middleware 子包）

| 模块 | 实现 |
| --- | --- |
| `builder.py` | `build_agent()` 装配 `create_deep_agent`：backend（`build_backend(session_id)`）+ permissions（`build_permissions(project_id)` 8 条）+ checkpointer（`AsyncSqliteSaver`）+ tools（`build_tools()`）+ subagents（`load_subagent_specs` 读 DB，DB 空则回落内置定义；`resolved_models` 预解析 ChatModel 实例）+ skills（`SKILL_PATHS = ["/skills/builtin/", "/skills/user/"]` + 会话精选 `/skills/session-{sid}/`）+ middleware 栈 + `context_schema=AgentContext` + `interrupt_on={"delete": True}` + system prompt（`render("main_agent.md.j2")`）。`build_middleware_stack()` **9 层固定顺序**（不可调换）：`ModelRetry(max_retries=2, backoff=2.0, jitter=True)` → `ModelFallback(primary, *fallbacks)` → `ModelCallLimit(run_limit=40, exit_behavior="end")` → `TodoList` → `Summarization` → `RecapInjection` → `WorkspaceGuard` → `FileChangeNotify` → `WriteApproval`。每个内置中间件都包 try/except 做版本兼容兜底。`DEFAULT_MODEL_CALL_LIMIT=40`，硬上限另经 `config={"recursion_limit": 60}` |
| `runner.py` | **run 生命周期管理**（单一职责，375 行）。`RunManager` 进程内单例，持 `_tasks` / `_cancel_flags` / `_locks` / `_sessions` 四个字典。`start()`：用户消息入消息树（`parent_id` 为空则接 `active_leaf`，传 `parent_id` 则建兄弟分支）→ 建 `agent_runs(status=pending)` → 会话置 running → `sse_bus.open()` → `_spawn()` → 返回 run_id。`resume()`：校验 run 非终态 → `sse_bus.reopen()`（finished=False，seq 续增，缓冲保留）→ `_spawn(resume_value=...)`。`cancel()`：置取消标志 → 给 3 秒优雅窗口（`wait_for(shield(task))`）→ 超时硬 `task.cancel()` → run 置 cancelled + 会话回 idle → 发 `run.end` + `close()`。`_spawn()` 用 **per-run `asyncio.Lock` 串行化**（防 resolve 与进行中的 run 并发写同一 thread），`add_done_callback` 清理四个字典。`_execute_run()` 检测 `target_chapter_id` 非空则分流到 `gates/runner.py` |
| `streaming.py` | **LangGraph → SSE 事件映射**（335 行）。`stream_graph()` 驱动 `graph.astream(input, config, stream_mode=["updates","messages"], subgraphs=True, context=ctx)`，逐 chunk 拆 `(namespace, mode, payload)`：`messages` 模式 → `_handle_message_chunk()` 发 `message.delta`（顶层）/ `subagent.delta`（有 namespace）+ `usage`；`updates` 模式 → `_handle_update()` 检测 `__interrupt__` → `_persist_and_emit_interrupt()`，`model` 节点完成 → `_emit_message_complete()` 发 `message.complete` / `subagent.end`，`TodoListMiddleware`/`write_todos` → `_extract_todos()` 发 `plan.update`；每次 updates 后 `syncer.sync()` 增量落库。**子代理泳道**：新 namespace 首次出现发 `subagent.start`，顶层 `tools` 节点完成时对所有活跃子代理发 `subagent.end`。`_persist_and_emit_interrupt()` 区分三类中断：`kind=="gate_consult"` → type=consult + `consult.request`；`kind==INTERRUPT_KIND` → type=approval + `interrupt.request`；否则是 langchain HITL **batched** 形状（`{action_requests, review_configs}`）→ 从 `review_configs[].allowed_decisions` 取决策集。落 `agent_interrupts` + run/会话置 `awaiting_*` |
| `message_sync.py` | **消息树增量同步**（158 行）。`MessageSyncer` ★按**索引基线**同步（不按消息 id 匹配）：graph state 里的消息 id 由 LangGraph 生成，与业务库 uuid 无对应关系；而 state 的 messages 是**追加式**的，故只记录「已同步到的下标」，每次 sync 只处理新增部分。基线确定：首轮用户消息已由 API 层落库、state 里它将成为 `messages[0]` → 基线 = 1、parent = 该消息 id；恢复时中断前消息已全部落库 → 基线 = 当前 state 长度、parent = 会话 `active_leaf_id`。`skip_human_once` 标记让门禁 G2 的内部生成指令**不入消息树**（只推进指针）。未完成的流式消息（`status=="streaming"` 或无内容且无 tool_calls）不落库，留待下次 sync（`force=True` 时强制落） |
| `resolve.py` | 运行前解析（145 行）：`resolve_models()` 主模型 + 降级链（`chain[1:4]` 最多 3 级，单个失败只 warning 跳过）；`max_context_of()` 依次探 `max_context`/`max_tokens`/`context_window`，兜底 32768；`attach_langfuse()` 挂 langchain CallbackHandler（未配 Key 返回 None 自动跳过）；`make_recap_provider()` 返回 `async (context) -> str`，组合 `build_recap` + 会话「已读章节」Chips（`_pinned_chapters_of`，注入失败只 warning 不阻断运行）；`session_context_of()` 把会话存的 id 解析为 name（子代理 id → `subagent_configs.name`；技能 id 可为 DB uuid 或 `builtin:{name}` 前缀） |
| `lifecycle.py` | run 生命周期公共件（146 行，普通运行与门禁运行共用，故独立成模块以免互相依赖）：`mark_running()`（仅 pending/cancelling → running）；`graph_config()` 生成 `{configurable:{thread_id}, recursion_limit}`；`error_of()` ApiError 保留业务错误码、其余归 50001；`publish_run_end()` 暂停态发 `{status, paused:true}`、终态发 `{status, usage, error}`；`finalize()` **收尾统一执行**（★不在中间件里做——langchain 无 `on_run_end` 钩子）：写 `agent_runs` 汇总（status/tokens/recursion/fallback/error/ended_at/langfuse_trace_id）+ 会话状态（暂停态不改）+ 刷新 `project_index` + 写 Langfuse score（`run_tokens` / `project_words` / `fallback_used`），每步独立 try/except 不互相阻断；`run_state_conflict()` 生成 40902 |
| `events.py` | **SSE 事件总线**（241 行）。`SseEvent` dataclass（`slots=True`）+ `to_json()`；`RunStream` 每 run 一个：`deque(maxlen=sse_buffer_size=500)` 环形缓冲 + `seq` 单调递增 + 订阅者 queue 集合 + `finished` 标记；`SseBus` 进程内单例管 `open` / `reopen` / `publish` / `subscribe(run_id, last_event_id)` / `unsubscribe` / `close`；`subscribe` 返回 `(queue, backlog, lost, finished)`——`lost` 判定为请求的 `last_event_id` 已被缓冲淘汰；`schedule()` 用 `ensure_future` 供同步上下文发事件；`buffer_lost_event()` 生成 41001 |
| `checkpointer.py` | `AsyncSqliteSaver` 落 `data/agent_state.db`（与业务库分离，跨库不建外键），`thread_id = novel:{project_id}:session:{session_id}`。★**进程内单例**：aiosqlite 连接须长生命周期持有（每个 run 重开会丢中断态）；`asyncio.Lock` + **双检**（可能在等锁期间已被其它协程重建）；`DATA_ROOT` 变化时自动重开；`reset_checkpointer()` 供 shutdown / 测试隔离 |
| `context.py` | `AgentContext` dataclass（`slots=True`）：`project_id` / `session_id` / `run_id` / `gate_stage` / `target_chapter_id` / `model_ref` / `emit` / `cancel_flag`。经 `context_schema` + `Runtime.context` 注入中间件与工具，★**不使用全局变量/上下文变量传递**（避免并发 run 串数据） |
| `emitter.py` | `Emitter` dataclass 绑定 `(run_id, session_id)`，提供 `publish()` / `schedule()` / `budget_report()`，避免每处都传三元组；中间件经 `AgentContext.emit` 使用 |
| `middleware/` | 4 个自定义中间件（见下） |

**4 个自定义中间件**（挂载点受 langchain 1.4.7 真实 API 约束：只有 `before_agent`/`after_agent`、
`before_model`/`after_model`、`wrap_model_call`、`wrap_tool_call`，**没有** `before_tool`/`after_tool`/
`on_node_end`/`on_run_end`）：

| 中间件 | 挂载点 | 职责 |
| --- | --- | --- |
| `RecapInjectionMiddleware` | `abefore_model` | 调 `recap_provider` 把前情提要追加到 system 提示；token 预算检查（超阈值发 `context.compact`）；敏感内容检查**仅预留挂载位**（`_check_sensitive`，MVP 不实现） |
| `WorkspaceGuardMiddleware` | `awrap_tool_call` | **第二道**路径防线（permissions 是第一道，但只作用于内置文件工具，自定义工具/MCP 不在其覆盖范围）：`_TOOL_OPERATION` 把 7 个文件工具映射到 read/write（★`delete` 归 write）→ `WorkspaceGuard` 校验 → 审计埋点（写 `tool_call_logs`：status/duration_ms/result_excerpt/error）→ 发 `tool.start` / `tool.result`（含 `denied` 态）→ **错误指纹记忆与阻断**（自研，内置中间件无此能力）：对 `(tool_name, args_hash, error_fingerprint)` 计数，`_WARN_AFTER=2` 次注入告警 ToolMessage，`_BLOCK_AFTER=3` 次直接阻断该调用路径 |
| `FileChangeNotifyMiddleware` | `awrap_tool_call`（工具成功后） | 发 SSE `file.changed`（`virtual_path` / `change_kind`(write\|edit\|delete) / `chapter_id`）供前端热更新正文与目录树 + 触发派生索引同步（`refresh_project_index` / `reindex_project`）；★**变更目标为大纲文件时额外触发 `sync_order_from_outline`**（卷章顺序回填） |
| `WriteApprovalMiddleware` | `awrap_tool_call` | **参数级** HITL（`interrupt_on` 只能做工具级开关，无法按参数判定）。`classify_risk()` 三类高危：① `write_file` 目标是**已存在的非空章节文件**且新旧内容不同（`overwrite_threshold=200`）→ 整章覆盖；② `edit_file` 的 `old_string`/`new_string` 超 `edit_threshold=800` 字符 → 大范围改写；③ `delete` 任何项目内文件/目录 → 不可逆。命中则调 `langgraph.types.interrupt({kind:"writer_approval", tool_name, args, risk, reason, decisions})`。恢复语义四决策：`approve` 原样执行 / `edit` 用 `edited_args` 替换参数后执行 / `reject` 不执行并向模型返回「用户拒绝」ToolMessage / `respond` 不执行并把用户补充说明回给模型。`virtual_to_host()` 仅用于**只读**探测（拒绝含 `..`） |

#### `gates/` —— 四阶段门禁

| 模块 | 实现 |
| --- | --- |
| `state.py` | `STAGES = ("G0","G1","G2","G3","done","failed")`；`G3_MAX_RETRY = 2`；三种咨询类型常量 `CONSULT_G0_MISSING` / `CONSULT_G1_PRECHECK` / `CONSULT_G3_POSTCHECK`（resume 时据此路由决策）；`ConsultRequired(Exception)` 携 `kind` + `payload`（抛出后 runner 落中断并暂停）；`GateTarget`（project/volume/chapter 定位）；`GateState` dataclass（stage / outcome / g3_retry / pending_consult / 各阶段结果），`to_json()` / `from_json()` 序列化到 `agent_runs.gate_result_json`（★恢复时据此还原状态机位置） |
| `orchestrator.py` | **状态机判定**（676 行）。`GateRunner` 持有 db/state/validator_model/graph/graph_config/emit/run_id；`advance_gate(runner, resume_decision)` 推进一阶段；`resolve_target()` 定位目标章；`initial_state(target, word_target=3000)`；`prepare_g2()` 组装 G2 生成指令（`render("gate_g2_generate.md.j2")`）；`build_outline_draft_prompt()` 让 Agent 代写大纲小节。**G0/G1/G3 直连 ChatModel + JSON 结构化输出**（`llm_json()` 容错解析），不进 Agent 循环——可预测、可单测、成本低。G1 用 `render("gate_g1_precheck.md.j2")` 并**带入上一卷末章简介作锚点**（否则每卷第 1 章无上文可校验；第一卷改用大纲附录的人物/世界观设定作锚点）；G3 用 `gate_g3_postcheck.md.j2`。完成后 `summarize_chapter` → 更新派生索引 → `stage=done`。判定（理由+分数）写 Langfuse observation/score |
| `runner.py` | **门禁运行编排**（344 行，与 orchestrator 同住 `gates/`，消除「同一功能劈两半」）。`execute_gate_run()`：还原或新建 `GateState`（resume 且有 `gate_result_json` 则 `from_json`）→ 解析模型 → 装配 agent（G2 用，writer 子代理需要）→ 发 `run.start(gate_stage)` → **resume 决策路由**：`state.pending_consult` 属三种咨询类型则走门禁 resume，否则包成 `langgraph.types.Command` 给 G2 流内处理 → `while True` 状态机循环：`advance_gate` + `_persist_gate`；捕获 `ConsultRequired` → `_persist_gate_consult`（落 `agent_interrupts(type=consult)` + 发 `consult.request` + run/会话置 `awaiting_consult` + **把 `consult.kind` 记进 `state.pending_consult`** 供 resume 路由）；`G0 + outcome=="agent_write_outline"` → 跑一次 agent 流代写大纲 → 清 outcome 重查 G0；`G2` → `prepare_g2` 拿指令 → ★resume 时 `stream_input=Command`，否则 `syncer.skip_human_once=True`（内部指令不入树）→ `stream_graph` → 中断则持久化后 `awaiting_approval`，正常结束则 `stage="G3"` 继续；`done`/`failed` → 结束（`outcome.startswith("cancelled")` 判取消）。`finally` 走 `finalize` + `publish_run_end` + `close`，与普通运行完全一致 |

#### `tools/` `subagents/` `skills/` `mcp/`

| 模块 | 实现 |
| --- | --- |
| `tools/registry.py` | `build_tools()` 返回内置自定义工具清单，**当前仅 `web_search`**。★边界约定：**写作业务函数不作为 Agent 工具**（下沉为 REST 接口，位于 services/），Agent 对小说内容的操作一律通过 deepagents 内置文件工具完成——因此自定义工具只有不碰路径的 `web_search`，从根上避免「permissions 不覆盖自定义工具」的越权面 |
| `tools/web_search.py` | `httpx` 走**免费免 Key 双源**：Wikipedia API + DuckDuckGo（lite/html）。★源不可用必须返回**明确错误**而非静默空结果（供 `researcher` 子代理如实说明） |
| `subagents/builtin.py` | 5 个内置子代理的**出厂定义**（纯静态数据：name → description / system_prompt / skills / mode）。`planner`（策划大纲，技能 novel-planning）/ `writer`（撰写正文，技能 prose-writing）/ `editor`（润色校对，技能 line-editing）/ `continuity-checker`（连贯性审查，技能 continuity-check）/ `researcher`（资料检索，无技能）。提示词内嵌硬性要求（如 writer 的「必须用 write_file/edit_file 写进 `chapters/v{卷}/c{章}.md`、保留 frontmatter、不得只贴正文」） |
| `subagents/builder.py` | `build_subagents()` 装配为 `SubAgent` 列表。★**必须传 ChatModel 实例而非字符串**：`SubAgent.model` 传字符串会被 deepagents 按 `provider:model-name` 解析，而本项目 `model_ref` 是 `{provider_id}:{model_id}`（uuid:model_id）无法解析。支持 `enabled_names` 过滤（会话级 Chips）、`model_overrides`（子代理模型覆盖，未解析只 warning）、`prompt_overrides`（DB 自定义提示词覆盖内置）；★`mode=="fork"` 且有 skills 时**清空并告警**（deepagents 实测会抛错） |
| `skills/` | 8 个内置技能目录，每个 `<name>/SKILL.md`（YAML frontmatter）。`novel-planning`（大纲与分卷节奏）/ `prose-writing`（文风场景）/ `line-editing`（润色去水）/ `continuity-check`（伏笔时间线人物一致性）/ `naming`（人名地名功法命名）+ `genre-xuanhuan` / `genre-urban` / `genre-fantasy` 题材类。frontmatter 约束（deepagents 解析规则）：`name` **必须等于父目录名**、小写字母数字连字符、≤64 字符；`description` ≤1024 字符；可选 `allowed-tools`、`metadata.include_tools`；SKILL.md ≤10MB。★技能靠模型 `read_file` **渐进式披露**，故 `/skills/**` 必须开 `read allow`，否则技能全部读不到 |
| `mcp/server.py` | 内置**免费开源天气 MCP**（封装 Open-Meteo：免费无 Key、覆盖中国城市含 Geocoding），提供 `get_current_weather(city)` / `get_forecast(city, days)`，同时服务左上角动态组件与 Agent。★实现基类用 `mcp.server.mcpserver.MCPServer`（**mcp 2.3.0 中 `mcp.server.fastmcp` 已移除**，仍有 `.tool` 装饰器与 `run_stdio_async`/`run_sse_async`/`run_streamable_http_async`）；天气逻辑同时抽到 `services/weather.py`，避免两份实现漂移 |
| `mcp/adapter.py` | 第三方 MCP → LangChain tool 封装。★实测 `langchain-mcp-adapters` 与 `mcp 2.3.0` **不兼容**（`ImportError: cannot import name 'RequestContext' from 'mcp.shared.context'`），故回落后用 `mcp` 官方 client 自封装（`stdio_client` / `ClientSession` / `streamablehttp_client` + `AsyncExitStack`），`wrap_tools_async()` 转成 `StructuredTool`。安全边界：`STDIO_COMMAND_WHITELIST = ("npx","uvx","python")`，`validate_stdio_command()` **命令必须是白名单内的裸命令名**（不含路径分隔符，拒绝任意路径命令）；`probe_stdio()` / `probe_http()` 探测工具列表（`_PROBE_TIMEOUT=15s`） |

#### `memory/` `knowledge/` `sandbox/` `prompts/` `tracing/`

| 模块 | 实现 |
| --- | --- |
| `memory/tree.py` | **消息树**（255 行）：`messages.parent_id + branch_index`，会话保存 `active_leaf_id`。「重新生成」= 同父节点下新增兄弟分支；「编辑用户消息后重发」= 新建分支；前端可切换分支查看历史版本；构造 LLM 上下文时**只取 `root → active_leaf` 的活跃路径**（`_active_path`）。`add_message()` 自动算 `branch_index`（同父兄弟最大值 +1）、自动接 `active_leaf`、更新会话 `active_leaf_id`；`set_active_leaf()` 切换分支（校验消息归属会话）；`load_tree()` 返回全部消息 + active 路径标记 + 兄弟分支信息；`active_path_to_langchain()` 把活跃路径转成 `HumanMessage`/`AIMessage`/`ToolMessage`/`SystemMessage`（含 tool_calls 还原）；`thread_id_of()` = `novel:{project_id}:session:{session_id}`；`sibling_branches()` 列兄弟分支 |
| `knowledge/chapters.py` | `list_chapters_ordered()`：按大纲派生顺序列出全部章节元信息（卷 `sort_order` → 章 `sort_order` 双层排序），含 volume_id / title / status |
| `knowledge/recap.py` | `build_recap(session, project_id, before_chapter_id=None, max_chapters=6)`：书名/题材/梗概 + 「已写 N 章，仅列最近 M 章」提示 + 近 N 章（有摘要用摘要 ≤600 字，无则正文节选）+ **跨卷衔接锚点**（`_cross_volume_anchor`：若最近章节不含上一卷内容，补一条上一卷末章锚点，门禁 G1 复用）；`before_chapter_id` 只汇总该章**之前**的章节；全书第一章返回「（这是全书第一章，暂无前情提要）」。`build_pinned_chapters()` 构建会话级「已读章节」上下文段（Chips，上限 8 章防提示词膨胀），每章给标题 + 摘要（无摘要则正文节选 ≤500 字） |
| `knowledge/summarize.py` | `summarize_chapter()`：★**唯一可写 `summaries/` 的入口**（WorkspaceGuard 强制）。流程：校验项目/章节文件存在 → 已有摘要且非 force 则跳过 → 正文 <30 字跳过 → `model=None` 时取默认模型（无可用模型则跳过，返回 `skipped` 而非抛错）→ `render` 摘要提示词（要求**只输出 JSON**：`key_events`(3-6 条，每条 ≤40 字，按时间顺序) / `characters` / `foreshadowing` / `ending_state`，严格忠于原文不推测）→ `observation("summarize_chapter")` 包裹调用 → `_parse_summary_json()` **容错解析**（剥代码块标记 → `json.loads` → 失败则正则 `\{.*\}` 提取）→ 写 `summaries/ch-{id}.md`（frontmatter 含 key_events/characters/foreshadowing/ending_state/model/created_at，`allow_summaries=True`）→ 同步派生索引（失败只 warning 不阻断）。`summarize_finalized_chapters()` 批量为 `status=finalized` 章节补摘要 |
| `sandbox/backend.py` | `build_backend(session_id)` 装配 `CompositeBackend`，4 条路由（虚拟路径 → 宿主路径）：`/projects/` → `data/workspaces/`；`/skills/builtin/` → `src/app/agent/skills/`（随包只读）；`/skills/user/` → `data/skills/`；`session_id` 非空时额外挂 `/skills/session-{sid}/` → `data/session-skills/{sid}/`（会话级技能精选）。`default=StateBackend()` 承载 `conversation_history` 等状态文件。全部 `FilesystemBackend(virtual_mode=True)`——Agent 只见虚拟路径。★**禁止 `LocalShellBackend` / `LangSmithSandbox`**：会激活 `execute` 任意命令执行，且与 permissions 冲突（`FilesystemMiddleware.__init__` 在「backend 支持执行 + 权限路径未全部 scoped 到 routes」时**直接抛 `ValueError`**）。用 `FilesystemBackend` 时 `supports_execution()` 返回 False，deepagents 会在 `wrap_model_call` 阶段**自动把 `execute` 从模型可见工具中移除**，无需自行裁剪 |
| `sandbox/permissions.py` | `PERMISSION_RULES` **8 条有序三元组常量**（顺序即语义，测试逐行断言与手册表一致）：① `["read","write"] /projects/{pid}/** allow`（当前项目可读写）② `["write"] /projects/** deny`（其它项目禁写，★`delete` 归入 `write`）③ `["read"] /projects/** allow`（允许跨项目阅读）④ `["read"] /skills/** allow`（技能渐进披露依赖 read_file）⑤ `["write"] /skills/** deny`（技能由服务层维护）⑥ `["read","write"] /conversation_history/** allow`（summarization 驱逐卸载）⑦ `["read","write"] /large_tool_results/** allow`（大工具输出卸载）⑧ `["read","write"] /** deny`（**兜底必须最后**）。★判定机制：**首条匹配生效、无匹配默认 allow**，故兜底 deny 位置错了就会全线放行；`build_permissions(project_id)` 把 `{pid}` 替换为当前项目 |
| `prompts/renderer.py` | Jinja2 `Environment`：`FileSystemLoader(TEMPLATES_DIR, encoding="utf-8")` + `StrictUndefined`（★漏传变量立即报错，便于回归）+ `trim_blocks`/`lstrip_blocks`/`keep_trailing_newline` + `autoescape` 对 `md.j2`/`j2`/`md`/`txt` **禁用**（提示词是纯文本不是 HTML）；`lru_cache(1)` 单例环境。5 个模板：`main_agent.md.j2` / `gate_g1_precheck.md.j2` / `gate_g2_generate.md.j2` / `gate_g3_postcheck.md.j2` / `rewrite_selection.md.j2` |
| `tracing/langfuse.py` | **Langfuse v5（OTel 版）**。★v5 与 v2 API 完全不同：`Langfuse.trace()` / `trace.span()` **已不存在**（仓库 `demo-langfuse.py` 是 v2 写法，不可照抄）。三种用法：① **自动埋点为主**——`langfuse.langchain.CallbackHandler` 挂到 `config={"callbacks":[handler]}`，自动产出 trace/span/generation（含 token 用量）；② **手动补充**——`observation(name, metadata)` 上下文管理器包裹门禁 G0/G1/G3 与 `summarize_chapter` 等服务层 LLM 调用；③ `create_score(name, value, trace_id=...)` 写分数（字数/工具成功率/是否降级/门禁判定）。`get_client()` **惰性初始化 + 未配 Key 自动降级**（返回 None，所有函数变 no-op，功能不受影响）；`current_trace_id()` 取当前 trace id 落 `agent_runs.langfuse_trace_id`，★含 v5 API 差异容错（先试 `get_current_observation().trace_id`，回落 `get_current_trace().id`）；`reset_client()` 供 `/config/reload` 后调用 |

### 4.7 前端模块要点

| 模块 | 实现 |
| --- | --- |
| `api/sse.ts` | EventSource 封装：自动重连带 `Last-Event-ID`；手动重连走 `?last_event_id=`（EventSource 无法自定义 header）；收到 `error(41001)` 时回落重拉 `GET /agent/sessions/{sid}/messages` 重建消息列表 |
| `api/http.ts` | axios 封装：统一拆 `{code,message,data}`；按 `code` 分发（40901 → 触发 ConflictBanner；40902 → 提示运行已结束；42201 → 命名错误提示） |
| `components/agent/AgentPanel.vue` | 会话面板：会话列表（按项目隔离）+ 消息流（含分支切换）+ 输入区 + **上下文 Chips 选择器**（子代理/技能/已读章节三类，含「＋当前章」快捷，增删即 `PATCH /context` 持久化，会话切换时回填）+ 模型切换（单条消息级覆盖）+ 打断/重新生成/复制/导出会话 |
| `components/editor/TiptapEditor.vue` | Tiptap（ProseMirror）：StarterKit + CharacterCount + Placeholder；自动保存（`autosave_interval_sec`，不产生快照）；保存走 `PUT content` 携 `base_version` |
| `components/editor/aiSourceMark.ts` | Tiptap Mark 扩展：标记 AI 生成/改写的文本，**点击可回滚为原文** |
| `components/editor/RewriteDiffDialog.vue` | 选区改写 diff 预览：**句级 LCS diff** 高亮（增/删/改三色），用户「接受」才落盘 |
| `components/editor/ConflictBanner.vue` | 409/40901 时弹出：展示服务端最新 content/version/updated_at/**last_writer**(manual\|agent)，让用户选「查看 diff 合并」或「强制覆盖」（强制覆盖需再次带最新 `base_version`） |
| `components/agent/GateStatusBar.vue` | 订阅 `gate.update` 渲染 G0→G3 阶段流转 + 重试计数 + 各阶段判定摘要 |
| `components/agent/ApprovalCard.vue` | 渲染 `interrupt.request`：工具名 + 参数 + 风险说明 + 四决策按钮（批准/编辑后批准/拒绝/回复补充），`edit` 时参数可编辑，`reject`/`respond` 时可填说明 |
| `components/agent/ConsultCard.vue` | 渲染 `consult.request`：问题 + `options` 选项按钮 + 自由输入框（`allow_text`），回复走同一 resolve 接口（`decision=option\|text`） |
| `components/sidebar/WeatherWidget.vue` | 宠物**两态**动画（写作中敲键盘 / 久无进展打瞌睡，阈值 `pet_idle_minutes`）+ 城市 + 当前天气。★浏览器定位需 HTTPS 或 localhost 且用户可拒绝 → 拒绝/失败/超时回落 `user_settings.city` → 再失败只显示宠物与占位天气；**坐标不入库、不写审计** |
| `components/editor/VolumeChapterTree.vue` | 卷章树，★**只读顺序**（无拖拽、无上移下移按钮）——顺序是大纲派生值 |

---

## 五、运行逻辑

### 5.1 应用启动（lifespan）

```
uvicorn 启动 → app/main.py lifespan
  ├─ init_database()：create_all 建表 → 打 alembic 版本戳（两条初始化路径互相幂等）
  │                   → FTS5 两步探测建 story_fts（不可用则永久降级 LIKE）
  ├─ seed_builtins()：播种 5 个内置子代理（幂等，不覆盖用户改动）
  ├─ 内置技能一致性检查（skill_configs ↔ 包内 SKILL.md）
  ├─ _mount_frontend()：挂载 src/web/dist（SPA history 回落）
  └─ 统一异常处理 / CORS / 结构化日志（key|token|secret 字段自动掩码）
```

### 5.2 普通对话运行（发消息 → 流式回复）

```
前端 POST /agent/sessions/{sid}/messages
  │  （该会话有活跃 run → 409 / 40902）
  ▼
api/agent.py：services.agent.require_session 取会话行 → run_manager.start()
  ▼
loop/runner.py：用户消息入消息树（接 active_leaf 或按 parent_id 建兄弟分支）
  → 建 agent_runs(status=pending) → 会话置 running → sse_bus.open()
  → _spawn()（per-run asyncio.Lock 串行化）→ 立即返回 202 + run_id
  ▼
loop/resolve.py：解析主模型 + 降级链（未配模型 → 50201）
  → make_recap_provider（前情提要 + 会话「已读章节」Chips）
  → session_context_of（会话级子代理/技能 id → name）
  ▼
loop/builder.py：build_agent → create_deep_agent(backend, permissions, checkpointer,
                  tools, subagents, skills, 9 层 middleware, context_schema=AgentContext)
  ▼
lifecycle.graph_config() 生成 thread_id + recursion_limit
resolve.attach_langfuse() 挂 CallbackHandler
发 run.start 事件
  ▼
graph.astream(stream_mode=["updates","messages"], subgraphs=True, context=ctx)
  ▼
loop/streaming.py：逐 chunk 映射为 SSE
  ├─ namespace 首次出现 → subagent.start（子代理泳道开启）
  ├─ messages 模式 → message.delta / subagent.delta / usage
  └─ updates 模式 → __interrupt__ → 落 agent_interrupts + interrupt.request|consult.request
                  → model 节点完成 → message.complete / subagent.end
                  → 顶层 tools 节点完成 → 对所有活跃子代理发 subagent.end
                  → write_todos → plan.update
                  → 每次 updates 后 message_sync 增量落库消息树
  ▼
中间件并行生效（9 层固定顺序）：
  ModelRetry → ModelFallback → ModelCallLimit → TodoList → Summarization
  → RecapInjection（注入前情提要 + 预算检查 → context.compact）
  → WorkspaceGuard（路径校验 + 审计 + tool.start/tool.result + 错误指纹阻断）
  → FileChangeNotify（file.changed + 同步派生索引；改大纲则触发 sync_order_from_outline）
  → WriteApproval（参数级高危写入 → interrupt）
  ▼
loop/lifecycle.py：finalize 收尾（agent_runs 汇总 + 会话状态 + 刷新字数索引 + Langfuse score）
  → publish_run_end → sse_bus.close()
```

前端 `GET /agent/runs/{run_id}/stream` 订阅事件（先建流再发消息、或先发消息再建流都支持，
环形缓冲会补齐）。断线重连带 `Last-Event-ID`（或 `?last_event_id=`）从缓冲补发；
缓冲丢失 / 进程重启 → `error(41001)` 并关流，前端回落重拉消息树。

**17 类 SSE 事件**：`run.start` / `message.delta` / `message.complete` / `tool.start` /
`tool.result` / `subagent.start` / `subagent.delta` / `subagent.end` / `plan.update` /
`file.changed` / `context.compact` / `gate.update` / `consult.request` / `interrupt.request` /
`usage` / `error` / `run.end`。

### 5.3 门禁生成运行（「生成本章」→ G0→G3）

```
用户请求生成第 N 章（POST messages 带 target_chapter_id）
  ▼
loop/runner.py 检测 target_chapter_id 非空 → 分流到 gates/runner.py:execute_gate_run
  ▼
还原或新建 GateState（resume 且有 gate_result_json → from_json 还原状态机位置）
  ▼
G0 大纲存在性（orchestrator，parse_outline 严格定位目标章简介小节）
  │  区分两类缺失（咨询文案不同）：① 无对应小节 → 引导按模板补小节
  │                              ② 小节存在但简介为空 → 引导填写简介
  │  ★两类均不自动插入占位小节
  │──无简介──▶ ConsultRequired(g0_missing) → consult.request
  │            （选项：让 Agent 代写大纲 / 我自己去「大纲」节点补 / 取消本次生成）
  │            → 运行置 awaiting_consult 暂停
  │            若选「代写大纲」→ 跑一次 agent 流写大纲 → 清 outcome 重查 G0
  │有简介
  ▼
G1 衔接预检（orchestrator 服务层直连 LLM + JSON 结构化输出）
  │  比对该卷各章简介的衔接合理性（因果/时间线/人物状态/伏笔承接）
  │  ★带入上一卷末章简介作锚点（否则每卷第 1 章无上文可校验；
  │    第一卷改用大纲附录的人物/世界观设定作锚点）
  │──不合理──▶ ConsultRequired(g1_precheck) → consult.request（问题清单 + 修改建议）
  │            用户可改大纲后重跑，或「强制跳过」（需显式确认并落审计）
  │合理
  ▼
G2 生成（★唯一进入 deep agent 的阶段）
  │  prepare_g2() 组装生成指令（章简介 + build_recap，目标字数 agent_word_target=3000）
  │  writer 子代理用文件工具写入 chapters/v{卷}/c{章}.md
  │  ★syncer.skip_human_once = True（生成指令是内部消息，不入消息树）
  │  ★写入前 WriteApproval 判定整章覆盖 → 先快照旧正文进 chapter_revisions(source="agent")
  │  期间可触发参数级审批 → 持久化 GateState → awaiting_approval
  │  正常结束 → state.stage = "G3" 继续
  ▼
G3 匹配后检（orchestrator 服务层直连 LLM）
  │  比对生成正文与该章简介的匹配度（要点覆盖 / 是否偏离 / 是否引入简介外重大剧情）
  │──不匹配──▶ 自动重生成（g3_retry ≤ G3_MAX_RETRY=2，每次重生前同样先快照）
  │            仍不匹配 → ConsultRequired(g3_postcheck) → consult.request
  │                       （认可现状 / 手动修改正文或大纲 / 补充指示后再生成）
  │匹配
  ▼
完成：summarize_chapter 生成摘要 → 更新派生索引 → stage=done → run.end
```

**执行主体分工**：G0/G1/G3 由 `gates/orchestrator.py` 服务层直连 ChatModel 完成
（可预测、可单测、成本低，**不进入 Agent 循环**）；只有 G2 进 deep agent。
`agent_runs` 的 `gate_stage` / `gate_retry_count` / `gate_result_json` 全程流转，
前端 `GateStatusBar` 据 `gate.update` 事件渲染。

**适用范围**：门禁仅约束 **AI 生成正文**路径；用户手工编辑不受限。

### 5.4 中断与恢复（HITL）

```
Agent 触发中断（三类来源）
  ① 工具级：interrupt_on={"delete": True} 无条件暂停
  ② 参数级：WriteApprovalMiddleware.classify_risk 命中 → langgraph.types.interrupt()
  ③ 门禁咨询：orchestrator 抛 ConsultRequired
  ▼
streaming._persist_and_emit_interrupt / gates._persist_gate_consult
  → 落 agent_interrupts(type=approval|consult, payload_json ≤20000 字符)
  → 发 interrupt.request | consult.request
  → run.status = awaiting_approval | awaiting_consult，会话同状态
  → publish_run_end({status, paused:true})
  ▼
前端渲染 ApprovalCard / ConsultCard → POST /agent/interrupts/{id}/resolve
  │  approval: approve | edit(带 edited_args) | reject(带 message) | respond(带 message)
  │  consult : option(带 choice) | text(带 text)
  │  （中断已处理 → 409；对已结束 run → 409 / 40902）
  ▼
services.agent.resolve_interrupt：校验 + 归一化为 langchain HITL batched resume 形状
  │  approve → {"decisions":[{"type":"approve"}]}
  │  edit    → {"decisions":[{"type":"edit","edited_action":{"name":<真实工具名>,"args":{...}}}]}
  │            ★name 必须是 agent 已注册的工具名（_apply_edit 会校验，传错抛 ValueError）
  │  reject  → {"decisions":[{"type":"reject","message":...}]}
  │  respond → {"decisions":[{"type":"respond","message":...}]}
  → 落库决定（decision/response_json/decided_by/decided_at）+ 审计
  → run 与会话置 running → 返回 {run_id, resume_value}
  ▼
api 层 asyncio.create_task(run_manager.resume(...))（不阻塞响应）
  ▼
sse_bus.reopen()（finished=False，seq 续增，缓冲保留 → 客户端无缝续传）
  → Command(resume=resume_value) 续跑同一 run（thread_id 不变）
  → ★门禁运行按 state.pending_consult 路由：
      属三种咨询类型 → 走门禁 resume_decision
      否则（approval）→ 包成 langgraph Command 给 G2 流内处理
```

### 5.5 取消

```
POST /agent/runs/{id}/cancel
  ▼
置 cancel_flags[run_id]["cancelled"] = True（★协作式：当前工具/模型调用返回后才停）
  → run 置 cancelling
  ▼
给 3 秒优雅窗口：await wait_for(shield(task), timeout=3.0)
  超时/异常 → task.cancel() 硬取消
  ▼
deepagents 内置 PatchToolCallsMiddleware 自动修复被截断的消息历史
  ▼
run 置 cancelled + ended_at，会话回 idle → 发 run.end({"status":"cancelled"}) → sse_bus.close()
```

`stream_graph` 每处理一个 chunk 都检查 `cancel_flag`，命中即置 `result.cancelled` 并 break。

### 5.6 文件写入的两条路径（收敛到同一处）

```
路径 A：前端编辑 → PUT /chapters/{cid}/content（乐观锁 base_version）
        services/volumes.write_content
路径 B：Agent 工具 → write_file / edit_file / delete
        permissions（第一道）+ WorkspaceGuardMiddleware（第二道）
                        │
                        ▼
        services/workspace.WorkspaceGuard（★服务层唯一文件写入口）
        ├─ resolve() 归一化后必须严格落在白名单根内，否则 403 / 40301
        ├─ 拒绝含 .. 的路径、逐段检查符号链接解析后是否越界
        ├─ 跨项目写一律拒绝；summaries/ 只允许 summarize_chapter 写
        └─ 写入成功后同步更新派生索引（story_fts / project_index）
             失败不阻断写入 → 标 project_index.dirty=1 + 告警
             → writer rebuild-index / POST /novels/{id}/index/rebuild 全量重建
                        │
                        ▼
        FileChangeNotifyMiddleware 发 file.changed → 前端热更新正文与目录树
        （变更目标是大纲文件 → 额外触发 sync_order_from_outline 回填卷章顺序）
```

### 5.7 消息树双轨持久化

```
① 业务库（app.db）：messages / tool_call_logs / agent_runs
   用途：前端渲染、审计、导出会话
   写入：message_sync.MessageSyncer 按索引基线增量同步 graph state

② LangGraph checkpoint（agent_state.db）：AsyncSqliteSaver
   用途：Agent 恢复运行、HITL 中断续跑
   键：thread_id = novel:{project_id}:session:{session_id}
   ★进程内单例（aiosqlite 连接须长生命周期持有，每 run 重开会丢中断态）
   ★DeepAgentState.messages 用 DeltaChannel(snapshot_frequency=50) 增量存储
     → checkpoint 体积 O(N) 而非 O(N²)（长会话尤其重要）
```

两轨以 `run_id` / `thread_id` 关联，跨库不建外键。

---

## 六、技术体系

### 6.1 技术栈与实测版本

| 层 | 技术 | 版本（`.venv` 实测） |
| --- | --- | --- |
| **Agent 框架** | deepagents | 0.7.23 |
| | langchain | 1.4.7 |
| | langchain-core | 1.7.0 |
| | langgraph | 1.2.16 |
| | langgraph-checkpoint-sqlite | 3.1.2 |
| | mcp | 2.3.0 |
| **可观测** | langfuse | 5.0.4（★OTel 版，API 与 v2 完全不同） |
| **Web / API** | fastapi | 0.162.0 |
| | uvicorn | 0.54.0 |
| | sse-starlette | ≥2.1 |
| | pydantic | 2.14.1 |
| **数据** | sqlalchemy (async) | 2.1.6 |
| | aiosqlite / alembic | ≥0.20 / 1.20.1 |
| | SQLite | 3.53.x（FTS5 可用：`unicode61`/`trigram`/`porter` 均可建表，`bm25()` 可用） |
| **工具库** | jinja2 / python-frontmatter / pyyaml / structlog / httpx / tenacity / tiktoken | — |
| **运行时** | Python | 3.12.15（uv 管理 `.venv`） |
| **前端** | vue / vue-router / pinia | 3.5.x / 4.5.x / 2.3.x |
| | element-plus / axios / dayjs / markdown-it / dompurify | 2.9.x / 1.7.x / — / 14.x / 3.2.x |
| | @tiptap/{vue-3,starter-kit,pm,extension-character-count,extension-placeholder} | 2.11.x |
| | vite / typescript / vue-tsc | 6.x / 5.7.x / 2.2.x |
| **依赖管理** | uv（`pyproject.toml` + `uv.lock` 唯一事实源） | 0.12.x |
| **部署** | Dockerfile（node:24 构建前端 → python:3.12-slim 运行）+ docker-compose | — |

`requirements.txt` 为兼容保留，**不作为事实源**。

### 6.2 deepagents 能力与本项目诉求的映射

| 诉求 | deepagents 0.7.23 实测能力 | 本项目用法 |
| --- | --- | --- |
| Agent-Loop | `create_deep_agent(...)` 返回 `CompiledStateGraph`（基于 LangGraph），支持 `astream` / `astream(subgraphs=True)` 取子代理事件 | `loop/builder.py` 装配，`loop/streaming.py` 消费事件 |
| Tools | `tools=` 接受 `BaseTool` / callable / `dict`；**MCP 工具不在 deepagents 内建** | 只注册 `web_search`；MCP 经 `agent/mcp/adapter.py` 转 LangChain tool |
| Skill | `skills=` 参数是 **backend 虚拟路径**而非磁盘路径；靠模型 `read_file` 读 `SKILL.md` 渐进式披露；`name` 必须等于父目录名 | `SKILL_PATHS = ["/skills/builtin/", "/skills/user/"]` + 会话精选路由 |
| Sub-agent | `SubAgent` 字段：`name / description / system_prompt / tools / model / middleware / interrupt_on / skills / permissions / response_format / mode`；`mode` 仅 `isolated`(默认)/`fork`，★**`fork` 下设 `skills` 会抛错**、`system_prompt` 是**追加**到继承提示之后 | `subagents/builder.py` 装配，`fork`+skills 时清空并告警 |
| 沙箱 | 可插拔 backend：`StateBackend` / `FilesystemBackend` / `StoreBackend` / `CompositeBackend` / `ContextHubBackend` / `LangSmithSandbox` / `LocalShellBackend` | ★**只用 `FilesystemBackend` + `CompositeBackend`** |
| 半隔离 | `permissions=[FilesystemPermission(operations, paths, mode)]`；`operations` 仅 `read`/`write`（★`delete` 归入 `write`）；`mode` 为 `allow`/`deny`/`interrupt`；★**首条匹配生效、无匹配默认 `allow`** | 8 条有序规则，兜底 `deny /**` 必须最后 |
| 会话持久 | `checkpointer=`（`None` 或 `bool` 或 `BaseCheckpointSaver`），按 `thread_id` 维度；`DeepAgentState` 用 `DeltaChannel(snapshot_frequency=50)` 增量存 messages | `AsyncSqliteSaver` 进程内单例 |
| 人为介入 | `interrupt_on={tool_name: True 或 InterruptOnConfig}`；`InterruptOnConfig` 支持 `allowed_decisions` 与 `description`。★**只能做工具级开关、无法按参数判定**，参数级需自定义中间件调 `langgraph.types.interrupt()` | `interrupt_on={"delete":True}` + `WriteApprovalMiddleware` |
| 上下文压缩 | `create_summarization_middleware(model, backend)`：额外把驱逐消息卸载到 `/conversation_history/{session_id}.md`、预截断过大的 `write_file/edit_file` 参数、遇 `ContextOverflowError` 自动摘要重试 | `_build_summarization()`，失败回落 langchain 版 `SummarizationMiddleware` |
| 计划/待办 | ★**默认中间件栈不含 `TodoListMiddleware`**（`langchain.agents.factory` 未引用它） | 显式加入以获得 `write_todos` 工具与 `plan.update` 事件 |
| `general-purpose` | 传入 `subagents` 时自动追加；关闭需经 harness profile `GeneralPurposeSubagentProfile(enabled=False)`（**不是普通参数**）。harness profile 按模型匹配（内置 Anthropic sonnet/opus/haiku、NVIDIA、OpenAI codex），Qwen/DashScope 无匹配 profile → 走默认行为 | 保持默认追加 |

**实测确认但本项目不使用**（避免误读）：`store=` / `StoreBackend`、`memory=` / `MemoryMiddleware`、
`RubricMiddleware`、`AsyncSubAgentMiddleware`（LangSmith 部署专用）、`ContextHubBackend`、
`LangSmithSandbox`、`LocalShellBackend`、harness/provider profile 注册机制。

### 6.3 langchain 1.4.7 中间件钩子的真实边界

`AgentMiddleware` **只有**：`before_agent` / `after_agent`、`before_model` / `after_model`、
`wrap_model_call`、`wrap_tool_call`（各带 `a*` 异步版）+ `name` / `state_schema` /
`trace_policy` / `transformers`。

★**没有** `before_tool` / `after_tool` / `on_node_end` / `on_run_end`。这一约束直接决定了本项目两个设计：

1. 工具级校验/审计/通知全部落在 `awrap_tool_call`（4 个自定义中间件都挂这里）；
2. **运行收尾不在中间件里做**，由 `loop/lifecycle.py:finalize()` 在 `astream` 结束后统一执行。

### 6.4 关键算法与数据结构

| 主题 | 方案 |
| --- | --- |
| **中文全文检索** | FTS5 `tokenize='unicode61'` + **手工二元切分**（`少年走出山村` → `少年 年走 走出 出山 山村`）。★`trigram` 对 2 字词**零命中**（`MATCH '少年'` 返回 0 行，3 字起才命中），不能作主方案。索引侧与查询侧**必须用同一个 `bigram_tokenize()`**，否则永远查不到 |
| **FTS5 可用性** | **两步探测**：先 `SELECT fts5(NULL)`（已编译返回 `(None,)`，未编译报 `no such function: fts5`），再建临时表验证 `unicode61` 与 `bm25()`；任一步抛 `sqlite3.OperationalError` 即**永久降级** `LIKE`（进程内缓存结果） |
| **大纲解析** | 严格层级正则 `^#(?!#)\s+(.*)$` / `^##(?!#)\s+(.*)$`（★宽松正则会把 `## 第三章 卷土重来` 误判为卷节）；附录节（`^附录`）下的 H2 不参与章匹配；卷章名**归一化后完全相等**才算命中 |
| **消息树增量同步** | **索引基线**而非 id 匹配（graph state 消息 id 由 LangGraph 生成，与业务库 uuid 无对应；state 的 messages 是追加式的）→ 只记「已同步下标」，每次处理新增部分 |
| **SSE 断线续传** | 每 run 内存环形缓冲 `deque(maxlen=500)` + `seq` 单调递增 + `Last-Event-ID` 补发；缓冲淘汰或进程重启 → `41001` + 关流 + 前端重拉消息树 |
| **乐观锁** | 章节 frontmatter `version` + 请求 `base_version` 比对；不符返回 409/40901 且 `data` 回传最新 content/version/updated_at/**last_writer**(manual 或 agent) |
| **重复错误避免** | 对 `(tool_name, args_hash, error_fingerprint)` 计数：2 次注入告警 ToolMessage，3 次阻断该调用路径（★自研，内置中间件无此能力） |
| **EPUB 生成** | 自研（不依赖外部库）：`mimetype`（**必须第一个且不压缩**）+ `META-INF/container.xml` + `content.opf` + `nav.xhtml` + 各章 xhtml；markdown 段落转 xhtml；mobi 经 calibre `ebook-convert` 二次转换 |
| **上下文治理** | token 预算（`max_context × 0.5`）+ 前情提要替代逐章原文 + `DeltaChannel` 增量 checkpoint + summarization 驱逐卸载 + 子代理上下文隔离（`mode=isolated`）+ `ModelCallLimit(40)` 软上限 + `recursion_limit(60)` 硬上限 |

### 6.5 安全模型

| 面 | 措施 |
| --- | --- |
| **密钥** | API Key **只存 `.env`**，DB 仅存 `api_key_env`（变量名引用）；UI 展示掩码 `sk-****abcd`；写回走**白名单键**（`*_API_KEY`、`LANGFUSE_*`）+ 临时文件 `os.replace` 原子写 + `.env.bak` 备份 + 进程内互斥锁；日志 processor 对 `key`/`token`/`secret` 字段自动掩码 |
| **文件越权** | 两道防线：① deepagents `permissions`（8 条有序规则，作用于内置文件工具）② `WorkspaceGuardMiddleware` + `services/workspace.WorkspaceGuard`（覆盖自定义工具/MCP，`resolve()` 归一化 + 白名单根约束 + 逐段符号链接检查 + 拒绝 `..` + 跨项目拒绝） |
| **命令执行** | ★`execute` **不开放**：用 `FilesystemBackend` 时 `supports_execution()` 返回 False，deepagents 自动把 `execute` 从模型可见工具中移除。禁用 `LocalShellBackend`/`LangSmithSandbox`（会激活执行且与 permissions 冲突抛 `ValueError`） |
| **stdio MCP** | 等价「受限的任意命令执行」→ 三重约束：可执行文件**白名单**（`npx`/`uvx`/`python`，拒绝任意路径命令）+ 用户**二次显式确认**（`mcp_servers.confirmed_at`，展示完整命令）+ **命令全文落审计**。默认不预置任何第三方 stdio MCP |
| **高危写入** | 参数级 HITL：整章覆盖 / 大范围 edit（>800 字符）/ 任何 delete → `interrupt()` 转人工审批；写入前先快照 `chapter_revisions(source="agent")` 保证可回滚 |
| **第三方依赖** | MCP 仅接**开源免费、无需认证**的服务（不提供认证头/Token 配置字段） |
| **注入面** | Agent 不注册碰路径的自定义工具（只有 `web_search`），从根上收窄「permissions 不覆盖自定义工具」的越权面 |

---

## 七、开发重难点

### 7.1 ★难点一：门禁编排与 Agent 循环的职责切分

**问题**：AI 生成正文必须以大纲章简介为前提，但「让 Agent 自觉检查大纲」不可靠——
模型可能跳过检查、也可能反复重试不收敛。

**方案**：**服务层硬编排**为四阶段状态机（非 Agent 自觉执行），且按「可预测性」切分执行主体：

- G0/G1/G3 = **服务层直连 ChatModel + JSON 结构化输出**（不进 Agent 循环）→ 可预测、可单测、成本低；
- G2 = **唯一进入 deep agent 的阶段**（需要文件工具与 writer 子代理）。

**踩坑与对策**：

| 坑 | 对策 |
| --- | --- |
| 状态机要跨中断恢复（咨询暂停 → 用户回复 → 从原阶段续跑） | `GateState` 序列化进 `agent_runs.gate_result_json`；resume 时 `from_json` 还原；★用 `state.pending_consult` 记录**是哪种咨询**，resume 时据此路由：属三种咨询类型走门禁 resume，否则包成 `langgraph.types.Command` 交给 G2 流内处理 |
| resume 值的形状不统一（门禁咨询 vs langchain HITL batched） | `resolve_interrupt` 统一归一化为 `{"decisions":[Decision]}`；门禁侧 `_extract_consult_decision` 提取 `{decision, choice, text}` |
| G3 反复重生成不收敛（死循环） | `G3_MAX_RETRY=2` 上限 + 每次重生前快照（可回滚）+ 仍不匹配发咨询卡片兜底（认可现状/手动修改/补充指示）+ G1「显式确认跳过」落审计 |
| 门禁功能被劈成两半（状态机在 `gates/`、运行编排在 `loop/runner.py`），读流程必须跨文件跳转 | 把 `_execute_gate_run` 及其 3 个辅助函数整体迁入 `gates/runner.py`，与 `orchestrator.py` 同住 |
| G2 的生成指令是**内部消息**，不应出现在用户可见的消息树里 | `MessageSyncer.skip_human_once` 标记：跳过下一条 human 消息（只推进索引指针，不落库） |
| G0 缺失有两种语义（无小节 vs 小节存在但简介为空），咨询文案应不同 | `parse_outline` 区分两类，咨询卡片分别引导「按模板补小节」与「填写简介」；★两类均**不自动插入占位小节**（避免污染用户大纲） |
| G1 校验每卷第 1 章时无上文可比 | 带**上一卷末章简介作锚点**；第一卷改用大纲附录的人物/世界观设定作锚点 |

### 7.2 ★难点二：流式事件的完整映射与断线续传

**问题**：LangGraph 的 `astream` 输出是 `(namespace, mode, payload)` 三元组流，与前端需要的
语义事件（消息增量、工具调用、子代理泳道、待办计划、门禁流转、审批卡片）之间没有现成映射；
且 SSE 会断线、进程会重启，事件不能丢。

**方案**：

- **双 stream_mode**：`["updates","messages"]` —— `messages` 拿 token 级增量，`updates` 拿节点完成与中断；
- **`subgraphs=True`** 拿子代理事件，用 `namespace` 区分泳道（`_subagent_name()` 从形如
  `("task:planner",)` 或 `("planner:xxxx",)` 的 namespace 提取名字）；
- **泳道生命周期**：新 namespace 首次出现 → `subagent.start`；顶层 `tools` 节点完成 → 对所有
  活跃子代理发 `subagent.end`（★顶层完成是子代理结束的唯一可靠信号）；
- **环形缓冲 + seq**：每 run 一个 `deque(maxlen=500)`，`seq` 单调递增；`subscribe(run_id, last_event_id)`
  返回 `(queue, backlog, lost, finished)` 四元组，`lost` 判定为请求位点已被淘汰；
- **两种重连方式**：`Last-Event-ID` 请求头（EventSource 自动重连）或 `?last_event_id=`
  （手动重连，★EventSource 无法自定义 header）；取两者较大值；
- **先建流/先发消息都支持**：`POST messages` 返回 202 后，连接前产生的事件由环形缓冲补齐；
- **不可续传时明确失败**：发 `error(41001)` 并关流，前端回落重拉消息树（★不做静默丢事件）；
- **心跳保活**：`wait_for(queue.get(), timeout=15)` 超时发 `{"comment":"ping"}`
  （★SSE 注释行，不占 seq，不影响续传位点）；
- **`reopen()` 无缝续跑**：中断恢复时 `finished=False`、`seq` 续增、**缓冲保留**，客户端无需重连。

### 7.3 ★难点三：消息树的双轨一致性与增量同步

**问题**：消息既要给前端渲染/审计/导出（业务库），又要给 LangGraph 恢复运行（checkpoint）；
而 graph state 里的消息 id 由 LangGraph 生成，与业务库 uuid **无对应关系**。

**方案**：

- **双轨持久化**：业务库 `messages`/`tool_call_logs`/`agent_runs` + LangGraph `AsyncSqliteSaver`
  （`data/agent_state.db`，与业务库分离，跨库不建外键），以 `run_id`/`thread_id` 关联；
- **★索引基线增量同步**（不按 id 匹配）：利用 state 的 messages 是**追加式**的这一性质，
  只记「已同步到的下标」，每次 sync 只处理新增部分；
- **基线确定**：首轮用户消息已由 API 层落库、state 里它将成为 `messages[0]` → 基线 = 1、
  parent = 该消息 id；恢复时中断前消息已全部落库 → 基线 = 当前 state 长度、parent = 会话 `active_leaf_id`；
- **未完成消息不落库**：`status=="streaming"` 或（无内容且无 tool_calls）视为未完成，留待下次 sync；
  中断时 `force=True` 强制落库已产生的消息；
- **分支语义**：`parent_id + branch_index`，「重新生成」= 同父兄弟分支，「编辑用户消息后重发」= 新建分支；
  构造 LLM 上下文时**只取 `root → active_leaf` 的活跃路径**；
- **checkpoint 体积**：`DeltaChannel(snapshot_frequency=50)` 增量存储 → O(N) 而非 O(N²)；
- **checkpointer 必须进程内单例**：aiosqlite 连接须长生命周期持有（每个 run 重开会丢中断态）；
  `asyncio.Lock` + 双检；`DATA_ROOT` 变化时自动重开（★否则测试会串库）。

### 7.4 ★难点四：权限「首条匹配 + 默认放行」的顺序陷阱

**问题**：deepagents `FilesystemPermission` 的判定机制是**首条匹配生效、无匹配默认 `allow`**
（`_check_fs_permission` 返回 allow）。这意味着规则顺序写错就会**全线放行**，且不会报错。

**方案**：

- 8 条规则以 **`PERMISSION_RULES` 常量数组**集中定义，每条带注释说明意图；
- **兜底 `deny /**` 必须最后**；新增规则只能插在兜底之前；
- ★把设计约束**编码成常量 + 配回归测试**：`tests/test_permissions.py` 逐行断言常量数组与设计表一致
  （顺序回归测试），并对每条规则做越权用例（读其它项目允许、写其它项目拒绝、写技能拒绝、
  兜底拒绝等）；
- **第二道防线**：`permissions` 只作用于内置文件工具，自定义工具/MCP 不在其覆盖范围 →
  `WorkspaceGuardMiddleware` + `services/workspace.WorkspaceGuard` 兜底；
- ★`delete` **归入 `write` 权限**（deepagents 的 `operations` 只有 `read`/`write`），
  故「删其它项目文件」会被第 2 条 `write deny` 拦下；
- ★`/skills/**` 必须开 `read allow`：技能靠模型 `read_file` 读 `SKILL.md` 渐进式披露，
  漏开 read 会导致**技能全部静默不可用**（配套集成测试断言 Agent 能读到内置技能全文）。

### 7.5 ★难点五：中文全文检索

**问题**：SQLite FTS5 默认分词器对中文无效（整段当一个 token）；`trigram` 看似可行但实测
**对 2 字词零命中**（`MATCH '少年'` 返回 0 行，3 字起才命中），而中文小说检索大量是 2 字词。

**方案**：

- **手工二元切分 + `unicode61`** 为主方案：把中文切成空格分隔的二字词后存入 FTS5 列，
  非 CJK 片段（拉丁词、数字）原样保留交给 unicode61；
- ★**查询侧必须用同一个 `bigram_tokenize()`**，否则永远查不到（这是最易踩的坑）；
- `project_id` **必须入表且 UNINDEXED**：查询时附加 `AND project_id = ?` 过滤，否则跨项目串数据；
- **降级路径**：FTS5 不可用或查询异常时回落 `LIKE '%q%'`，★接口签名与返回结构不变
  （只多 `data.degraded=true`），前端无需分支处理；
- **两步探测**（见 6.4）确保降级判定可靠且只探一次；
- 不使用向量库（零 Embedding 费用、零额外服务）；数据源为大纲单文件 + `summaries/`；
  ★检索**不作为 Agent 工具**（Agent 用内置 `grep` 搜文件），避免两套检索语义漂移。

### 7.6 ★难点六：并发写入与内容一致性

**问题**：自动保存（前端定时）、Agent 写章（后台任务）、门禁重生成可能同时改同一章；
Agent 改文件后派生索引可能与文件不一致；卷章顺序若有两个来源会漂移。

**方案**：

| 风险 | 对策 |
| --- | --- |
| 自动保存与 Agent 写同一章互相覆盖 | 正文**乐观锁**（`base_version` + 409/40901，`data` 回传最新内容与 `last_writer`）+ 前端 `ConflictBanner` 合并选择；Agent 写入前快照 `chapter_revisions(source="agent")`；`file.changed` 事件驱动前端提示「本章已被 AI 修改」 |
| 修订快照爆炸 | **自动保存不快照**（`is_autosave`）；只有手动保存/Agent 写章前/章节定稿才快照；`_prune_revisions` 保留最近 20 条 |
| Agent 改文件与派生索引不一致 | 文件为内容事实源；写入后**同步**更新派生索引，失败标 `project_index.dirty` 并告警；`writer rebuild-index` / `POST /novels/{id}/index/rebuild` 全量重建 |
| **卷章顺序双源漂移**（大纲小节先后 vs `sort_order`） | ★**顺序只由大纲派生**：唯一写入口 `sync_order_from_outline`；前端无拖拽/上移下移，后端无 `reorder`/`move`（并有 `assert_no_manual_reorder()` 守卫 + 测试断言路由不存在）；章不可跨卷移动；★回填**不产生快照、不递增 version**（顺序是元数据而非正文变更，避免与乐观锁/自动保存互相干扰） |
| 同一 run 被 resolve 与进行中的任务并发写同一 thread | `RunManager._spawn` 用 **per-run `asyncio.Lock`** 串行化 |
| 小说重命名与大纲文件名耦合（`{title}-大纲.md`） | 重命名是**事务**：同步重命名 outline 文件 + 更新 `novel.yaml.title/outline_file` + `novels.title/outline_file`（DB 值是文件值的缓存），任一步失败整体回滚；`outline_file` 为唯一寻址依据 |

### 7.7 ★难点七：第三方生态的版本兼容性

**问题**：deepagents / langchain / mcp / langfuse 都是快速演进的库，文档与实装常有差异，
且仓库里的 `demo/` 脚本用的是旧版 API。

**实测踩坑与对策**：

| 坑 | 对策 |
| --- | --- |
| **`langchain-mcp-adapters` 与 mcp 2.3.0 不兼容**（`ImportError: cannot import name 'RequestContext' from 'mcp.shared.context'`） | 回落后用 `mcp` 官方 client 自封装（`stdio_client` / `ClientSession` / `streamablehttp_client` + `AsyncExitStack`）→ `agent/mcp/adapter.py`，对上层接口不变 |
| **mcp 2.x 破坏性变更**：`mcp.server.fastmcp` 已移除，更名 `MCPServer` | 内置天气服务用 `mcp.server.mcpserver.MCPServer`（仍有 `.tool` 装饰器与 `run_stdio_async` 等） |
| **Langfuse v5 与仓库 demo 写法不兼容**（v5 无 `Langfuse.trace()` / `trace.span()`，`demo-langfuse.py` 是 v2 写法） | 按 v5 API 实现：`langfuse.langchain.CallbackHandler` + `start_as_current_observation` + `create_score`；★不照抄 demo；`current_trace_id()` 含 v5 API 差异容错（先试 `get_current_observation().trace_id`，回落 `get_current_trace().id`） |
| **deepagents 默认中间件栈不含 `TodoListMiddleware`**（`langchain.agents.factory` 未引用它） | 在 `middleware=` 中**显式加入**，否则拿不到 `write_todos` 工具与 `plan.update` 事件 |
| **`SubAgent.model` 传字符串会被按 `provider:model-name` 解析**，而本项目 `model_ref` 是 `{uuid}:{model_id}` 无法解析 | 必须传 **ChatModel 实例**（builder 异步预解析后经 `resolved_models` 传入） |
| **`mode="fork"` 下设 `skills` 会抛错** | 装配时检测并清空 + 告警；`services/subagents.validate_mode` 在 CRUD 层就拒绝 |
| **`interrupt_on` 只能做工具级开关**，无法按参数判定「整章覆盖」 | 自定义 `WriteApprovalMiddleware` 在 `awrap_tool_call` 内调 `langgraph.types.interrupt()` |
| **langchain HITL 是 batched resume 形状** | `resolve_interrupt` 归一化为 `{"decisions":[Decision]}`；★`edit` 决策的 `edited_action.name` **必须是 agent 已注册的真实工具名**（`_apply_edit` 会校验，传错抛 ValueError），故 `_interrupt_tool_name()` 从 `tool_name` 或 `payload.action_requests[0].name` 取真实名 |
| `permissions` 与沙箱 backend 不兼容会**直接抛 `ValueError`**（`FilesystemMiddleware.__init__` 校验） | 禁用 `LocalShellBackend`/`LangSmithSandbox`；只用 `FilesystemBackend` + `CompositeBackend` |
| 内置中间件构造参数跨版本可能变化 | 每个 `stack.append(...)` 都包 try/except 做**版本兼容兜底**（失败则降级为无参构造或跳过并告警） |
| `demo/` 脚本与实装版本漂移，误导后续开发 | 实现一律以实测结论为准，demo 仅作原理参考 |

### 7.8 ★难点八：Windows 平台的编码与路径

**问题**：项目在 Windows 上开发运行，路径分隔符、非法文件名字符、编码默认值都是坑。

**方案**：

- 路径解析**全部集中**在 `core/paths.py`，一律 `pathlib.Path`；
- 文件读写**显式 `encoding="utf-8"`**（`write_text()` / `read_text()` 统一入口），
  Jinja2 `FileSystemLoader` 也显式传 `encoding="utf-8"`；
- `safe_filename()` 替换 Windows 非法字符（反斜杠、斜杠、冒号、星号、问号、引号、尖括号、竖线）
  与控制字符为 `_`，并压缩连续空白；
- SQLite URL 需把 Windows 路径**转正斜杠**（`path.as_posix()`）；
- `WorkspaceGuard` 拒绝含 `..` 的路径、逐段检查符号链接解析后是否越界；
- ★虚拟路径 → 宿主路径转换时统一把反斜杠归一化为正斜杠；
- `main.py` 启动器区分 `os.name == "nt"` 处理子进程信号与命令构造。

### 7.9 ★难点九：长篇上下文治理与成本控制

**问题**：长篇小说逐章累积，若把历史章节全塞进上下文会爆窗口且成本失控。

**方案（多层组合）**：

1. **前情提要替代原文**：`build_recap` 只给近 6 章**摘要**（无摘要则正文节选 ≤600 字）+
   书名/题材/梗概 + **跨卷衔接锚点**；
2. **章节摘要自动生成**：`summarize_chapter` 在章节定稿或 Agent 写完后生成结构化摘要
   （`key_events` 3-6 条、`characters`、`foreshadowing`、`ending_state`）；
3. **token 预算检查**：`budget_report` = `max_context × 0.5` 阈值，超则发 `context.compact`；
4. **summarization 驱逐卸载**：deepagents 版把被驱逐消息卸载到 `/conversation_history/{session_id}.md`
   （权限规则第 6 条专门放行）、预截断过大的 `write_file/edit_file` 参数、遇 `ContextOverflowError` 自动重试；
5. **子代理上下文隔离**：`mode=isolated`（默认），子代理不继承主代理全部上下文；
6. **调用次数双限**：`ModelCallLimitMiddleware(run_limit=40, exit_behavior="end")` 软上限优雅收尾 +
   `config={"recursion_limit":60}` 硬上限；
7. **checkpoint 增量存储**：`DeltaChannel(snapshot_frequency=50)` → O(N)；
8. **用量可观测**：`usage` 事件 + `agent_runs.tokens_in/out` + Langfuse score（`run_tokens` /
   `project_words` / `fallback_used`）供复盘调优；
9. **裁剪优先级**：预算不足时按「当前章简介 + 卷节 > 最近 3 章摘要 > 附录补充设定 > 更早摘要」裁剪。

### 7.10 ★难点十：架构自洽（依赖方向与文件粒度）

**问题**：设计约定「Agent 逻辑不外溢到 api/services，非 Agent 业务代码也不进入 agent/」，
但同时又约定 `agent/llm/` 存放 ChatModel 工厂——而 `services/llm.py`（Provider CRUD 业务服务）
必须用该工厂做「连接测试」和「降级链解析」。**这条禁令在该处不可能被满足**，
导致实现里出现 9 处 `services → agent` 反向依赖，并用 172 处函数内延迟导入规避 import 环。

**已落地的整改**（在不把 Agent 相关文件迁出 `agent/` 的前提下）：

| 动作 | 结果 |
| --- | --- |
| 4 个纯数据访问函数（会话列表/工具日志/会话校验/项目字数）从 `loop/runner.py` 迁入 `services/agent.py` | 14 个 api 模块取数路径**全部归一**（都经 services）；`api/agent.py` 只保留 `run_manager` + `sse_bus` 两个**运行入口** |
| `BUILTIN_SKILLS_DIR`（纯路径常量，目录本体仍在 `agent/skills/`）下沉 `core/paths.py` | 消除 `services/skills.py` 的 3 处反向 import |
| `BUILTIN_SUBAGENTS`（Agent 运行语义数据）**留在** `agent/subagents/builtin.py`，新增 `core/catalog.py` **函数级惰性访问器** | 消除 `services/subagents.py` 的反向 import，且 core 在模块加载期不触碰 agent → import 图仍无环 |
| `loop/runner.py`（1420 行 / 7 类职责）拆为 6 个模块 | runner **1420 → 375 行**，只留 run 生命周期；门禁编排完整归入 `gates/`（消除「一个功能劈两半」） |
| `agent/` 各 `__init__.py` 实现外提到具名模块 | 12 个子包**风格统一**（`__init__` 一律纯 re-export），找代码时可预期实现落点 |
| `schemas/`（424 行）与 `db/models/`（335 行）按域拆 4 文件 | 可发现性提升；`__init__` re-export 使既有 `from app.schemas import X` 写法零改动 |
| `services → agent` import | **9 → 6 处**（余下为 LLM/MCP/可观测基础设施 + 2 处文档化能力型例外） |
| 函数内延迟导入 | **172 → 161 处**（大头需消环后才能继续上提到模块顶层） |

**明确保留的现状**：`agent/llm/factory.py`、`agent/mcp/adapter.py`、`agent/tracing/` 仍在 `agent/` 内。
彻底消环需把它们下沉到 `core/`，但那会把 Agent 基础设施迁出 `agent/`，
与「把 Agent 当成一个整体来使用」的项目定位冲突，故保留 `services → agent` 的这几处依赖并接受
函数内延迟导入作为代价。

**验证方式**（确保是纯重构、零行为变更）：用独立 git worktree 检出改动前版本，
把 SQLAlchemy metadata（16 表 × 列/类型/默认值/索引/约束/命名约定）与 OpenAPI（81 操作）
分别 dump 成 JSON **逐字比对**，确认完全一致；39 个 schema class 做逐类正文比对；
全量测试 399 passed / 1 skipped 与基线一致。

---

## 八、测试与质量保障

### 8.1 测试策略

**18 个测试文件 / 399 用例 / 1 skipped**，`pytest-asyncio` `asyncio_mode=auto`。

核心手法：

- **httpx `ASGITransport` 直连** FastAPI 应用（不起真实服务器、不占端口）；
- **假模型驱动**（`tests/fake_model.py`）：可编排的 ChatModel 替身，返回预设 content /
  tool_calls / usage_metadata，★**不触网、不打真实 LLM**（测试零成本、可重复、CI 友好）；
- `register_model_override()` / `clear_model_overrides()` 在工厂层注入替身；
- 每测试独立 `DATA_ROOT`（tmp 目录）+ `reset_engine()` + `reset_checkpointer()` +
  `get_settings.cache_clear()` 做隔离（★否则 checkpointer 会跨 DATA_ROOT 串库）；
- `conftest.py` 重定向 `env_file_path` 并还原 `os.environ`（★防测试污染真实 `.env`）。

### 8.2 覆盖域

| 测试文件 | 覆盖 |
| --- | --- |
| `test_permissions.py` | ★8 条权限规则**逐条越权** + **顺序回归**（断言 `PERMISSION_RULES` 常量数组与设计表逐行一致）+ `_TOOL_OPERATION` 映射 |
| `test_workspace_guard.py` | 路径穿越、符号链接越界、跨项目拒绝、`summaries/` 只允许 `summarize_chapter` 写（`allow_summaries=True`） |
| `test_outline_order.py` | 大纲严格解析（含 `## 第三章 卷土重来` 陷阱用例）、归一化匹配、`sync_order_from_outline` 顺序派生、★**断言 reorder/move 路由不存在** |
| `test_fts_search.py` | FTS5 二元切分、查询侧同函数、`project_id` 隔离、LIKE 降级 |
| `test_content_lock.py` | 乐观锁（`base_version` 不符 → 409/40901 + 回传最新内容）、修订快照触发策略、`finalize` 建快照、自动保存不快照 |
| `test_gates.py` | G0 两类缺失、G1 预检通过与强制跳过、G2 需 graph、G3 不匹配自动重生成 ≤2 次、`ConsultRequired` 信号、`GateState` 序列化往返 |
| `test_agent_e2e.py` | 端到端：发消息 → 202 → SSE 事件序列 → 消息落库 → run 收尾 |
| `test_agent_interrupts.py` | 中断四决策语义（approve/edit/reject/respond）、`edited_action.name` 校验、对已结束 run 的 resolve → 40902、resume 续跑 |
| `test_sse_bus.py` | `seq` 单调、环形缓冲淘汰、`Last-Event-ID` 续传、`41001`、`reopen` 无缝续跑 |
| `test_session_context.py` | pinned 章节注入、recap 含 pinned、id→name 解析（含 `builtin:` 前缀）、会话技能物化与重清、子代理过滤、上下文 API 往返与审计 |
| `test_subagents_skills.py` | 子代理 CRUD 与校验（name 规则、`fork`+skills 拒绝、内置不可删）、`reset_builtin`、技能物化与 `/resync` |
| `test_subagent_assembly.py` | `load_subagent_specs`（DB 空时回落内置定义）、`build_agent` 装配、模型覆盖解析 |
| `test_agent_units.py` | `WriteApproval.classify_risk` 三类高危、`WorkspaceGuardMiddleware` 错误指纹阻断、消息树 `add_message`/`load_tree`/`set_active_leaf`、`build_middleware_stack` 顺序、`SKILL_PATHS`、★**内置技能合规**（8 个技能 frontmatter `name`==目录名、description ≤1024、≤10MB；子代理引用的技能路径真实存在）、tracing 降级 |
| `test_mcp_widgets.py` | stdio 白名单 8 种拒绝形态、确认流转、命令全文落审计、probe、widgets 回落链、内置天气 MCP |
| `test_rewrite.py` | 四模式、参数校验（选区 ≤8000）、上下文注入、**不落盘**、审计、错误可读、★中文引号剥离（不对称对） |
| `test_novels_api.py` | 项目 CRUD、显式命名 42201、软删除入回收站、复制、重命名事务 |
| `test_volumes_chapters.py` | 卷章 CRUD、短 ID 恒定、无重排能力 |
| `test_export.py` | 4 种格式产物结构、EPUB 结构校验、calibre 缺失 → 50301 |
| `test_naming.py` | 显式命名各违规形态 |

### 8.3 静态质量

```powershell
uv run python -m pytest -q     # 399 passed / 1 skipped
uv run ruff check src tests    # ruff：line-length 110，select = E,F,W,I,UP,B,SIM
                               #   ignore = E501,B008,UP009（UP009 豁免：刻意保留 coding 声明）
cd src/web; npm run build      # vue-tsc --noEmit 类型检查 + vite 构建
```

### 8.4 「把约束编码成常量 + 配回归测试」

本项目最有价值的质量手法：设计约束不写在文档里靠人工自觉，而是**编码成常量并配回归测试**。

| 约束 | 落地方式 |
| --- | --- |
| 权限规则顺序即语义 | `PERMISSION_RULES` 常量数组 + 顺序回归测试（断言与设计表逐行一致） |
| 错误码规范 | `ErrorCode` 常量 + `CODE_TO_HTTP` / `CODE_TO_MESSAGE` **双映射表** |
| 路径与编码统一 | `core/paths.py` 集中解析，全部 `pathlib` + 显式 `encoding="utf-8"` |
| ID 稳定、顺序派生 | `sort_order` 唯一写入口 `sync_order_from_outline`，**无 reorder 接口**（测试断言路由不存在）+ `assert_no_manual_reorder()` 守卫函数 |
| 内置技能合规 | 测试遍历 8 个 `SKILL.md` 校验 frontmatter 约束 + 子代理引用的技能路径真实存在 |

---

## 九、已知边界与可演进方向

### 9.1 有意的取舍（非缺陷）

| 取舍 | 理由 |
| --- | --- |
| **单用户、无鉴权** | 定位为本地写作工具；审计日志只读、无后台管理 UI |
| **不用向量库 / Embedding** | FTS5 二元切分对小说检索足够；零 Embedding 费用、零额外服务、零冷启动 |
| **不自研 Agent 框架** | deepagents 已提供主循环/沙箱/权限/子代理/技能/压缩；自研只增加维护面 |
| **写作业务函数不作为 Agent 工具** | 收窄「permissions 不覆盖自定义工具」的越权面；Agent 只用文件工具碰内容 |
| **章不可跨卷移动、无手工重排** | 顺序单一事实源（大纲）优于双源；批量改 frontmatter 会放大写入并撞乐观锁 |
| **`services → agent` 保留 6 处依赖** | 彻底消环需把 LLM/MCP/可观测基础设施迁出 `agent/`，与「Agent 作为整体」定位冲突 |
| **敏感内容检查仅预留挂载位** | MVP 不实现（`RecapInjectionMiddleware._check_sensitive` 是空挂载点） |

### 9.2 外部依赖限制

- **mobi 导出**依赖本机安装 calibre（`ebook-convert`）；未装则该格式置灰并返回 `50301`
  （markdown / epub / txt 不受影响）；
- **浏览器定位**需 HTTPS 或 localhost，且用户可拒绝 → 回落 `user_settings.city`；
- **Langfuse** 三项配置全空则追踪全链路 no-op 降级为本地结构化日志；
- **FTS5** 不可用则检索永久降级 `LIKE`（返回结构不变，`data.degraded=true`）；
- **第三方 MCP** 仅支持开源免费、无需认证的服务（无认证头/Token 配置字段）。

### 9.3 可演进方向

| 方向 | 说明 |
| --- | --- |
| **基础设施下沉** | 把 `agent/llm/`（factory/params/budget）与 `agent/mcp/adapter` 传输层下沉到 `core/`，可消除剩余 3 处主要反向依赖，并使约 150 处函数内延迟导入大部分提升到模块顶层（IDE 跳转与静态分析随之改善）。**代价**：需同步修订目录边界约定（Agent 基础设施将不再全部位于 `agent/` 内） |
| **架构契约自动化** | 引入 import-linter，把「services 不得静态 import agent（白名单例外除外）」写成 CI 契约，防止架构再度漂移（当前靠文档约定 + code review） |
| **多用户 / 鉴权** | 现为单用户；若要多用户需引入认证层、把 `project_id` 隔离升级为 `user_id + project_id`、并重新审视 WorkspaceGuard 的白名单根 |
| **敏感内容检查** | `RecapInjectionMiddleware._check_sensitive` 挂载位已预留，可接入内容安全服务 |
| **检索增强** | 若二元切分召回不足，可加 `trigram` 作 ≥3 字查询的补充索引（★不能替代主方案），或引入向量检索作混合召回 |
| **门禁提示词调优** | G1/G3 判定质量取决于提示词；判定（理由+分数）已写 Langfuse observation/score，`gate_result_json` 留存，可基于真实数据回归调优 |
| **子代理并行** | 当前子代理由 deepagents 调度（`general-purpose` 自动追加）；若有明确的并行分工需求，可在 builder 层引入显式编排 |

---

> **文档定位说明**：本文是项目技术全景（架构与实现逻辑），不含待办与实施步骤。
> 启动方式与功能清单见同目录 [`README.md`](./README.md)。
> 全部技术结论均以 `.venv` 实测版本为准（见 6.1），与仓库 `demo/` 脚本的旧版 API 写法不一致时以本文为准。
