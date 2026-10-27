# AI Writer Pro

大纲驱动的 AI 小说写作工作台 —— **单用户本地应用**（无登录鉴权、无后台管理端）。

采用 [deepagents](https://github.com/langchain-ai/deepagents) + LangGraph + Langfuse + SQLite 搭建：
本质上是一个小说编辑器，支持**手动撰写**与**AI 生成**两种模式。AI 生成正文以大纲章简介为**必要前提**
（四阶段门禁 G0→G3 硬编排），内容以 Markdown 文件为事实源，SQLite 存登记与派生索引。

> 完整设计蓝图见 [`doc/Writer-Develop-Plan.md`](doc/Writer-Develop-Plan.md)（权威手册）；
> 开发进度见 [`doc/Progress.md`](doc/Progress.md)。

---

## 功能总览

| 域 | 能力 |
| --- | --- |
| 项目管理 | 小说 CRUD（显式命名）、软删除入回收站、复制、zip 导出（markdown/epub/mobi/txt + 大纲原件） |
| 卷章 | 卷/章 CRUD（短 ID 永不变）、**无重排接口**（顺序由大纲小节先后派生）、正文乐观锁（`base_version` + 409）、修订快照与回滚 |
| 大纲 | 单文件大纲（`# 卷 / ## 章 / - 简介 / - 衔接要点` + 附录）、严格层级解析、归一化匹配、`sync_order_from_outline` 顺序唯一写入口、解析校验 |
| 检索 | FTS5 派生索引（中文二元切分 + unicode61），FTS5 不可用自动降级 LIKE |
| 阅读 | 目录（顶端「大纲」节点）、翻页、进度记忆、夜间模式 |
| 编辑 | Tiptap 富文本、自动保存、字数统计、卷章树（只读顺序）、版本冲突横幅、**选区 AI 改写**（改写/扩写/缩写/润色 → diff 预览 → 接受后打 AI 来源标记可回滚） |
| AI Agent | deepagents 主循环 + 5 个写作子代理（planner/writer/editor/continuity-checker/researcher）+ 8 个内置技能 + 用户自建技能 |
| 门禁生成 | **G0** 大纲存在性 → **G1** 衔接预检 → **G2** 生成（写前快照）→ **G3** 匹配后检（不匹配自动重生成 ≤2 次）；每阶段可下发咨询卡片 |
| HITL | 工具级审批（delete 无条件）+ 参数级审批（整章覆盖 / 大范围 edit），支持 批准 / 编辑后批准 / 拒绝 / 回复补充 |
| 会话 | 按项目隔离的多会话、消息树分支（重新生成 / 编辑重发）、SSE 流式（环形缓冲 + `Last-Event-ID` 续传）、协作式取消、**导出会话为 Markdown**、**上下文 Chips**（主动添加子代理 / 技能 / 已读章节，会话级持久化并真实注入装配） |
| LLM | 多厂商（OpenAI / OpenAI 兼容 / Anthropic / Google / Azure / OpenRouter）、四层参数覆盖、连接测试、降级链；**密钥只存 `.env`，DB 仅记变量名** |
| MCP | 内置 Open-Meteo 天气 MCP；第三方 MCP（stdio 命令白名单 + 二次确认 + 审计 / http / sse） |
| 可观测 | Langfuse v5 全链路追踪（未配置 Key 自动降级本地日志）、审计日志（SQLite + JSONL 双写）+ 只读查询 API |
| 动态组件 | 左上角宠物两态动画（写作中敲键盘 / 久无进展打瞌睡）+ 浏览器定位城市 + 当前天气 |

## 技术栈

- **后端**：Python 3.12 + FastAPI + SQLAlchemy 2.0 (async) + aiosqlite + Alembic + deepagents 0.7.23 / langchain 1.x / langgraph
- **前端**：Vue 3 + Vite + TypeScript + Pinia + Element Plus + Tiptap
- **数据**：SQLite（`data/app.db` 业务库、`data/agent_state.db` LangGraph checkpoint）+ Markdown 文件（内容事实源）
- **依赖管理**：`uv`（`pyproject.toml` + `uv.lock` 是唯一事实源）

## 快速开始（Windows PowerShell）

### 一键启动（推荐，调试用）

```powershell
python main.py                 # 同时拉起后端(8000) + 前端 Vite(5173)，Ctrl+C 一起停
python main.py --prod          # 单体模式：构建前端后只开后端 8000
python main.py --backend-only  # 只启动后端
python main.py --frontend-only # 只启动前端（需后端已在 8000）
python main.py --port 9000     # 自定义后端端口（前端代理自动跟随）
```

打开 `http://localhost:5173`（开发模式）或 `http://127.0.0.1:8000`（单体模式）。

### 手动分步启动

```powershell
# 1. 安装依赖
uv sync

# 2. 数据库迁移
uv run writer migrate

# 3. 启动后端（默认 http://127.0.0.1:8000）
uv run writer dev

# 4. 另开终端，启动前端开发服务器
cd src/web
npm install
npm run dev
```

打开 `http://localhost:5173`（Vite 默认端口，`/api` 已代理到 8000）。

**首次使用**：左下角「AI 工具」→「大模型 API」添加 Provider 与模型（密钥会写入 `.env`），
之后即可在右侧 AI 助手面板对话，或用「生成本章」触发门禁生成。

### 单体部署（生产）

```powershell
cd src/web; npm ci; npm run build   # 产出 dist/，FastAPI 自动挂载（SPA history 回落）
uv run writer dev --no-reload       # 访问 http://127.0.0.1:8000
```

### Docker

```powershell
docker compose up -d          # 构建 + 启动（数据持久化在 writer-data 卷）
docker compose logs -f app    # 看日志
```

## CLI

```powershell
uv run writer dev             # 启动开发服务器（uvicorn --reload）
uv run writer migrate         # alembic upgrade head
uv run writer rebuild-index   # 全量重建 story_fts / project_index（DB 与文件不一致时）
uv run writer doctor          # 环境自检（FTS5 / calibre / 依赖版本 / Langfuse）
```

## 目录结构

```
src/
├── app/                    # 后端（FastAPI）
│   ├── api/                # 路由层（novels/volumes/chapters/outline/story/settings/audit/llm/agent/subagents/skills/mcp/widgets）
│   ├── core/               # config / errors / response / logging / paths / security / deps
│   ├── db/                 # base / session / bootstrap / fts / models / migrations
│   ├── schemas/            # Pydantic DTO
│   ├── services/           # 业务编排（novels/volumes/story/search/outline/exporters/audit/files/naming/workspace/llm/agent/skills/subagents/mcp/weather）
│   └── agent/              # ★AI Agent 全部逻辑
│       ├── llm/            #   factory（多厂商 ChatModel）/ params（四层覆盖）/ budget（token 预算）
│       ├── loop/           #   builder（create_deep_agent 装配）/ runner（astream→SSE）/ events（环形缓冲）
│       │                   #   / checkpointer / context / emitter / middleware（Recap·WorkspaceGuard·FileChangeNotify·WriteApproval）
│       ├── gates/          #   四阶段门禁 G0-G3 + 咨询卡片
│       ├── sandbox/        #   CompositeBackend 路由 + 8 条有序 permissions
│       ├── tools/          #   web_search 实现 + registry 装配（Wikipedia + DuckDuckGo 免 Key 双源）
│       ├── subagents/      #   5 个内置写作子代理
│       ├── skills/         #   8 个内置技能（SKILL.md）
│       ├── mcp/            #   内置天气 MCP + 第三方 MCP 封装
│       ├── memory/         #   消息树（parent_id + branch_index + active_leaf）
│       ├── knowledge/      #   build_recap / summarize_chapter
│       ├── llm/            #   多厂商 ChatModel 工厂 / 四层参数 / token 预算 / 选区改写
│       ├── prompts/        #   Jinja2 模板（主代理 / 门禁预检后检 / 生成指令 / 选区改写）
│       └── tracing/        #   Langfuse v5 集成
└── web/                    # 前端（Vue 3 + Vite + TS）
    └── src/{api,components,stores,views,router,i18n,types,styles}
tests/                      # pytest（httpx ASGITransport 直连 + 假模型驱动，不触网/不打真实 LLM）
doc/                        # 设计手册 + 开发进度 + 验收清单
data/                       # 运行期数据（不入版本库）
```

## 数据目录（`data/`，备份 = 停服后整目录复制）

```
data/
├── app.db                  # 业务库（登记 / 快照 / 会话 / 消息 / 运行 / 中断 / 审计 / 派生索引）
├── agent_state.db          # LangGraph checkpoint（Agent 恢复与 HITL 续跑）
├── workspaces/{project_id}/# ★小说内容（最重要）：novel.yaml / volumes.yaml / chapters/ / outline/ / summaries/
├── skills/{name}/SKILL.md  # 用户自建技能物化目录
├── exports/                # 导出 zip 产物
├── trash/                  # 软删除项目回收站
└── logs/                   # JSONL 运行日志（app / error / audit）
```

## `.env` 键清单（见 `.env.example`）

```
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

## 测试与质量

```powershell
uv run python -m pytest -q          # 全量测试（300+ 用例，假模型驱动）
uv run ruff check src tests         # lint
cd src/web; npm run build           # vue-tsc 类型检查 + vite 构建
```

测试覆盖：权限矩阵（8 条规则逐条越权 + 顺序回归）、WorkspaceGuard 路径穿越、大纲严格解析与顺序派生、
FTS5 二元切分与降级、乐观锁与修订快照、显式命名、EPUB 结构、SSE seq/续传/41001、
中断四决策语义、消息树分支、门禁 G0/G1/G3 与自动重生成、子代理装配、技能物化、MCP 白名单与二次确认、
天气服务（mock HTTP）。详见 [`doc/Acceptance-Checklist.md`](doc/Acceptance-Checklist.md)。

## 设计铁律（摘要，全文见手册「一」）

1. 不自研 Agent 框架，只做写作领域化封装；
2. C/S 分离（REST 数据 + SSE 流式 + REST 控制）；
3. **文件为内容事实源，DB 为登记与索引**，不一致时以文件为准（可 `rebuild-index` 重建）；
4. 单用户 + 项目级隔离（Agent 跨项目可读不可写）；
5. Langfuse 可观测，未配置自动降级；
6. **显式命名**（禁止「未命名小说」「第 1 卷」等默认名，违规 42201）；
7. UI 默认中文，文案集中管理；
8. M0→M7 分阶段交付；
9. permissions 首条匹配生效、兜底 `deny /**` 必须最后；
10. **ID 稳定、顺序由大纲派生**（`sort_order` 唯一写入口 `sync_order_from_outline`，无 reorder/move 接口）；
11. 时间戳 UTC ISO-8601 应用层写入，文件读写一律 `pathlib` + `encoding="utf-8"`。
