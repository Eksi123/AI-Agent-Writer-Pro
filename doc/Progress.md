# 开发进度记录

> 基准：`doc/Writer-Develop-Plan.md`「七、分阶段实施路线」（M0→M7 线性依赖链）。
> 最近更新：2026-10-27（M0~M7 全部完成）

## 总体进度

**100% —— M0~M7 全部里程碑已交付**

| 阶段 | 状态 | 完成度 |
| --- | --- | --- |
| **M0** 脚手架与地基 | ✅ 完成 | 100% |
| **M1** 小说基础功能 | ✅ 完成 | 100% |
| **M2** 审计与通用设置 | ✅ 完成 | 100% |
| **M3** Agent 核 v1 + 中断恢复 | ✅ 完成 | 100% |
| **M4** 大纲与门禁 | ✅ 完成 | 100% |
| **M5** 子代理 / 技能 / HITL 完善 | ✅ 完成 | 100% |
| **M6** MCP 与动态组件 | ✅ 完成 | 100% |
| **M7** 打磨与交付 | ✅ 完成 | 100% |

**最终验证基线**：`pytest -q` **399 passed / 1 skipped**；`ruff check src tests` 全过；
前端 `npm run build`（vue-tsc + vite）通过；`writer doctor` 自检正常；真实 uvicorn 冒烟测试
（health / novels / sessions / 202+run_id / SSE 补发 / 优雅降级 / subagents 5 内置播种 /
skills 8 内置 / mcp-servers / pet-state / SPA 回落）通过；`python main.py --backend-only`
一键启动器冒烟通过。

---

## 各里程碑交付清单

### M0 脚手架与地基（100%）

- `pyproject.toml`：`uv_build` + `module-root=src` + `module-name=app`，`writer = "app.cli:main"` 入口，「八、②」全部新增依赖
- 根 `.gitignore`、`.env.example`、`.vscode/settings.json`（固定解释器到 `.venv`）
- FastAPI 骨架 `main.py`：lifespan、CORS、统一响应包装、全局异常处理器、SPA 挂载与 history 回落
- `core/`：config（pydantic-settings + **进程级单调 config_version**）、errors（错误码全表）、response、logging（JSONL + 脱敏）、paths、security（`.env` 白名单原子写回 + 掩码；**测试隔离时重定向到 {DATA_ROOT}/.env**）、deps
- SQLite + Alembic 双路径幂等初始化、FTS5 两步探测、JSONL 三通道日志
- 前端 Vite 6 + TS 5.7 + Vue 3.5 + Pinia + Element Plus + Tiptap；vendor/ 已删
- deepagents 0.7.23 API spike（结论见手册「设计依据」表与本文末尾「实测结论」）
- CLI：`writer dev | migrate | rebuild-index | doctor`

### M1 小说基础功能（100%）

- 项目 CRUD（显式命名 42201、软删除入 trash、大纲模板初始化、复制）
- 卷/章 CRUD（短 ID 永不变、追加式顺序、**无 reorder/move 接口**）
- 正文乐观锁（base_version + 409/40901 回传最新内容）、chapter_revisions 快照与回滚
- 阅读模式（大纲节点、进度记忆、夜间）、编辑模式（Tiptap + 自动保存 + 字数 + 卷章树 + ConflictBanner + OutlineEditor）
- zip 导出（markdown/epub/mobi/txt + 大纲原件；EPUB3 自研生成器；calibre 探测置灰）
- WorkspaceGuard 规范 1~6 全实现

### M2 审计与通用设置（100%）

- 审计双写（SQLite + JSONL），埋点覆盖项目/卷章/大纲/导出/设置/LLM 配置/Agent 运行与中断/门禁/MCP
- `GET /audit-logs`（action/entity_type/from/to/page 过滤）
- 通用设置页全字段（主题/主色/字号/字体/页宽/自动保存间隔/回落城市/宠物阈值）

### M3 Agent 核 v1 + 中断恢复（100%）

- **数据表**：agent_sessions / messages（消息树）/ tool_call_logs / agent_runs（含门禁字段）/ agent_interrupts + 迁移 `22869e0bf363`
- **agent/llm**：多厂商 ChatModel 工厂（openai/openai_compatible/openrouter/azure/anthropic/google）、四层参数覆盖、model_ref 解析、`(model_ref, config_version)` LRU≤32 缓存、降级链、密钥掩码、测试注入缝 `register_model_override`、token 预算（tiktoken + 近似回落）
- **agent/sandbox**：8 条有序 permissions 常量（顺序回归测试）+ CompositeBackend 三路由
- **agent/loop**：`AsyncSqliteSaver` checkpointer（按路径缓存，DATA_ROOT 变化自动重开）；builder（create_deep_agent 装配：中间件顺序 Retry→Fallback→CallLimit→Todo→Summarization→Recap→Guard→Notify→Approval + skills + subagents + interrupt_on + context_schema）；runner（astream→SSE 事件映射、消息树按索引基线同步、协作式取消、运行收尾汇总 + Langfuse score）；SseBus（环形缓冲 500、seq 单调、Last-Event-ID/query 双续传、41001、reopen 无缝续跑）
- **4 个自定义中间件**：RecapInjection（abefore_model 注入 + 预算告警 + 敏感检查挂载位）、WorkspaceGuard（路径校验第二道 + 错误指纹 2 告警/3 阻断 + tool_call_logs）、FileChangeNotify（file.changed + 派生索引同步 + 大纲变更触发 sync_order_from_outline）、WriteApproval（**参数级**高危判定：整章覆盖/大范围 edit/delete → `interrupt()`，四决策恢复语义）
- **agent/tools**：`web_search.py`（工具实现：Wikipedia + DuckDuckGo lite 免 Key 双源，失败明确报错不静默）+ `registry.py`（装配清单 `build_tools()`，与手册「tools/ = 工具 + registry」结构对齐）
- **agent/memory**：消息树（parent_id + branch_index + active_leaf）、活跃路径裁剪、切分支
- **agent/tracing**：Langfuse v5（CallbackHandler + observation + create_score，未配置自动降级）
- **API**：`api/llm.py`（providers CRUD + test + models CRUD + fallback_chain + /config/reload）、`api/agent.py`（sessions / messages 202 / branch / context / runs / **SSE stream** / cancel / **interrupts resolve**）
- **前端**：AiToolsView（Provider/Model 管理 + 密钥掩码交互 + 连接测试 + 降级链 + 配置重载）、AgentPanel 实装（会话切换、流式消息、工具卡片、子代理泳道、PlanTodoList、ApprovalCard 四决策、ConsultCard、模型切换、停止/重新生成/复制）、agent store（SSE 订阅 + file.changed 热更新接线到 WorkspaceView）
- **测试**：假模型基础设施 `tests/fake_model.py`（ScriptedFakeChatModel，**bind_tools 返回 self 保 cursor 单调**——langchain 每轮 bind_tools，clone 会重置游标）；端到端 18 + 中断/取消/分支 11 + 单元 30 + SSE 9 + 权限 20

### M4 大纲与门禁（100%）

- **agent/gates**：四阶段编排 `advance_gate`（服务层硬编排，状态机 GateState 序列化在 `agent_runs.gate_result_json`，中断可续跑）
  - G0 存在性：两类缺失（无小节/简介空）不同咨询文案，**不自动插入占位小节**；「让 Agent 代写大纲」走 planner 前置 agent 流后自动重查
  - G1 衔接预检：独立 LLM JSON 校验，**带上一卷末章锚点**（第一卷用附录设定）；force_skip 落审计
  - G2 生成：writer 子代理（生成指令含简介+衔接+前情提要+反馈）；**生成前快照旧正文 source=agent（幂等：同 run 同 version 只快照一次）**
  - G3 匹配后检：独立 LLM 校验；不匹配自动重生成 ≤2 次（每次带反馈）；用尽后咨询（认可现状/手动修改/补充指示再生成）
- **agent/knowledge**：`build_recap`（近 6 章摘要 + 跨卷锚点）、`summarize_chapter`（LLM 摘要写 summaries/，**仅此函数可写**，成功后同步派生索引）
- **咨询机制**：`ConsultRequired` 信号 → runner 落 `agent_interrupts(type=consult)` + `consult.request` 事件 + `gate.update` 事件；resolve（option/text）→ 状态机按 pending_consult 路由续跑
- **前端**：GateStatusBar（G0→G3 流转 + 重试计数 + G1 跳过标记）、AgentPanel「生成本章」入口（`target_chapter_id` 触发门禁 run）、ConsultCard
- **测试**：13 用例（G0 两类咨询/G1 锚点与跳过/G3 重试与咨询/accept/状态序列化/目标解析）

### M5 子代理 / 技能 / HITL 完善（100%）

- **services/subagents.py**：内置 5 子代理启动播种（is_builtin 幂等）、CRUD、fork+skills 组合校验、内置保护（不可删/不可改名、可停用/改提示词/恢复出厂）
- **services/skills.py**：skill_configs CRUD、**物化 data/skills/{name}/SKILL.md**（frontmatter name==目录名、description≤1024、10MB 上限）、改名迁移目录、停用移除物化、`/skills/resync` 一致性修复、与内置技能重名拒绝
- **builder 改 DB 驱动装配**：`load_subagent_specs`（DB 空回落内置定义）+ model_ref 预解析为 ChatModel 实例（deepagents 无法解析 uuid:model_id 字符串）
- **HITL 完善**：审批四决策（批准/编辑后批准/拒绝/回复补充）M3 已交付；子代理泳道（subagent.start/end 按 namespace 聚合）；门禁 Langfuse score（gate_g1_passed / gate_g3_matched / chapter_words）
- **前端**：SubagentManager.vue（卡片式管理 + 模型覆盖下拉 + 恢复出厂）、SkillManager.vue（编辑器 + SKILL.md 预览 + 物化状态标签 + resync）
- **测试**：管理 API 18 + 装配集成 4（含开启子代理的完整运行）

### M6 MCP 与动态组件（100%）

- **services/weather.py**：Open-Meteo geocode/current/forecast（WMO 代码→中文+emoji）、BigDataCloud 反向地理编码（失败回落 settings.city）、10 分钟内存缓存、失败一律 503/50301 可读错误
- **agent/mcp/server.py**：内置天气 MCP（mcp 2.3.0 `MCPServer` + `.tool`；`python -m app.agent.mcp.server` 可 stdio 独立运行）+ LangChain tool 双形态（进程内直连 services.weather，单一实现）
- **agent/mcp/adapter.py**：第三方 MCP 自封装（stdio_client/streamablehttp/sse + ClientSession → StructuredTool，工具名 `mcp_{server}_{tool}` 安全化）——**因 `langchain-mcp-adapters` 与 mcp 2.3.0 不兼容（ImportError RequestContext），按手册回落路径实现**
- **services/mcp.py + api/widgets.py**：MCP CRUD、**stdio 白名单（npx/uvx/python，拒绝路径形式）**、新建默认停用、探测缓存工具列表、**二次显式确认（confirmed_at）+ 命令全文落审计**、改命令重置确认；`GET /widgets/weather|city|pet-state`（宠物两态：project_index.last_updated 距今 ≤ pet_idle_minutes → writing）
- **builder 接线**：tools = web_search + 天气双工具 + 启用的第三方 MCP 工具（单个失败跳过不阻断）
- **前端**：WeatherWidget.vue（左上角：SVG 宠物两态动画——敲键盘/打瞌睡 zzz、浏览器定位→回落城市、天气 5 分钟刷新、宠物 1 分钟轮询）、McpManager.vue（内置卡片 + 白名单下拉 + 二次确认弹窗展示完整命令 + 探测 + 启停）
- **测试**：43 用例（天气 mock 全链路/白名单 8 种拒绝形态/确认流转/审计命令全文/widgets 回落链）

### M7 打磨与交付（100%）

- **Dockerfile**（多阶段：node:24 构建前端 → python:3.12-slim + uv 运行，healthcheck /api/v1/health）+ **docker-compose.yml**（writer-data 卷、env_file .env 可选、回环绑定）
- **README.md 重写**：消除「用户登陆/前后台」旧表述（单用户本地应用、无鉴权、审计只读 API），补功能总览/技术栈/快速开始/CLI/目录结构/数据目录/`.env` 键清单/测试口径/设计铁律摘要
- **doc/Acceptance-Checklist.md**：七大链路端到端验收清单（写作业务/LLM 配置/Agent 会话/门禁生成/子代理技能/MCP 组件/部署恢复）+ 必测清单→测试文件映射表
- **doc/Writer-Develop.md 精简**：标注以手册为准 + 废弃「登陆/前后台」表述 + 实现状态索引
- **错误边界与空态**：后端统一异常处理（ApiError 错误码在 SSE error 事件中保留，如 LlmProviderError→50201）；前端空态（无项目/无会话/无消息/未配置模型引导）
- **修复的运行期缺陷**：FileChangeNotify 未 await refresh_project_index、config_version 重载归零（改进程级单调计数器）、checkpointer 跨 DATA_ROOT 串库（按路径缓存）、测试污染真实 `.env`（env_file_path 重定向 + conftest 还原 os.environ）
- **本地调试启动器 `main.py`**（仓库根）：`python main.py` 一键拉起后端(8000)+前端 Vite(5173)，Ctrl+C 同停两端；`--prod` 单体、`--backend-only`/`--frontend-only`、`--port` 自定义；uv 缺失时回落 PYTHONPATH=src；自动 npm install / 前端构建；端口就绪探测
- **统一文件头**：src/app + tests 全部 .py 加装标准头（模块/文件名/作者/时间/说明五字段，说明=原 docstring 无损保留，时间=git 首次入库日期）；跳过 demo/ 与 Alembic 自动生成迁移；`ruff` 豁免 UP009（刻意保留 coding 声明）
- **死代码清理 + API 模块去歧义**：删 `prompts/versions.py`（零引用）、`render_string`、`security.langfuse_enabled()`、`runner.load_message_tree`、`services/llm.resolve_ref`；`api/config.py`（与 core/config.py 重名）按手册拆为 `api/subagents.py`+`api/skills.py`+`api/mcp.py`，`api/widgets.py` 收窄为纯动态组件；`POST /summaries/rebuild` 接线 LLM 摘要生成（原为占位）

## 补充交付（第二轮需求排查）

- **选区 AI 改写**（手册「三、(3)【正文】」选中浮层，原为禁用占位）：
  - 后端 `agent/llm/rewrite.py`（服务层直连 LLM，同门禁 G1/G3，不进 Agent 循环）+ `prompts/rewrite_selection.md.j2`（四模式分述要求）+ `POST /chapters/{cid}/rewrite`（只读不落盘、审计 `chapter.rewrite`、选区 ≤8000 字、模型失败给可读错误）
  - 前端 `RewriteDiffDialog.vue`（句级 LCS diff 高亮）、`aiSourceMark.ts`（Tiptap Mark 扩展：AI 来源标记 + 点击回滚原文）、TiptapEditor 四按钮启用（改写/扩写/缩写/润色）+ 选区前后文注入
  - 修复 `_strip_wrappers` 中文引号剥离 bug（`“ ”` 不对称对，原用首尾相等判断失效）
- **导出会话**（手册「三、(3) 右侧栏」：打断/重新生成/复制/导出会话）：AgentPanel 会话菜单「导出会话」→ 消息树导出为 Markdown（含工具调用）
- **测试**：`tests/test_rewrite.py` 14 用例（四模式/参数校验/上下文注入/不落盘/审计/错误可读/引号剥离）

## 补充交付（第三轮需求排查：上下文 Chips 消费链路）

排查发现 `agent_sessions` 的 `context_chapter_ids / subagent_ids / skill_ids` 三个字段**已入库、有 PATCH API，但从未被 Agent 装配消费**（手册「三、(3) 输入窗口」要求可主动添加）。本轮补齐端到端链路：

- **后端消费**：
  - `agent/knowledge::build_pinned_chapters`（选中的「已读章节」→ 标题+摘要/节选上下文段）；`_make_recap_provider` 把 pinned 章节并入前情提要注入
  - `runner::_session_context_of`（会话存的 id → 解析为子代理 name / 技能 name，`builtin:` 前缀支持）
  - `builder::build_agent` 新增 `session_id / subagent_names / skill_names` 参数：子代理按选择过滤（`build_subagents_from_specs`）；技能精选经 `materialize_session_skills` 物化到 `data/session-skills/{sid}/`，backend 挂载 `/skills/session-{sid}/` 路由（`build_backend(session_id)`）
  - `paths::session_skills_root/session_skills_dir`；`list_sessions` DTO 复用 `_session_dto`（列表与单会话同构，前端 Chips 回填依赖）
- **前端**：AgentPanel 输入区上方「＋ 上下文」Chips 选择器（子代理/技能/已读章节三类，含「＋当前章」快捷），增删即 `PATCH /agent/sessions/{sid}/context` 持久化，会话切换时回填
- **测试**：`tests/test_session_context.py` 11 用例（pinned 章节注入/recap 含 pinned/id→name 解析/会话技能物化与重清/子代理过滤/上下文 API 往返与审计）

**最终验证基线（含第二、三轮）**：`pytest -q` **399 passed / 1 skipped**；`ruff check src tests` 与 `ruff check main.py` 全过；前端 `npm run build`（vue-tsc + vite）通过；`main.py --backend-only` 真实启动冒烟通过（health OK）。

---

## deepagents 0.7.23 / langchain 1.4.7 实测结论（spike 记录）

| 项 | 实测结果 |
| --- | --- |
| `create_deep_agent` | 参数与手册一致（model/tools/system_prompt/middleware/subagents/skills/permissions/backend/interrupt_on/context_schema/checkpointer/…） |
| `SubAgent` | TypedDict：name/description/tools/model/middleware/interrupt_on/skills/permissions/response_format/system_prompt/mode(isolated\|fork) |
| `FilesystemPermission` | `(operations: list['read'\|'write'], paths: list[str], mode: 'allow'\|'deny'\|'interrupt')`；首条匹配生效、无匹配默认 allow |
| 文件系统工具参数名 | ls: `path`；read_file/write_file/edit_file/delete: `file_path`；glob/grep: `pattern` |
| HITL 中断形状 | langchain `HumanInTheLoopMiddleware` 默认 **batched**：`interrupt(HITLRequest{action_requests, review_configs})`，resume 值 `{"decisions":[Decision]}`；Decision：approve / edit（**edited_action={name:真实工具名, args}**，name 传错抛 ValueError）/ reject(message) / respond(message) |
| resume 机制 | `graph.astream(Command(resume=...), config)` 续跑同一 thread；**错误形状会导致工具调用悬空被 PatchToolCallsMiddleware 修复** |
| `AgentMiddleware` 钩子 | before/after_agent、before/after_model、wrap_model_call、wrap_tool_call（各带 a* 版）；无 before_tool/on_run_end（收尾在 runner 做） |
| `ToolCallRequest` | tool_call/tool/state/runtime；改参数用 `.override(...)`；`ToolRuntime` 含 context/stream_writer |
| 上下文压缩 | `create_summarization_middleware` 在 **`deepagents.middleware.summarization`**（非顶层导出）；langchain `SummarizationMiddleware(trigger=("tokens", N))` 可作回落 |
| `AsyncSqliteSaver` | `(conn: aiosqlite.Connection)` + `await setup()`（自动建表）；连接须长生命周期持有 |
| MCP | `mcp.server.mcpserver.MCPServer` 可用（fastmcp 已移除）；**`langchain-mcp-adapters` 与 mcp 2.3.0 不兼容**（ImportError: RequestContext），已按手册回落自封装 |
| 假模型 | langchain 自带 Fake* 的 `bind_tools` 抛 NotImplementedError，自建 `ScriptedFakeChatModel`；**bind_tools 必须返回 self**（langchain 每轮调用 bind_tools，model_copy 会重置 cursor 导致脚本重放） |

## 关键设计约束（续作时务必遵守）

1. **铁律 9**：permissions 首条匹配生效、兜底 `deny /**` 必须最后；新增规则插在兜底前且必须补越权测试（`tests/test_permissions.py` 顺序回归）
2. **铁律 10**：ID 稳定、顺序只写 `sort_order`（唯一入口 `sync_order_from_outline`）
3. **铁律 3**：文件为内容事实源；不一致以文件为准（`writer rebuild-index`）
4. **密钥只进 `.env`**（白名单 + 原子写 + .bak）；DB 只存 `api_key_env`；接口/日志一律掩码
5. **目录边界**：Agent 逻辑全在 `src/app/agent/`，不外溢到 api/services
6. 运行期上下文经 `context_schema` + `Runtime.context` 传递（AgentContext），禁全局变量
7. 派生索引同步失败不阻断写入，标 `project_index.dirty`
8. 门禁咨询与工具审批共用 `interrupts/{id}/resolve`，决策形状不同（consult: option/text；approval: approve/edit/reject/respond）
9. SSE `seq` 每 run 单调递增且 **reopen 不清零**（中断恢复无缝续传）；缓冲丢失 41001 → 前端回落重拉消息树
10. 测试隔离：DATA_ROOT 覆盖时 `.env` 重定向到 `{DATA_ROOT}/.env`；conftest 还原 os.environ 并重置 checkpointer/SseBus/模型缓存

## 运行手册（与手册「十二」一致）

```powershell
uv sync                                  # 安装依赖
uv run writer migrate                    # alembic upgrade head
uv run writer dev                        # uvicorn 127.0.0.1:8000
cd src/web; npm install; npm run dev     # 前端开发服务器
docker compose up -d                     # 或容器单体部署
```
