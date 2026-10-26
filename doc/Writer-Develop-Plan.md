# AI Writer Pro —— 整体功能设计蓝图

---

## 一、总体设计原则

1. **不自研 Agent 框架**：`deepagents` 已提供 Agent-Loop、子代理、技能、虚拟文件系统、上下文压缩、HITL、checkpointer 持久化与流式；本项目只做「写作领域化」封装与业务编排。
2. **C/S 分离**：前端 `src/web`（Vue SPA），后端 `src/app`（FastAPI）。REST 负责数据操作，SSE 负责 Agent 流式输出，控制类操作（打断 / 审批）走 REST。
3. **文件为内容事实源，DB 为登记与索引**：正文、大纲、章节摘要以 Markdown 文件为唯一**内容**事实源；SQLite 存 Agent 消息/会话/运行/中断、项目登记（`novels`）、修订快照（`chapter_revisions`）、阅读进度、审计与派生索引（`story_fts` / `project_index`）；运行日志用 JSONL。**不一致时一律以文件为准**，登记表与派生索引可从工作区全量重建（`uv run writer rebuild-index`）。
4. **单用户 + 项目级隔离**：无登录鉴权，单用户本地使用；Agent 对项目文件「半隔离」（跨小说项目可读、不可写，项目外完全隔离）。
5. **可观测**：Langfuse 全链路追踪（未配置 Key 时自动降级为本地结构化日志，不影响功能）。
6. **显式命名**：创建小说、卷、章**必须由用户显式命名**（AI 生成时要求大纲章简介中有卷章名），不允许「未命名小说」「第 1 卷」等默认名或纯序号名；前端必填校验 + API 同步拒绝（错误码 `42201`）。
7. **UI 默认中文**：文案集中管理，便于后续双语扩展。
8. **分阶段 MVP 交付**：按 M0 → M7 里程碑推进（见「七」）。
9. **权限规则「默认放行 ⇒ 必须显式兜底 deny」**：`deepagents` 的 `FilesystemPermission` 判定是**首条匹配生效、无规则匹配时默认 `allow`**（实测 `_check_fs_permission`）。权限清单顺序即语义，兜底 `deny /**` **必须是最后一条**；新增规则只能插在兜底之前，且必须补越权测试。
10. **ID 稳定、顺序由大纲派生**：小说 / 卷 / 章的 ID 为应用层生成的 TEXT(uuid)（卷/章取前 8 位短 ID），**顺序调整只写 `sort_order`，绝不改 ID 或文件名**（章节路径 `chapters/v{vol_id}/c{chapter_id}.md` 含 ID），以免修订快照、摘要、阅读进度等引用失效。`sort_order` 不是用户可编辑字段，而是**大纲小节先后的派生值**，唯一写入口为 `sync_order_from_outline`（见「四」）；因此前端不提供拖拽/上移下移，后端不提供 `reorder` 接口，章不可跨卷移动（跨卷等于改路径）。
11. **时间与编码统一**：时间戳一律 UTC ISO-8601 由应用层写入（不用 SQLite 触发器/`CURRENT_TIMESTAMP`）；文件读写一律 `pathlib` + 显式 `encoding="utf-8"`。

### 术语与约定（全文统一）

| 术语 | 含义 |
| --- | --- |
| 小说项目 / novel / project | 同一概念。`novels.id`（uuid）即 `project_id`；工作区目录 `data/workspaces/{project_id}/` |
| 虚拟路径 | Agent 视角的后端路径，以 `/` 开头，如 `/projects/{pid}/chapters/...`、`/skills/user/...`；由 `CompositeBackend` 路由映射到宿主机真实目录，**Agent 不可见宿主路径** |
| 宿主路径 | 服务层视角的真实磁盘路径，如 `data/workspaces/{pid}/chapters/...`；仅服务层与 `WorkspaceGuard` 使用 |
| session（会话） | 一次持续的对话上下文，`agent_sessions.id`；按项目隔离，一个项目可有多个会话 |
| run（运行） | 一次「用户发消息 → Agent 跑完」的执行，`agent_runs.id`；对应 LangGraph `thread_id = novel:{project_id}:session:{session_id}` 上的一次调用 |
| gate（门禁） | 大纲驱动生成的四阶段编排 G0/G1/G2/G3，见「二、(6)」 |
| `model_ref` | 模型引用，统一格式 `"{llm_providers.id}:{llm_models.model_id}"`（与 deepagents `SubAgent.model` 的 `provider:model-name` 约定一致） |
| 咨询（consult） | `type=consult` 的中断，下发「选项按钮 + 自由输入框」结构化卡片，见「二、(2)」 |

## 二、怎么设计 AI Agent

### 设计依据：deepagents 能力与本项目诉求的映射

> 下表已在 `.venv` 实测核对（`deepagents 0.7.23` / `langchain 1.4.7` / `langchain-core 1.7.0` / `langgraph 1.2.16` / `mcp 2.3.0` / `langfuse 5.0.4`），全文涉及版本行为的结论一律以此表为准。

| 手册诉求 | deepagents 0.7.23 实测能力 |
| --- | --- |
| Agent-Loop | `create_deep_agent(...)` 返回 `CompiledStateGraph`（基于 LangGraph），支持 `astream` / `astream(subgraphs=True)` 取子代理事件 |
| Tools | `tools=` 接受 `BaseTool` / 普通 callable / `dict`；**MCP 工具不在 deepagents 内建**，需经 `langchain-mcp-adapters` 转成 LangChain tool 再传入（该包当前未安装，见「八」） |
| Skill | `skills=["/skills/builtin/", "/skills/user/"]`，参数是 **backend 虚拟路径**而非磁盘路径；技能靠模型 `read_file` 读 `SKILL.md` 实现渐进式披露；`name` 必须等于父目录名（小写字母数字连字符，≤64 字符），`description` ≤1024 字符 |
| Sub-agent | `subagents=[SubAgent]`，字段实测为 `name / description / system_prompt / tools / model / middleware / interrupt_on / skills / permissions / response_format / mode`；`mode` 仅 `isolated`（默认）/ `fork`，**`fork` 下设 `skills` 会抛错**、且 `system_prompt` 是**追加**到继承提示之后 |
| 沙箱 | 可插拔 backend：`StateBackend` / `FilesystemBackend(root_dir, virtual_mode=True)` / `StoreBackend` / `CompositeBackend(default=..., routes={前缀: backend})` / `ContextHubBackend` / `LangSmithSandbox` / `LocalShellBackend`。**本项目只用 `FilesystemBackend` + `CompositeBackend`** |
| 半隔离 | `permissions=[FilesystemPermission(operations, paths, mode)]`；`operations` 仅 `read` / `write`（`delete` 归入 `write`）；`mode` 为 `allow` / `deny` / `interrupt`；**首条匹配生效、无匹配默认 `allow`** |
| 会话持久 | `checkpointer=`（`None \| bool \| BaseCheckpointSaver`），本项目传 `AsyncSqliteSaver`，按 `thread_id` 维度；`DeepAgentState` 用 `DeltaChannel(snapshot_frequency=50)` 增量存 messages 以降低 checkpoint 体积 |
| 人为介入 | `interrupt_on={tool_name: True \| InterruptOnConfig}`；`InterruptOnConfig` 支持 `allowed_decisions`（`approve/edit/reject/respond`）与 `description`（可为 callable）。**`interrupt_on` 只能做工具级开关、无法按参数判定**，「整章覆盖 / 大范围 edit」需自定义中间件调 `langgraph.types.interrupt()`（实测可用） |
| 上下文压缩 | `create_summarization_middleware(model, backend)`：除 langchain 版能力外，额外把被驱逐消息卸载到 `/conversation_history/{session_id}.md`、预截断历史里过大的 `write_file/edit_file` 参数、遇 `ContextOverflowError` 自动摘要重试；默认 `token_counter=count_tokens_approximately`，可替换为已安装的 `tiktoken` |
| 计划/待办 | **默认中间件栈不含 `TodoListMiddleware`**（实测 `langchain.agents.factory` 未引用它），本项目在 `middleware=` 中**显式加入**以获得 `write_todos` 工具与 `plan.update` 事件 |
| `general-purpose` | 传入同步 `subagents` 时自动追加 `general-purpose` 子代理；关闭方式是通过 harness profile `GeneralPurposeSubagentProfile(enabled=False)`，**不是普通参数**。harness profile 按模型匹配（内置 Anthropic sonnet/opus/haiku、NVIDIA、OpenAI codex），Qwen/DashScope 无匹配 profile → 走默认行为，M0 spike 需记录实际表现 |

**实测确认但本项目不使用的能力**（避免误读）：`store=` / `StoreBackend`、`memory=` / `MemoryMiddleware`、`RubricMiddleware`、`AsyncSubAgentMiddleware`（LangSmith 部署专用）、`ContextHubBackend`、`LangSmithSandbox`、`LocalShellBackend`、harness/provider profile 注册机制。

> **M0 必须先做 spike**：在真实装配下验证 `create_deep_agent` 的组合行为——`CompositeBackend` 多路由 + 8 条有序 `permissions` + `AsyncSqliteSaver` + `subagents` + `skills` + `interrupt_on` + `astream(subgraphs=True)` 的事件形状，以及 Qwen 无 harness profile 时的提示词与工具裁剪表现；结论回写本节后再定型 `agent/loop/builder.py`。

### （1）LLM 模块

负责 LLM 的正常接入使用（参数可设置）与异常处理。

**多厂商适配**（统一走 LangChain ChatModel）：

| 类型 | 厂商 | 实现 |
| --- | --- | --- |
| `openai_compatible` | DashScope/Qwen、DeepSeek、Moonshot、智谱、硅基流动、vLLM、任意自建网关 | `ChatOpenAI(base_url=...)` |
| `openai` | OpenAI | `ChatOpenAI` |
| `anthropic` | Claude | `ChatAnthropic` |
| `google` | Gemini | `ChatGoogleGenerativeAI` |
| `azure_openai` | Azure OpenAI | `AzureChatOpenAI` |
| `openrouter` | OpenRouter 聚合 | `ChatOpenAI(base_url=openrouter)` |

**参数设置**：`temperature / max_tokens / top_p / timeout / max_retries / stream / extra_body`，**四层覆盖优先级**（后者覆盖前者，未设置的项逐层回落）：

1. **全局默认**（`.env` + `llm_models.default_params_json`）；
2. **小说项目级**（`novel.yaml.llm_params`，可空）；
3. **会话级**（`agent_sessions.model_ref / subagent_ids_json / skill_ids_json` 等，经 `PATCH /agent/sessions/{sid}/context` 修改，作为该会话后续消息的默认）；
4. **单条消息级**（发消息 body 内的 `model_ref` 覆盖，仅对本次 run 生效、不回写会话）——右侧栏「切换本次使用的模型」即走此层。

**异常处理（harness 关键点）——优先复用 langchain 1.4.7 内置中间件，避免自研**：

| 诉求 | 实现（实测可用） |
| --- | --- |
| 请求超时 | per-provider `timeout`（`ChatOpenAI(timeout=...)` 等）+ 全局兜底默认值 |
| 超时重试 | `ModelRetryMiddleware(max_retries, backoff_factor, initial_delay, max_delay, jitter=True, retry_on=<可重试判定>)`，仅对超时 / 429 / 5xx / 连接错误重试；工具侧用 `ToolRetryMiddleware` + `ToolErrorMiddleware` |
| 最大调用次数 | `ModelCallLimitMiddleware(run_limit=<单次运行 LLM 调用上限>, exit_behavior="end")` 优雅收尾；**硬上限**用 `config={"recursion_limit": N}` 传入（不是 `create_deep_agent` 的参数） |
| 降级处理 | `ModelFallbackMiddleware(first_model, *fallbacks)`，降级链存 `llm_models.fallback_chain_json`（有序 `model_ref` 列表）；全失败返回可读错误并落审计（`agent_runs.fallback_count`） |
| 上下文压缩 | `create_summarization_middleware(model, backend)`（deepagents 版）：驱逐消息卸载到 `/conversation_history/`、大工具参数预截断、`ContextOverflowError` 兜底；配合「前情提要」替代逐章原文 |
| 重复错误避免 | **自研**（内置中间件无此能力）：对 `(tool_name, args_hash, error_fingerprint)` 记忆，二次失败注入告警，三次阻断该调用路径，落在 `WorkspaceGuardMiddleware.awrap_tool_call` 内 |

> 中间件装配顺序见「二、(2) Agent-Loop」。

**密钥安全**：API Key **只存 `.env` 环境变量、从环境变量读取，不入数据库**；DB 的 `llm_providers` 仅存 `api_key_env`（变量名引用）与非敏感元数据。

- `core/security.py` 提供 `get_api_key(env_name)`（读取 + 缺失检测）、`mask(key)`（`sk-****abcd` 掩码）、`set_env_key(env_name, value)`（UI 保存密钥时写回 `.env`）；
- 写回 `.env` 只允许**白名单键**（`*_API_KEY`、`LANGFUSE_*`），流程为「读全文 → 替换/追加目标键 → 临时文件 + `os.replace` 原子写 → 写前备份 `.env.bak`」，并加进程内互斥锁；
- 接口返回与日志一律脱敏（structlog processor 对键名匹配 `key|token|secret` 的字段掩码）；
- 「测试连接」`POST /llm/providers/{id}/test`（极小请求）；配置热生效 `POST /config/reload`（重读 `.env` + `config_version += 1` 使 Agent 工厂缓存失效）。

### （2）Agent-Loop 模块

掌管 Agent 的调用运行：接入 LLM、各类工具、各类中间件（节点检查、人为介入）。

- 由 `create_deep_agent(...)` 提供主循环（思考 → 工具 → 观察），异步执行；
  `thread_id = novel:{project_id}:session:{session_id}`，经 `config={"configurable": {"thread_id": ...}, "recursion_limit": N}` 传入。
- 运行期上下文（`project_id / session_id / run_id / gate_stage`）经 `context_schema` + `Runtime.context` 注入中间件与工具，**不使用全局变量/上下文变量传递**（避免并发 run 串数据）。

**流式**：LangGraph 事件流（`astream(..., subgraphs=True)` 以获取子代理事件），映射为 SSE 事件（信封 `{event, run_id, session_id, seq, ts, data}`，各事件 `data` 契约见「六、API 约定」）：

| 事件 | 触发时机 | 主要 data 字段 |
| --- | --- | --- |
| `run.start` | runner 启动 | `model_ref, gate_stage` |
| `message.delta` / `message.complete` | 模型流式 token / 单条消息完成 | `message_id, delta` / `content, tokens_in, tokens_out` |
| `tool.start` / `tool.result` | 工具调用前后 | `tool_name, args` / `status, result_excerpt, duration_ms, size` |
| `subagent.start` / `.delta` / `.end` | 子代理泳道 | `subagent_name, task` |
| `plan.update` | `write_todos` 被调用（需显式装配 `TodoListMiddleware`，deepagents 默认不带） | `todos[{content,status}]` |
| `file.changed` | Agent 用 `write_file/edit_file/delete` 改动项目文件成功后 | `virtual_path, change_kind(write\|edit\|delete), chapter_id?` —— 前端据此热更新正文/目录树 |
| `context.compact` | summarization 触发 | `before_tokens, after_tokens, offload_path` |
| `gate.update` | 门禁阶段流转 | `gate_stage, gate_result, retry_count` |
| `consult.request` | 下发咨询卡片 | `interrupt_id, question, options[], allow_text` |
| `interrupt.request` | 下发审批卡片 | `interrupt_id, tool_name, args, decisions[]` |
| `usage` | token 用量增量 | `tokens_in, tokens_out, model` |
| `error` / `run.end` | 出错 / 结束 | `code, message` / `status, usage, langfuse_trace_id` |

**钩子（真实 API）**：langchain 1.4.7 的 `AgentMiddleware` **只有** `before_agent / after_agent`、`before_model / after_model`、`wrap_model_call`、`wrap_tool_call`（各带 `a*` 异步版）+ `name / state_schema / trace_policy / transformers`；没有 `before_tool / after_tool / on_node_end / on_run_end`，一律按下表落到真实挂载点：

| 中间件（本项目自定义） | 挂载点 | 职责 |
| --- | --- | --- |
| `RecapInjectionMiddleware` | `abefore_model` | 注入前情提要、token 预算检查（超阈值先触发裁剪）；敏感内容检查**仅预留挂载位，MVP 不实现** |
| `WorkspaceGuardMiddleware` | `awrap_tool_call` | `WorkspaceGuard` 路径校验、审计埋点、耗时与结果大小统计、**错误指纹记忆与阻断**（重复错误避免） |
| `FileChangeNotifyMiddleware` | `awrap_tool_call`（工具成功后） | 发 SSE `file.changed` + 触发派生索引同步；**变更目标为大纲文件时额外触发 `sync_order_from_outline`**（卷章顺序回填，见「四」） |
| `WriteApprovalMiddleware` | `awrap_tool_call` | 按**参数**判定高危写入（整章覆盖、大范围 `edit`、批量 `delete`）→ 调 `langgraph.types.interrupt()` 转人工审批 |
| `TodoListMiddleware`（langchain 内置） | — | 提供 `write_todos` 工具 → `plan.update` 事件 |

> 运行收尾（写 Langfuse score：字数、工具成功率、是否降级；落 `agent_runs` 汇总）**不在中间件里做**，由 `agent/loop/runner.py` 在 `astream` 结束后统一执行（无 `on_run_end` 钩子）。

**中间件装配顺序**（`middleware=` 传入，deepagents 会把用户中间件插在核心栈之后、skills/prompt-caching/memory 之前）：
`ModelRetryMiddleware` → `ModelFallbackMiddleware` → `ModelCallLimitMiddleware` → `TodoListMiddleware` → `RecapInjectionMiddleware` → `WorkspaceGuardMiddleware` → `FileChangeNotifyMiddleware` → `WriteApprovalMiddleware`。

**人为介入（HITL）两层**：

1. **工具级**：`interrupt_on={...}` 对指定工具无条件暂停（也可由 `FilesystemPermission(mode="interrupt")` 自动生成，用户显式 `interrupt_on` 优先）；
2. **参数级**：`WriteApprovalMiddleware` 按参数判定「整章文件覆盖写入 / 大范围 `edit` / 批量删除章节或设定文件」后调 `interrupt()`——这是 `interrupt_on` 做不到的粒度。

前端渲染审批卡片，支持 **批准 / 编辑后批准 / 拒绝 / 回复补充**（`allowed_decisions=["approve","edit","reject","respond"]`）；`POST /agent/interrupts/{id}/resolve` 恢复运行。（卷/章/设定的增删改命名属项目管理 API，高危确认由前端弹窗承担，不走 Agent HITL。）

**咨询中断（type=consult）**：大纲门禁（G0/G1/G3）等需用户决策的场景下发**结构化咨询卡片**——`options`（选项按钮，如「让 Agent 代写大纲 / 我自己补 / 取消」）+ 自由输入框；由编排层调 `interrupt(payload)`（`interrupt_on` 只能挂工具，咨询不绑工具）。回复走同一个 `resolve` 接口，`decision=option|text`；运行状态置 `awaiting_consult`，恢复后从当前门禁阶段继续。

**取消**：`POST /api/v1/agent/runs/{run_id}/cancel`，协作式取消（置取消标志 → 当前工具/模型调用返回后停）+ deepagents 内置 `PatchToolCallsMiddleware` 自动修复被截断的消息历史。

### （3）Tools 模块

提供并接入各种能力：Agent 内置基本工具、Skill、MCP、Sub-agent。

**Agent 内置工具（仅基本工具，不注册写作业务函数）**：

- **文件类**（deepagents 内置，受 `permissions` + `WorkspaceGuard` 约束）——实测工具名为 `ls / read_file / write_file / edit_file / delete / glob / grep`。`operations` 映射：`ls / read_file / glob / grep → read`；`write_file / edit_file / delete → write`（**`delete` 走 write 权限**，删其它项目文件会被 deny 拦下）。`delete` 与「整章覆盖写入」属高危，须配 HITL 审批。
- **联网类**：`web_search`（自研内置工具，`httpx` 走免费免 Key 源，亦可被第三方 MCP 替换），供 `researcher` 与主代理查资料；**搜索源待 M3 实测选型**（候选 DuckDuckGo lite/html、Wikipedia API），源不可用必须返回明确错误而非静默空结果。
- **`execute` 不开放，且无需自行裁剪**：本项目用 `FilesystemBackend` / `CompositeBackend`，实测 `supports_execution()` 返回 `False`，deepagents 会在 `wrap_model_call` 阶段**自动把 `execute` 从模型可见工具中移除**。因此**禁止**引入 `LocalShellBackend` / `LangSmithSandbox`——除会激活任意命令执行，还会与 `permissions` 冲突：`FilesystemMiddleware` 在「backend 支持执行 + 权限路径未全部 scoped 到 routes」时**直接抛 `ValueError`**。

> ⚠️ **边界约定**：卷/章/设定的增删改命名等**写作业务函数不作为 Agent 工具**，统一下沉为「六、API 约定」中的小说项目管理 REST 接口，由前端调用；Agent 对小说内容的操作一律通过上述基本文件工具完成。

**派生索引同步**：无论文件由 Agent 工具还是项目管理 API 修改，服务层都在写入后**同步**更新派生索引（`story_fts` / `project_index`）；失败不阻断写入，改标 `project_index.dirty` 待全量重建（详见「四」）。

**知识库服务函数（非 Agent 工具，服务层内部调用）**：`summarize_chapter`（章节定稿或 Agent 运行结束后触发）、`build_recap`（经 `RecapInjectionMiddleware.abefore_model` 注入系统提示）、大纲章简介解析（供门禁 G0/G1/G3）；检索以 `GET /novels/{id}/search` API 提供给前端。

**Skills（写作技能）**：目录 + `SKILL.md`（YAML frontmatter）+ 可选参考资料。**`skills=` 接受 backend 虚拟路径而非磁盘路径**，故需经 `CompositeBackend` 路由挂载：

| 来源 | 物理位置 | 挂载为虚拟路径 | 读写 |
| --- | --- | --- | --- |
| 内置技能（随包只读） | `src/app/agent/skills/<name>/SKILL.md` | `/skills/builtin/` | Agent 只读（deny write） |
| 用户自建技能 | `data/skills/<name>/SKILL.md`（由 `skill_configs.content_md` + `assets_json` 物化落盘） | `/skills/user/` | 服务层可写，Agent 只读 |

传参 `skills=["/skills/builtin/", "/skills/user/"]`（后者同名覆盖前者）。frontmatter 约束（实测 deepagents 解析规则）：`name` **必须等于父目录名**、小写字母数字连字符、≤64 字符；`description` ≤1024 字符；可选 `allowed-tools`、`metadata.include_tools`（空格分隔工具名，可列运行期才知名字的 MCP 工具）；SKILL.md 上限 10MB。技能靠模型 `read_file` 渐进披露，**故 `/skills/**` 必须开 `read allow`，否则技能全部读不到**（见「二、(5)」）。

内置：`novel-planning`（大纲与分卷节奏）、`prose-writing`（文风场景）、`line-editing`（润色去水）、`continuity-check`（伏笔/时间线/人物一致性）、`naming`（人名地名功法命名）+ 题材类（玄幻/都市/奇幻）；用户可在「AI 工具 → 写作 Skill」新建/编辑/启停，存 `skill_configs` 并物化到 `data/skills/`。

**Sub-agents（写作子代理）**：

| 名称 | 职责 |
| --- | --- |
| `planner` | 策划：题材定位、三级大纲、分卷节奏、伏笔布局 |
| `writer` | 正文撰写：按大纲 + 前情提要 + 文风约束生成章节 |
| `editor` | 润色校对：去水词、修病句、统一称谓与语气 |
| `continuity-checker` | 连贯性审查：时间线、人物状态、设定冲突、未回收伏笔 |
| `researcher` | 资料检索：通过 `web_search` 内置工具或第三方 MCP 获取外部资料 |

每个可配置模型覆盖（`model`，用 `model_ref`）、专属提示词（`system_prompt`）、工具白名单（`tools`）、可用技能（`skills`）、独立权限（`permissions`，不设则继承主代理）、是否审批（`interrupt_on`）、上下文模式（`mode`，默认 `isolated`；`fork` 继承父上下文但**禁用 `skills`**）。`SubAgent` 字段实测为 `name / description / system_prompt / tools / model / middleware / interrupt_on / skills / permissions / response_format / mode`。`general-purpose` 子代理在传入 `subagents` 时由 deepagents 自动追加，关闭需经 harness profile `GeneralPurposeSubagentProfile(enabled=False)`（非普通参数）。

**MCP**：

- 内置**免费开源天气 MCP**（封装 **Open-Meteo**：免费、无需 Key、覆盖中国城市含 Geocoding），提供 `get_current_weather(city)` / `get_forecast(city, days)`，同时服务左上角动态组件与 Agent。实现基类用 `mcp.server.mcpserver.MCPServer`（mcp 2.3.0 中 `mcp.server.fastmcp` 已移除，仍有 `.tool` 装饰器与 `run_stdio_async / run_sse_async / run_streamable_http_async`）；天气逻辑同时抽到 `services/weather.py`，供组件 API 直接调用，避免两份实现漂移。
- 「AI 工具」面板支持添加**第三方 MCP**（stdio 命令 或 streamable-http / SSE URL），启动时探测工具列表并可启停；**仅支持开源免费、无需认证的 MCP**，不提供认证头/Token 配置。
- **接入方式**：deepagents 不内建 MCP 适配，第三方/内置 MCP 工具须经 **`langchain-mcp-adapters`**（当前未安装，见「八」）转成 LangChain tool 再并入 `tools=`；若与 mcp 2.x 不兼容（M0 spike 验证），回落为用 `mcp` 官方 client 自封装 `@tool`。
- **⚠️ stdio MCP 的安全边界**：`stdio` 型 MCP 由后端**拉起子进程执行命令**，等价于「受限的任意命令执行」，与「不开放 `execute`」原则存在张力。约束：① 用户添加时须**二次显式确认**并展示完整命令；② 命令全文落 `audit_logs`；③ 可执行文件走**白名单**（默认仅 `npx / uvx / python`），拒绝任意路径命令。

### （4）Message 模块

记录并处理 Agent 运行时各种「消息」，实现会话持久（项目消息树）。

- **消息树**：`messages.parent_id + branch_index`，会话保存 `active_leaf_id`。
  - 「重新生成」= 同父节点下新增兄弟分支；「编辑用户消息后重发」= 新建分支；前端可切换分支查看历史版本；
  - 构造 LLM 上下文时只取 `root → active_leaf` 的活跃路径。
- **持久化双轨**：
  1. 业务库 `messages / tool_call_logs / agent_runs`：供前端渲染、审计、导出；
  2. LangGraph **`AsyncSqliteSaver`** checkpoint（`checkpointer=` 传入，落 `data/agent_state.db`）：供 Agent 恢复运行、HITL 中断续跑；
     以 `run_id / thread_id` 关联，运行结束做幂等一致性校验。`DeepAgentState.messages` 用 `DeltaChannel(snapshot_frequency=50)` 增量存储，checkpoint 体积从 O(N²) 降到 O(N)——长会话尤其重要。
- **隔离**：会话严格按小说项目隔离（`agent_sessions.project_id`），右侧栏仅展示当前项目会话；一个项目可有多会话（如「大纲会话」「第 3 卷撰写会话」）。
- **运行日志**：`agent_runs`（状态、耗时、token 用量、模型、降级次数、错误、Langfuse trace id、门禁字段 `gate_stage / gate_retry_count / gate_result_json`）。

### （5）其他模块

- **沙箱（半隔离）**：`CompositeBackend` 路由 + `FilesystemPermission` **有序**规则清单。

  **backend 路由**（虚拟路径 → 宿主路径）：

  ```python
  backend = CompositeBackend(
      default=StateBackend(),                                   # /conversation_history/、/large_tool_results/ 落 checkpoint 状态，不碰宿主磁盘
      routes={
          "/projects/":      FilesystemBackend(root_dir=DATA_ROOT / "workspaces"),   # 各小说工作区
          "/skills/builtin/": FilesystemBackend(root_dir=PKG_ROOT / "agent/skills"), # 内置技能（只读）
          "/skills/user/":   FilesystemBackend(root_dir=DATA_ROOT / "skills"),       # 用户自建技能（只读）
      },
  )
  ```

  **权限规则清单（8 条，顺序即语义，不可调换）**：判定机制见「一」铁律 9（首条匹配生效、无匹配默认 `allow`），故兜底 deny 必须最后，技能与压缩卸载路径须在兜底之前显式放行：

| # | operations | paths | mode | 理由 |
| --- | --- | --- | --- | --- |
| 1 | read, write | `/projects/{pid}/**` | allow | 当前项目可读写 |
| 2 | write | `/projects/**` | **deny** | 其它项目**只读**（含增删改文件） |
| 3 | read | `/projects/**` | allow | 允许跨项目阅读 |
| 4 | read | `/skills/**` | allow | **技能渐进式披露依赖 `read_file` 读 `SKILL.md`；漏了这条则所有技能不可用** |
| 5 | write | `/skills/**` | **deny** | 技能由服务层维护，Agent 不得改写 |
| 6 | read, write | `/conversation_history/**` | allow | deepagents summarization 把驱逐消息卸载到此路径 |
| 7 | read, write | `/large_tool_results/**` | allow | 大工具输出卸载目录（`grep` 工具描述中即引用该根路径） |
| 8 | read, write | `/**` | **deny** | 兜底：项目区/技能区/卸载区之外完全隔离 |

  装配为等长的 `FilesystemPermission(operations, paths, mode)` 有序列表（构造签名见「设计依据」表），以常量集中定义在 `agent/sandbox/`，并配「顺序回归测试」（断言常量数组与本表逐行一致，见「十一」）。

  高危写入（整章覆盖、批量删除）用 `mode="interrupt"`（deepagents 经 `_build_interrupt_on_from_permissions` 自动转成 `interrupt_on` 条目）或 `WriteApprovalMiddleware` 转人工审批；`interrupt` 规则的路径模式须带字面量前缀锚点（如 `/projects/*/chapters/**`），否则 `ls/glob/grep` 这类批量工具会因「搜索子树可能重叠」而**过度触发**中断。

  > ⚠️ **限制与补偿控制**：`permissions` 由 `FilesystemMiddleware` 施加，**只作用于内置文件系统工具**，不覆盖自定义工具与 MCP。补偿控制：① Agent 侧不注册任何碰路径的自定义工具（只有 `web_search`）；② 服务层所有文件写入（项目管理 API、派生索引、技能物化、导出）统一经 `WorkspaceGuard`；③ 逐条越权单元测试；④ 第三方 MCP 仅接免认证开源服务，stdio 型另有白名单与二次确认。

  **`WorkspaceGuard` 规范**（服务层唯一文件写入口，`agent/sandbox/guard.py`）：

  1. 入参一律接受「虚拟路径或工作区相对路径」，内部换算为宿主路径；
  2. `Path(...).resolve()` 归一化后，必须严格落在 `DATA_ROOT/workspaces/{project_id}`（或声明的 `data/skills`、`data/exports` 白名单根）之内，否则抛 `PermissionError` → HTTP 403；
  3. 拒绝含 `..` 的路径、拒绝解析后越界的符号链接（`is_symlink()` 逐段检查）；
  4. 跨项目写（`project_id` 与目标路径不一致）一律拒绝；
  5. `summaries/` 只允许 `summarize_chapter` 服务写，禁止其它调用方（含 API 直传）写入；
  6. 每次写入成功后同步更新派生索引（见「四、故事知识库」）。

  不开放 `execute`（无任意命令执行），Agent 只能用内置文件工具、`web_search` 与 MCP 工具。
- **知识库 RAG**：见「四、故事知识库」。
- **提示词模块**：Jinja2 模板。主代理系统提示 = 角色设定 + 项目元信息 + 大纲 + 前情提要 + 已启用技能摘要 + 输出规范（**修改正文必须调用工具，不得只在回答里贴文本**）；writer 子代理系统提示额外强制「正文必须覆盖章节简介的剧情要点与衔接要点，不得引入简介之外的重大剧情」。模板文件集中在 `agent/prompts/`，**独立版本化**（门禁预检/后检提示词尤其需要，便于回归与调优）。
- **配置模块**：`.env`（端口、**LLM API Key**、DB 路径、Langfuse、数据根目录）+ DB 运行期配置（Provider / 模型 / 子代理 / 技能 / MCP / 通用设置）；配置变更热生效：Agent 工厂按 `(session_id, config_version)` 惰性构建并缓存（LRU 上限 32，防实例无限增长），`POST /config/reload` 使 `config_version += 1` 从而失效缓存。密钥类配置**只存 `.env`**，DB 仅存环境变量名引用。
- **远程 API 模块**：统一 `httpx` 异步客户端（超时、重试、限流、缓存），供天气 / 地理编码等外部服务使用。

### （6）大纲驱动生成工作流（阶段门禁）

**原则**：AI 生成正文**必须以大纲章节简介为前提**——知识库中存在目标章节的剧情简介才允许生成；没有则主动咨询用户补充，不允许凭空生成。工作流由**服务层硬编排**为四阶段门禁（非 Agent 自觉执行），预检/后检为**独立 LLM 校验调用**（服务层直连 ChatModel + `response_format` 结构化输出，**不进入 Agent 循环**），不通过不放行。

**执行主体分工**：G0 / G1 / G3 由服务层直连 LLM 完成（可预测、可单测、成本低）；**只有 G2 进入 deep agent**（writer 子代理写文件）。`agent_runs.gate_stage` 全程流转，前端 `GateStatusBar` 据此渲染。

```
用户请求生成第 N 章
   │
   ▼
G0 大纲存在性检查 ──无简介──▶ 咨询卡片（请补充大纲）──▶ 运行暂停，等待用户
   │有简介
   ▼
G1 衔接预检（独立 LLM 校验）──不合理──▶ 咨询卡片（问题清单+修改建议）──▶ 暂停
   │合理
   ▼
G2 生成（writer 子代理：章节简介 + build_recap → 正文，文件工具写入）
   │      ★生成前先把旧正文快照进 chapter_revisions(source="agent")，保证可回滚
   ▼
G3 匹配后检（独立 LLM 校验）──不匹配──▶ 自动重生成（≤2 次）──仍不匹配──▶ 咨询卡片
   │匹配                                                              （认可现状/手动修改/补充指示后再生成）
   ▼
完成：summarize_chapter 生成摘要 → 更新派生索引 → run.end
```

- **G0 大纲存在性**：经 `parse_outline` 按标题层级定位目标章简介小节（`# 第X卷 卷名` → `## 第X章 章名`），**卷名与章名须与 `volumes.yaml` / 章节 frontmatter `title` 归一化后完全一致**才算命中。区分两类缺失（咨询文案不同）：① **无对应小节** → 引导按模板补小节；② **小节存在但简介为空** → 引导填写简介；两类均**不自动插入占位小节**。缺失即发 `consult.request`（选项：让 Agent 代写大纲 / 我自己去「大纲」节点补 / 取消本次生成），运行置 `awaiting_consult`。
- **G1 衔接预检**：比对该卷各章简介之间的衔接合理性（因果、时间线、人物状态、伏笔承接），**并带入上一卷末章简介作为锚点**——否则每卷第 1 章无上文可校验（第一卷改用大纲附录的人物/世界观设定作锚点）。不通过则输出问题清单与修改建议并发咨询卡片（用户可改大纲后重跑，或强制跳过——需显式确认并落审计）。
- **G2 生成**：writer 子代理基于「当前章简介 + `build_recap`」生成正文；系统提示强制「必须覆盖简介的剧情要点与衔接要点」。**写入前先快照旧正文**（`chapter_revisions`，`source="agent"`），使重生成可回滚。
- **G3 匹配后检**：比对生成正文与该章简介的匹配度（要点覆盖、是否偏离、是否引入简介外重大剧情）；不匹配自动重生成（**最多 2 次**，每次重生前同样先快照），仍不匹配发咨询卡片：认可现状 / 手动修改正文或大纲 / 补充指示后再生成。
- **状态与可观测**：`agent_runs` 的 `gate_stage / gate_retry_count / gate_result_json`；SSE `gate.update`（阶段流转）与 `consult.request`（卡片下发）；门禁判定写入 Langfuse observation/score 供复盘调优。
- **适用范围**：门禁仅约束 **AI 生成正文**路径；用户手工编辑不受限，但编辑后可手动触发 G3 后检查看偏差提示。

## 三、怎么开发 Writer 应用

### （1）工程结构

- 前端网站页面放在 `src/web`，使用 **Vue 3 + Vite + TypeScript + Pinia + Element Plus**，正文编辑器用 **Tiptap（ProseMirror）**：支持选区改写、段落级 diff、AI 来源标记、对选中文字/段落用 AI 改写。
- **现状与 M0 迁移步骤**：`src/web` 已有无构建骨架（`vendor/` 存 vue 3.5.44 / vue-router 5.4.0 / pinia 4.1.0 的 esm-browser 构建 + `index.html` importmap，`python -m http.server` 即可运行；清单见「十、仓库现状」）。M0 引入 Vite：① `npm create vite`（`vue-ts` 模板）初始化 `package.json / vite.config.ts / tsconfig.json`；② 入口从 importmap 切到 Vite（删 `<script type="importmap">`）；③ 骨架 `.js` → `.ts`、`App.js` → `App.vue`、`HomeView.js` → `WorkspaceView.vue`、删 `AboutView.js` 并新增 `SettingsView.vue`；④ `vue / vue-router / pinia` 改由 npm 提供，**验证通过后删除 `vendor/`**（避免两份 Vue 运行时并存）；⑤ 按需引入 Element Plus / Tiptap / axios / markdown-it。
- 前端接口及 AI Agent 包装为后端服务，放在 `src/app`，采用 **FastAPI**。
- 前后端 C/S 架构通信（REST + SSE），便于生产环境分离部署（前端 Nginx / 后端 uvicorn，或 FastAPI 挂载静态资源单体部署）。
- 运行期数据统一放在 `data/`（不入版本库）：`data/app.db`（业务库）、`data/agent_state.db`（LangGraph checkpoint）、`data/workspaces/{project_id}/`、`data/skills/`（用户自建技能物化目录）、`data/exports/`、`data/trash/`（项目软删除回收）、`data/logs/`。
- Python ≥ 3.12，依赖用 `uv sync` 管理，必须使用项目 `.venv`（勿用系统 python）。**构建后端保留 `uv_build`**（`src/app` 的 `app` 是合法包名，只需显式声明模块位置，无需换构建工具；配置见「八、③」）。`app.cli` 提供子命令：`dev`（起 uvicorn）、`migrate`（Alembic 升级）、`rebuild-index`（全量重建 `story_fts` / `project_index`）。
- **目录边界**：`src/app/agent/` 存放 **AI Agent 的全部代码逻辑**（LLM 接入、Agent-Loop、门禁、工具、子代理、技能、MCP、记忆、知识上下文、沙箱、提示词、追踪）；其余后端内容（接口层 `api/`、业务编排 `services/`、数据层 `db/`、基础组件 `core/`、DTO `schemas/`）放在 `src/app` 其余目录；Agent 逻辑不外溢到 api/services，非 Agent 业务代码也不进入 agent/。

**后端目录（`src/app`）**

```
src/app/
├── main.py                     # 应用装配、lifespan、路由/静态资源挂载、CORS
├── cli.py                      # 命令行入口：dev / migrate / rebuild-index
├── core/
│   ├── config.py               # pydantic-settings，读取 .env
│   ├── security.py             # 环境变量密钥读写（get_api_key/set_env_key/mask），API Key 不入 DB
│   ├── errors.py               # 统一异常 + 全局异常处理器 + 错误码常量（见「六」）
│   ├── response.py             # 统一响应包装 {code,message,data}
│   ├── logging.py              # 结构化日志（输出到 JSONL 文件）、敏感字段脱敏 processor
│   ├── paths.py                # 数据根/工作区/技能/导出/回收站路径解析（统一 pathlib + utf-8）
│   └── deps.py                 # 依赖注入（DB session、服务实例、Agent 工厂）
├── db/
│   ├── session.py              # async engine / sessionmaker (aiosqlite)
│   ├── base.py                 # DeclarativeBase、命名约定、公共列（id/created_at/updated_at）
│   ├── models/                 # ORM 模型（见「五、数据模型」）
│   ├── fts.py                  # FTS5 建表/可用性探测/二元切分/降级 LIKE
│   └── migrations/             # Alembic
├── schemas/                    # Pydantic DTO（请求/响应/SSE 事件信封与各事件 data）
├── api/
│   ├── novels.py               # 项目 CRUD、导出、阅读进度
│   ├── volumes.py chapters.py  # 卷/章 CRUD、正文读写（乐观锁）、修订快照与回滚（★无重排接口，顺序由大纲派生）
│   ├── story.py                # 大纲读写与校验、章节简介解析、检索、章节摘要重建
│   ├── agent.py                # 会话、发消息(202+SSE)、分支、中断 resolve、运行取消、上下文
│   ├── llm.py                  # Provider/Model 管理、连接测试、降级链
│   ├── skills.py subagents.py mcp.py
│   ├── settings.py             # 通用设置（主题/字号/字体/页宽/自动保存间隔/回落城市）
│   ├── widgets.py              # 左上角动态组件数据（天气 / 反向地理编码）
│   └── audit.py                # 审计日志（只读）
├── services/                   # 业务编排层（事务边界、审计埋点、派生索引同步更新；写作业务函数、
│                               #   显式命名校验、导出、摘要生成、parse_outline；供 api/ 与 agent 门禁调用）
└── agent/                      # ★ AI Agent 全部代码逻辑（对应「二」各模块：LLM/Loop/门禁/Tools/Message/其他）
    ├── llm/                    # registry / factory / params（四层覆盖）/ budget（token 预算）
    ├── loop/                   # builder（create_deep_agent 装配）/ runner / events（LangGraph→SSE 映射）
    │                           # middleware/（RecapInjection·WorkspaceGuard·FileChangeNotify·WriteApproval）
    ├── gates/                  # 大纲驱动生成四阶段门禁编排（G0 存在性/G1 衔接预检/G2 生成/G3 匹配后检，独立 LLM 校验调用）
    ├── tools/                  # web_search 等少量内置工具 + registry（写作业务函数不注册为工具，位于 services/）
    ├── subagents/              # 写作子代理配置与构建
    ├── skills/                 # 内置写作技能（每个子目录一个 SKILL.md，只读；用户自建技能存 DB 并物化到 data/skills/）
    ├── mcp/                    # 内置 Open-Meteo 天气 MCP（mcp 2.x MCPServer）+ 第三方 MCP 接入管理（langchain-mcp-adapters）
    ├── memory/                 # 消息树、会话持久化、上下文构建（root→active_leaf 活跃路径裁剪）
    ├── knowledge/              # 大纲/摘要文件读取、章节摘要生成、build_recap、章节简介解析（门禁输入）
    ├── sandbox/                # backend 装配（CompositeBackend 路由）+ WorkspaceGuard + permissions 规则
    ├── prompts/                # 提示词模板（主代理/子代理/摘要/续写/门禁预检后检，独立版本化）
    └── tracing/                # Langfuse v5 集成（见下）
```

**可观测（Langfuse v5，实测 API 面）**：已装 `langfuse 5.0.4` 是 OpenTelemetry 版本，v2 的 `Langfuse.trace()` / `trace.span()` / `trace.generation()` **已不存在**（实测 `hasattr(Langfuse, "trace") == False`），仓库 `demo-langfuse.py` 是 v2 写法不可照抄。本项目用法：

- **自动埋点为主**：`from langfuse.langchain import CallbackHandler`，把 handler 挂到 Agent 调用的 `config={"callbacks": [handler]}` 上，自动产出 trace / span / generation 层级（含 token 用量）；
- **手动补充**：`langfuse.start_as_current_observation(name=...)` 包裹门禁 G0/G1/G3 校验、`summarize_chapter` 等服务层 LLM 调用，把 `gate_result_json` 的判定与分数作为 observation 的 metadata / `langfuse.create_score(...)` 写入；
- **运行收尾**：runner 在 `astream` 结束后写 score（本章字数、工具成功率、是否发生降级），并把 trace id 落 `agent_runs.langfuse_trace_id`（v5 下用 `langfuse.get_current_trace_id()` 获取）；
- **降级**：`LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY` 任一缺失时不初始化 client，全部埋点变为 no-op，仅保留本地 JSONL 结构化日志，**功能不受影响**。

**前端目录（`src/web`，M0 迁移到 Vite + TS 后的目标结构）**

```
├── package.json  vite.config.ts  tsconfig.json
├── index.html      # Vite 入口（挂载 #app；importmap 段在迁移后删除）
├── vendor/         # ★迁移期临时保留，验证通过后整体删除（vue/ vue-router/ pinia/ vue-devtools-api/ nostics/ perfect-debounce/）
└── src/
    ├── main.ts  App.vue  router/  stores/(pinia)  types/  i18n/(中文文案集中管理)
    ├── api/            # axios 封装 + 各模块接口 + SSE 客户端（seq / Last-Event-ID 续传 + 重连回落重拉消息树）
    ├── layouts/        # MainLayout：三栏可伸缩布局（左/中/右栏宽度可拖拽，右栏可收起）
    ├── views/          # WorkspaceView（三栏主界面）/ SettingsView（通用设置 + AI 工具）
    ├── components/
    │   ├── sidebar/    ProjectList(右键菜单) · NewNovelButton · DynamicWidget(宠物+城市+天气) · SidebarFooter(设置·AI工具入口)
    │   ├── reader/     ReaderToolbar(阅读/编辑切换) · TocDrawer(目录展开收回，顶端固定「大纲」节点) · ChapterReader ·
    │   │               OutlineReader(大纲 markdown 渲染阅读视图)
    │   ├── editor/     TiptapEditor · VolumeChapterTree(增删改/重命名，显式命名校验；★只读顺序，不可拖拽) · WordCountBar · AutosaveIndicator ·
    │   │               ConflictBanner(409 版本冲突提示 + 合并/覆盖选择) ·
    │   │               OutlineEditor(目录「大纲」节点入口：markdown 源码编辑器+编辑/预览切换+validate 警告条)
    │   ├── agent/      AgentPanel · MessageList(消息树+分支切换) · MessageBubble · ToolCallCard · SubAgentLane ·
    │   │               PlanTodoList(write_todos 渲染) · InterruptApprovalCard · ConsultCard(选项按钮+自由输入框) ·
    │   │               GateStatusBar(门禁阶段 G0-G3) · ContextChips(子代理/技能/章节) · ModelSwitcher · ChatInput
    │   ├── settings/   ThemeSettings · FontSettings · CitySettings(回落城市) · LlmProviderForm · SubAgentManager ·
    │   │               SkillManager · McpManager(stdio 命令白名单 + 二次确认弹窗)
    │   └── common/     DiffViewer · ExportDialog(zip 导出：正文格式单选 markdown 集合/epub/mobi/txt + 附大纲原 md) · EmptyState
    └── styles/         # CSS 变量驱动主题色/字号/字体（含暗色）
```

**动态组件数据流（宠物 + 城市 + 天气）**：

1. 前端用 `navigator.geolocation` 取坐标（**浏览器定位，坐标不入库**）；定位被拒绝/失败/非 HTTPS 环境 → 回落 `user_settings.city`（通用设置里的手填「回落城市」）；
2. `GET /widgets/city?latitude=&longitude=` 反向地理编码得到城市名（失败回落上一步的城市）；
3. `GET /widgets/weather?latitude=&longitude=`（或 `?city=`）取当前天气与预报；
4. 宠物动画状态机**只有两态**：写作中→敲键盘、久无进展（可配分钟数，默认 30 分钟无正文变更）→打瞌睡。

### （2）设置与审计

- **设置范围**：
  - **实现并持久化**：通用设置（主题色含暗色、字体大小/风格、阅读页宽、自动保存间隔、**回落城市**）；AI 工具设置（大模型 API、写作 sub-Agent、写作 Skill、MCP Server 管理，配置页随 M3 的 Provider API 交付）。
  - **不做**：个人资料中心、后台管理端（Admin Console）；无登录鉴权，单用户本地使用。
- **审计日志**：**数据表 + 只读查询接口**——`audit_logs` 记录关键动作（项目/卷/章增删改、大纲保存（含 `sort_order` 回填前后顺序）、导出、Agent 运行、工具调用、审批/咨询决定、G1 显式跳过、stdio MCP 命令全文、`.env` 密钥写入动作本身），不做后台管理 UI；同时以 JSONL 备份到 `data/logs/audit-{date}.jsonl`。

### （3）页面功能

**左侧栏**

- 最上方动态组件：**宠物动态 icon**（Lottie/SVG 动画状态机，**仅两态**：写作中→敲键盘、久无进展→打瞌睡；不设「每日目标字数」，故无「达成日目标」态）+ **所在城市** + **天气**（数据流见「三、(1) 动态组件数据流」；城市取浏览器定位，失败回落通用设置的「回落城市」）。
- 组件下方：【新建小说】/【删除小说】。新建必须填写书名（显式命名，违规返回 `42201`）；创建成功后服务层：① 写 `novels` 登记行（生成 `project_id`=uuid）；② 初始化工作区 `data/workspaces/{project_id}/`；③ 写入大纲模板文件 `outline/{小说名称}-大纲.md`。
- 再下方：各小说项目列表（标题、字数、更新时间，来自 `project_index` 缓存；右键菜单：重命名、导出、复制、删除）。**删除为软删除**：工作区移入 `data/trash/{id}-{ts}/` + `novels.status="deleted"`，列表不再显示（保留可恢复窗口，不做恢复 UI）。
- 左下角：【通用设置】与【AI 工具】按钮入口。
- **导出到本地**（`ExportDialog`）：产物为**一个 zip**（`{小说名称}.zip`）= 正文（格式单选）+ 大纲原 md（不转换）：
  - `markdown`：所有章节 md 集合；`txt`：自研纯文本拼接（卷章标题 + 正文按顺序连接）；
  - `epub`：EPUB 3 自研 `zipfile` 生成器（`mimetype` 必须为**首个条目且 STORE 不压缩** / `META-INF/container.xml` / `OEBPS/content.opf` / `nav.xhtml` / 各章 xhtml），不引入重型依赖，结构校验见「十一」；
  - `mobi`：EPUB 产物经**可选外部工具** calibre `ebook-convert` 转换；本机未安装 calibre，选项默认置灰并提示安装（启动时探测，M1 落地）；
  - 导出为异步任务：产物落 `data/exports/`，接口返回下载链接，动作写审计。

**通用设置**：页面主题颜色（预设 + 自定义主色 + 暗色）、小说字体大小、小说字体风格（宋体 / 黑体 / 楷体 / 自定义）、阅读页宽、自动保存间隔、回落城市。全部持久化到 `user_settings`（单行）。

**AI 工具**：大模型 API 设置（Provider CRUD、连接测试、默认模型、降级链、参数）、写作 sub-Agent 管理、写作 Skill 管理、MCP Server 管理（stdio 型须走白名单 + 二次确认，见「二、(3)」）。

- **密钥字段交互**：编辑态输入框以黑点（`••••`）隐藏；保存后只展示掩码 `sk-****abcd`，不回显明文；
- **密钥存储**：保存时后端将密钥**写入 `.env` 对应环境变量**（如 `DASHSCOPE_API_KEY=...`），DB 只记 `api_key_env` 变量名；保存成功后自动触发配置重载（同 `/config/reload`）；
- **状态提示**：环境变量缺失时 Provider 卡片显示「未配置密钥」警示。

**主页面（中间栏）**

- 最上方：【阅读模式】/【编辑模式】切换。
- **阅读模式**：展示目录与正文（类阅读软件），目录可展开收回；支持章节翻页、**进度记忆**（`reading_progress` 表：`novel_id` 主键 + `chapter_id` + `scroll_ratio`，进入项目时定位上次位置）、夜间模式。**目录最顶端固定「大纲」节点**（位于第一卷之前）：以 markdown 渲染视图阅读 `outline/{小说名称}-大纲.md`，与正文章节一样可翻页/定位。
- **编辑模式**：两个页签——【正文】【卷章】（大纲编辑入口归一到目录「大纲」节点，不单设页签）。
  - 【正文】：Tiptap 编辑（Markdown 快捷输入、选中浮层「AI 改写 / 扩写 / 缩写 / 润色」、AI 写入段落带来源标记与 diff 查看 / 接受 / 回滚）；字数统计与自动保存（间隔见通用设置）；**乐观锁**：保存请求携带读取时的 `base_version`（来自章节 frontmatter `version`），服务端版本不一致返回 `409 + code=40901` 并回传最新内容与版本号，`ConflictBanner` 提示「AI 或其它端已修改，请选择合并/覆盖」（契约详见「六」）；每次成功保存把旧内容快照进 `chapter_revisions`（`source="manual"`，自动保存不快照以防版本爆炸）。
  - 【卷章】：增删卷章、改名；**新建卷/章必须显式命名**（弹窗强制必填，违规返回 `42201`）。
    - **★不提供任何手工重排交互**（无拖拽、无上移/下移、无跨卷移动）：卷章树按 `sort_order` 只读渲染，树上方提示「卷章顺序由大纲小节先后决定」+「去大纲调整」按钮直达 `OutlineEditor`。
    - 新建卷/章一律**追加到末尾**（`sort_order = max+1`）；要插在中间位置就先建好再改大纲小节顺序（保存大纲时由 `sync_order_from_outline` 回填）。
    - 删除卷级联删除其下章节文件（二次确认弹窗列出章名与字数）；**章不可跨卷移动**——需换卷就在目标卷下新建同名章并复制正文，再删旧章。
- **大纲编辑（目录「大纲」节点，编辑模式下）**：`OutlineEditor` 为 **markdown 源码编辑器**（等宽文本框）+「编辑 / 预览」切换，保证 `# 卷 / ## 章` 层级不被富文本破坏；保存经 `PUT /novels/{id}/outline`，写入成功后服务层执行 **`sync_order_from_outline`**（★卷章顺序的唯一写入口，规则见「四」），随后调用 `GET /novels/{id}/outline/validate`，卷/章名归一化后不一致、序数与 `sort_order` 不一致时以警告条提示（不阻断保存）。

**右侧栏（Agent 面板）**

- 点击右上角 Agent 图标展开 / 收回；展示会话内容，**按小说项目隔离**（仅当前项目），一个项目可多会话（切换下拉）。
- 消息流渲染：用户消息、AI 回复（流式）、工具调用卡片（可折叠、含耗时与结果摘要）、子代理泳道、计划待办（`write_todos` → `PlanTodoList`）、审批卡片、**咨询卡片（ConsultCard：选项按钮 + 自由输入框）**、**门禁状态条（GateStatusBar：G0→G3 流转与预检/后检结论）**；收到 `file.changed` 时对中间栏正文/目录树做热更新提示。
- 消息树分支：「重新生成」= 同父节点新增兄弟分支；「编辑用户消息后重发」= 新建分支；可切换分支查看历史（`GET /agent/sessions/{sid}/messages` + `POST /agent/sessions/{sid}/branch` 切 `active_leaf_id`）。
- 输入窗口：可主动添加**写作 sub-Agent、写作 Skill、已读章节**（及大纲章简介 / 补充设定）作为上下文 Chips；可**切换本次使用的模型**（单条消息级覆盖，见「二、(1)」）。
- 支持打断运行（`POST /agent/runs/{run_id}/cancel`）、重新生成、复制、导出会话。

## 四、故事知识库（大纲为纲，文件事实源 + FTS5 派生索引）

**核心定位**：知识库以**大纲 markdown 文件为纲**——各卷-章剧情简介是 AI 生成正文的**必要前提**；世界观、人物、战力、写作要求等设定是大纲文件内的**可选补充信息**。不设独立实体卡目录（`world_build/` 已取消）。

- **大纲文件结构**（**单文件**，`outline/` 内仅此一个）：
  - 文件名 `outline/{小说名称}-大纲.md`（由 novel title 派生：替换 Windows 非法字符 `\/:*?"<>|` 为 `_`；小说重命名时服务层同步重命名该文件并更新 `novel.yaml.outline_file`）；
  - **项目创建时强制初始化**：`POST /novels` 由服务层写入内置模板常量，含两部分：① **卷-章简介参考格式** `# 第X卷 卷名` → `## 第X章 章名` → `- 简介：…`（生成正文的必要前提）+ `- 衔接要点：…`（建议填写）；② **附录补充资料格式**（可选）`# 附录A 世界观` / `# 附录B 人物角色` / `# 附录C 战力层次` / `# 附录D 写作要求` 等，各带格式示例；
  - **解析规则**（服务层 `parse_outline`，`services/outline.py`）：
    - **标题层级判定必须严格**：H1 用 `^#(?!#)\s+(.*)$`（`#` 后不接 `#`），H2 用 `^##(?!#)\s+(.*)$`，H3 及更深忽略。宽松的 `^#\s*第?.*卷` 会把 `## 第三章 卷土重来` 误判为卷节，须避免；
    - H1 中匹配 `^附录` 的为**附录节**（世界观/人物/战力/写作要求），其余 H1 视为**卷节**；只有卷节下的 H2 才是**章简介小节**（附录节下的 H2 视为附录子条目，不参与章匹配）；
    - 章简介小节按行解析 `- 简介：…`（必需）与 `- 衔接要点：…`（可选）；两者都缺 → 视为「小节存在但简介为空」；
    - **卷/章名归一化后比较**（`normalize_title`）：去首尾空白 → 全角空格转半角 → 压缩连续空白 → 去序数词前缀（`第X卷` / `第X章` / `第X回` / `第X节`，X 支持阿拉伯与中文数字）→ 去标点（`·、,，.。:：-—`）。归一化后与 `volumes.yaml` 的 `title` / 章节 frontmatter 的 `title` **完全相等**才算命中；
    - 序数词与 `sort_order` 冲突时出校验警告（不阻断）；解析全程只读、不写文件，**不自动插入占位小节**。
  - 章简介小节与正文章节一一对应，是「大纲驱动生成工作流」（见「二、(6)」）的输入。
- **★卷章顺序的唯一事实源 = 大纲小节先后**（`services/outline.py` 的 `sync_order_from_outline`）：
  - **触发点只有两个，都收敛到这一个服务函数**：① `PUT /novels/{id}/outline` 保存成功后；② Agent 用文件工具改写大纲时，由 `FileChangeNotifyMiddleware` 在工具成功后走的「派生索引同步」路径一并触发（见「二、(2)」中间件表）。除此之外任何接口都不写 `sort_order`。
  - **规则**：按卷节 H1 先后写 `volumes.yaml` 的 `sort_order`（1..N）；按各卷节下章简介 H2 的先后写对应章节 frontmatter 的 `sort_order`（卷内 1..N）。匹配以归一化后「卷名+章名完全一致」为准；大纲中未出现的卷/章保持原值并追加在该卷末尾（`validate` 给「未在大纲中登记」警告，不阻断）。
  - **与 M1 的关系**：M1 尚无大纲解析能力，`sort_order` 即创建先后（新建追加末尾）；M4 交付 `parse_outline` 后改为**由大纲派生并覆盖**创建顺序，两阶段前端交互与接口形态一致（都只读顺序）。
  - **不做什么**：不改 ID / 文件名、不移动章节文件、**不支持章挪到另一卷**（大纲小节归属变化只出警告，需用户手工在目标卷重建并删旧章）；不因顺序调整触发 `chapter_revisions` 快照或 `version` 自增（顺序是元数据而非正文变更，避免与乐观锁/自动保存互相干扰）。
  - **理由**：阅读翻页、EPUB/txt 导出顺序、`build_recap` 的「最近 N 章」、G1 的「上一卷末章锚点」都依赖顺序；再开手工重排入口会造成「大纲顺序 vs `sort_order`」双源漂移，且批量改 frontmatter 放大写入、与自动保存/Agent 写同章撞乐观锁。
- **章节摘要**：`summaries/ch-{chapter_id}.md`（章节定稿或 Agent 写完后由服务层触发 `summarize_chapter` 生成，200–400 字摘要 + 关键事件），供前情提要与后检参照；不要求人工维护，且**只允许 `summarize_chapter` 服务写入**（`WorkspaceGuard` 强制）。
- **检索**：服务层 `search_settings(query, project_id, scope?, limit)` 走 SQLite **FTS5**（`MATCH` + `bm25()` 排序）。
  - **中文分词主方案 = 手工二元切分 + `unicode61`**：把中文切成空格分隔的二字词（`少年走出山村` → `少年 年走 走出 出山 山村`）后存入 FTS5 列；**查询侧必须用同一个切分函数**（否则永远查不到）。实测可正常命中并有 `bm25()` 区分度。
  - **`trigram` 只作 ≥3 字查询的可选补充**：实测对 **2 字词零命中**（`MATCH '少年'` 返回 0 行，3 字起才命中），不能作主方案；
  - **降级**：FTS5 不可用或查询异常时回落 `LIKE '%q%'`（接口签名与返回结构不变，`data.degraded=true`）；
  - 不使用向量库（零 Embedding 费用、零额外服务）。数据源为大纲单文件 + `summaries/`；以 `GET /novels/{id}/search?q=&scope=` 提供给前端，**不作为 Agent 工具**（Agent 用内置 `grep` 搜文件）。
- **前情提要 `build_recap`**：当前章简介 + 所属卷节 + 最近 N 章摘要 + 大纲附录补充设定 → 组装上下文块，由 `RecapInjectionMiddleware.abefore_model` 注入系统提示。预算 = `llm_models.max_context × 阈值(默认 0.5)`；token 计数默认用 `count_tokens_approximately`，可切 `tiktoken`。预算不足时按「**当前章简介 + 卷节 > 最近 3 章摘要 > 附录补充设定 > 更早摘要**」优先级裁剪。
- **大纲门禁**：AI 生成正文前必须通过大纲存在性与衔接预检（详见「二、(6)」）；无目标章简介时主动咨询用户补充，不允许凭空生成。
- **大纲校验**：`GET /novels/{id}/outline/validate` 返回解析校验结果（卷/章名归一化一致性、缺失清单、序数与 `sort_order` 冲突、大纲未登记的卷/章、格式错误行号），供前端警告条与 G0 复用（同一份 `parse_outline`，避免两套解析漂移）。
- **FTS5 可用性探测（M0）**：**两步探测**——先 `SELECT fts5(NULL)`（已编译返回 `(None,)`，未编译报 `no such function: fts5`），再建临时表验证 `unicode61` 与 `bm25()`；任一步抛 `sqlite3.OperationalError` 即永久降级 `LIKE`（进程内缓存探测结果）。本机实测（SQLite 3.53.4 / Python 3.12.14）：FTS5 可用，`unicode61` / `trigram` / `porter` 均可建表，`bm25()` 可用。
- **派生索引一致性**：文件写入（Agent 工具或项目管理 API）后由服务层**同步**更新 `story_fts` / `project_index`；失败不阻断写入，置 `project_index.dirty = 1` 并告警，由 `writer rebuild-index` 或 `POST /novels/{id}/index/rebuild` 全量重建（`POST /novels/{id}/summaries/rebuild` 只重建摘要与摘要索引）。

## 五、数据模型（SQLite + SQLAlchemy 2.0 async）

### 全局约定

- **主键**：所有 `id` 为 `TEXT`（uuid4），应用层生成；不用自增整数（避免与工作区文件名中的短 ID 混淆）。卷/章另取 uuid 前 8 位作文件与 YAML 标识（`v{vol_id}` / `c{chapter_id}`），冲突时重取，一旦生成**永不改变**（见「一」铁律 10）。
- **时间戳**：`created_at / updated_at / started_at / ended_at / decided_at` 一律 UTC ISO-8601 文本（`YYYY-MM-DDTHH:MM:SS.sssZ`），应用层写入，**不用 SQLite 触发器或 `CURRENT_TIMESTAMP`**（见「一」铁律 11）。
- **JSON 列**：命名统一 `*_json`，存 `TEXT`，由 Pydantic 模型序列化/校验；读取失败视为空值并告警，不抛异常中断。
- **枚举**：以 `TEXT` + 应用层校验实现（SQLite 无原生枚举），取值在下方注释中列全。
- **软删除**：仅小说项目有（`novels.status='deleted'` + 工作区移入 `data/trash/`）；其余实体为硬删除。
- **外键**：开启 `PRAGMA foreign_keys=ON`（连接建立时设置）；跨库（`app.db` ↔ `agent_state.db`）不建外键，仅靠 `thread_id` 约定关联。

### SQLite 存储（`data/app.db`，结构化数据）

```
novels(id, title, workspace_dir, outline_file, genre, status, created_at, updated_at)
      -- ★项目登记表：id 即 project_id(uuid)；workspace_dir 相对 data/ 根；
      --   outline_file 为大纲相对路径（重命名后同步更新，是大纲唯一寻址依据）；
      --   status: active|deleted。小说内容仍以文件为事实源，本表只做登记 + 外键锚点，可从工作区全量重建
chapter_revisions(id, novel_id, chapter_id, version, content_md, word_count,
                  source, run_id, created_at)
      -- ★章节修订快照：source: manual|agent；run_id 可空（agent 写入时填）；
      --   触发时机：手动保存成功 / Agent 写章节前 / 章节定稿；自动保存不快照（防版本爆炸）；
      --   保留策略：每章最近 N 版（默认 20，可配），超限清理最旧的非定稿快照
reading_progress(novel_id PK, chapter_id, scroll_ratio, updated_at)
      -- ★阅读进度：单用户单行/项目；scroll_ratio 为 0..1

audit_logs(id, action, entity_type, entity_id, detail_json, ip, user_agent, created_at)
           -- action 覆盖：项目/卷/章增删改、大纲保存（含 sort_order 回填）、导出、Agent 运行、工具调用、
           --   审批与咨询决定、G1 显式跳过、stdio MCP 命令全文、.env 密钥写入（只记键名与掩码，不记明文）

llm_providers(id, name, type, base_url, api_key_env, extra_json, enabled, priority, created_at)
              -- api_key_env 仅存环境变量名（如 "DASHSCOPE_API_KEY"），密钥本身只在 .env，不落库
              -- type: openai|openai_compatible|anthropic|google|azure_openai|openrouter
llm_models(id, provider_id, model_id, display_name, max_context, supports_tools,
           default_params_json, is_default, enabled, fallback_chain_json)
           -- fallback_chain_json：有序 model_ref 列表，供 ModelFallbackMiddleware 使用
           -- model_ref 统一格式 "{provider_id}:{model_id}"

agent_sessions(id, project_id, title, active_leaf_id, model_ref, subagent_ids_json,
               skill_ids_json, context_chapter_ids_json, status, created_at, updated_at)
               -- project_id → novels.id；status: idle|running|awaiting_approval|awaiting_consult|error
messages(id, session_id, parent_id, branch_index, role, content_json, tool_calls_json,
         status, tokens_in, tokens_out, model, created_at)
         -- role: user|assistant|tool|system；status: streaming|complete|error|cancelled
tool_call_logs(id, message_id, session_id, tool_name, args_json, result_excerpt,
               status, error, duration_ms, created_at)
agent_runs(id, session_id, project_id, status, model_ref, recursion_used,
           tokens_in, tokens_out, fallback_count, langfuse_trace_id, error, started_at, ended_at,
           gate_stage, gate_retry_count, gate_result_json)
           -- status: pending|running|awaiting_approval|awaiting_consult|cancelling|cancelled|completed|failed
           -- gate_stage: G0|G1|G2|G3|NULL（非生成类运行为空）；gate_result_json: 预检/后检判定、分数与理由
agent_interrupts(id, run_id, session_id, type, tool_name, payload_json,
                 decision, decided_by, decided_at)
                 -- type: approval|consult；tool_name 可空（consult 不绑工具）
                 -- decision: approve|edit|reject|respond|option|text（未决时为空）
                 -- payload_json: approval 含工具名/参数/允许决策；consult 含 question/options[]/allow_text/gate_stage

subagent_configs(id, name, description, system_prompt, model_ref,
                 tools_json, skills_json, mode, permissions_json, interrupt_on_json,
                 enabled, is_builtin, created_at)
                 -- mode: isolated|fork（fork 下 skills_json 必须为空，装配时校验）
skill_configs(id, name, description, content_md, assets_json, enabled, is_builtin)
              -- name 必须等于物化目录名（小写连字符 ≤64）；物化到 data/skills/{name}/SKILL.md
mcp_servers(id, name, transport, command_json, url, enabled, confirmed_at, created_at)
            -- transport: stdio|http|sse；仅接开源免费、无需认证的 MCP，故无认证头字段
            -- confirmed_at：stdio 型的用户二次确认时间；command_json 只允许白名单可执行文件
user_settings(id, theme, primary_color, font_size, font_family, reading_width,
              autosave_interval_sec, city, widget_enabled_json, pet_idle_minutes, updated_at)
              -- 单行表（id 固定 'default'）；theme: light|dark|system
              -- city 语义 = ★回落城市（浏览器定位失败/被拒时使用），不是定位结果
              -- autosave_interval_sec 默认 30；pet_idle_minutes 默认 30（宠物打瞌睡阈值）
```

**派生索引与外键索引清单**：`messages(session_id, parent_id)`、`agent_runs(session_id, started_at)`、`tool_call_logs(session_id, created_at)`、`audit_logs(entity_type, entity_id)`、`chapter_revisions(chapter_id, version DESC)`、`agent_interrupts(run_id, decided_at)`、`llm_models(provider_id)`；外键：`agent_sessions.project_id → novels.id`、`messages.session_id → agent_sessions.id`、`agent_runs.session_id → agent_sessions.id`、`chapter_revisions.novel_id → novels.id`、`reading_progress.novel_id → novels.id`、`llm_models.provider_id → llm_providers.id`。

### 文件存储（小说与知识库，事实源）

```
data/
├── workspaces/{project_id}/   # project_id = novels.id(uuid)
│   ├── novel.yaml             # 项目元信息：title, author_name, genre, synopsis, cover_url, status,
│   │                          #   llm_params（项目级模型参数覆盖，可空）,
│   │                          #   outline_file（大纲相对路径，默认 outline/{title}-大纲.md，重命名后同步更新）
│   ├── volumes.yaml           # 卷列表：id(短uuid，永不变), title, summary,
│   │                          #   sort_order(★派生值，由大纲卷节先后回填，不提供手工重排)
│   ├── chapters/
│   │   └── v{vol_id}/c{chapter_id}.md   # 章节正文，frontmatter：
│   │                                    #   title, sort_order(★派生值，同上；章不可跨卷移动),
│   │                                    #   word_count, status(draft|finalized),
│   │                                    #   version(乐观锁计数), updated_at
│   ├── outline/               # 大纲（知识库之纲，AI 生成的必要前提；目录内仅一个文件）
│   │   └── {小说名称}-大纲.md  # 创建时由服务层写入模板（卷章简介格式 + 附录格式，见「四」）
│   └── summaries/             # 章节摘要（summarize_chapter 自动生成，非人工维护，仅该服务可写）
│       └── ch-{chapter_id}.md # frontmatter：key_events, model, created_at
├── skills/{name}/SKILL.md     # 用户自建技能物化目录（Agent 只读，服务层写）
├── exports/                   # 导出 zip 产物（异步任务落地，可定期清理）
├── trash/{project_id}-{ts}/   # 软删除项目的工作区（保留可恢复窗口）
└── logs/                      # JSONL 运行日志（见下）
```

> 设定信息与全书/卷级信息统一承载于单一大纲文件（卷节 H1 / 章简介 H2 / 附录 H1），不设实体卡目录与多文件大纲；章简介小节与正文章节按「归一化后卷名+章名完全一致」一一对应。

### 派生索引（`data/app.db`，从文件重建）

```
story_fts(doc_ref UNINDEXED, project_id UNINDEXED, kind UNINDEXED, title, content)
          -- ★FTS5 虚拟表，tokenize='unicode61'，title/content 存「二元切分后」的文本
          -- doc_ref: 定位源文件（如 outline 或 summaries/ch-{id}.md）；kind: outline|summary
          -- ★project_id 必须入表且 UNINDEXED：GET /novels/{id}/search 查询时附加
          --   `AND project_id = ?` 过滤，否则跨项目串数据
          -- 从大纲单文件 + summaries/ 构建；文件写入后同步更新，失败标 dirty
project_index(project_id PK, chapter_count, total_words, last_updated, dirty)
          -- 项目统计缓存 + 脏标记（dirty=1 表示派生索引待重建）
```

### JSONL 文件存储（应用运行日志）

```
data/logs/
├── app-{date}.jsonl           # 应用运行日志（structlog 输出，按日期轮转）
├── error-{date}.jsonl         # 错误日志
└── audit-{date}.jsonl         # 审计日志备份（同时存 SQLite audit_logs 表）
```

**文件与 DB 的关系**：正文与知识库以 Markdown 文件为唯一内容事实源，写入统一经 `WorkspaceGuard`（Agent 工具经 permissions + Guard，服务层写入经 Guard），变更后**同步**更新派生索引，失败标 `dirty` 待重建；前端渲染与导出直接读文件。`novels` 表只做项目登记与外键锚点（可从工作区扫描重建）；Agent 消息/会话/运行、修订快照、阅读进度、审计等结构化数据存 SQLite，不涉及文件。

## 六、API 约定（REST + SSE，统一前缀 `/api/v1`）

### 路由树（唯一权威清单，二/三章提到的路径均以此为准）

```
认证     （无，单用户本地应用，无需认证）

项目     GET    /novels                                    列表（含 project_index 统计）
         POST   /novels                                    创建（显式命名校验 + 初始化工作区与大纲模板）
         GET    /novels/{id}                               详情
         PATCH  /novels/{id}                               重命名/改元信息（事务内同步重命名大纲文件）
         DELETE /novels/{id}                               软删除（移入 data/trash/ + status=deleted）
         POST   /novels/{id}/export?format=markdown|epub|mobi|txt
                                                          ★异步导出 zip：正文按 format + {小说名称}-大纲.md 原件
         GET    /novels/{id}/export-tasks/{task_id}        导出任务状态与下载链接
         GET|PUT /novels/{id}/reading-progress             ★阅读进度（reading_progress 表）

卷章     GET|POST          /novels/{id}/volumes            卷列表/新建（显式命名；新建追加到末尾）
         PATCH|DELETE      /volumes/{vid}                  改名/删除（删除级联删除其下章节文件）
         GET|POST          /volumes/{vid}/chapters         章列表/新建（显式命名；新建追加到卷末尾）
         PATCH|DELETE      /chapters/{cid}
         GET|PUT           /chapters/{cid}/content         ★正文读写（PUT 必带 base_version，见下「乐观锁」）
         GET               /chapters/{cid}/revisions       ★修订快照列表（chapter_revisions）
         POST              /chapters/{cid}/revisions/{rid}/restore  ★回滚到指定快照
         ★无 reorder / move 接口：卷章顺序是大纲派生值，不提供手工重排与跨卷移动（见「一」铁律 10、「四」）

大纲     GET|PUT  /novels/{id}/outline                     ★大纲单文件读写（outline/{小说名称}-大纲.md）；
                                                          PUT 成功后执行 sync_order_from_outline 回填卷章 sort_order
         GET      /novels/{id}/outline/validate            ★解析校验：卷/章名归一化一致性、缺失清单、序数与顺序冲突、错误行号
检索     GET      /novels/{id}/search?q=&scope=&limit=     ★检索源：大纲单文件 + summaries/（限本项目）
摘要     POST     /novels/{id}/summaries/rebuild           重建章节摘要与派生索引
索引     POST     /novels/{id}/index/rebuild               ★重建 story_fts / project_index（清 dirty）

LLM      GET|POST       /llm/providers      PATCH|DELETE /llm/providers/{id}
         POST           /llm/providers/{id}/test           ★连接测试（极小请求）
         GET|POST|PATCH /llm/models（含 fallback_chain 配置）
         POST           /config/reload                     ★运行期重读 .env 并使 Agent 工厂缓存失效

Agent    GET|POST  /agent/sessions?novel_id=               会话列表/新建（按项目隔离）
         PATCH|DELETE /agent/sessions/{sid}
         GET       /agent/sessions/{sid}/messages           ★消息树（含分支）
         POST      /agent/sessions/{sid}/messages           ★发消息 → 202 + {run_id}
         POST      /agent/sessions/{sid}/branch             ★切换 active_leaf_id（分支/重新生成）
         PATCH     /agent/sessions/{sid}/context            会话级上下文（模型/子代理/技能/上下文章节）
         GET       /agent/runs/{run_id}                     运行详情（状态/用量/门禁）
         GET       /agent/runs/{run_id}/stream               ★SSE（run 级；run_id 全局唯一即可定位 session）
         POST      /agent/runs/{run_id}/cancel               ★取消（协作式）
         POST      /agent/interrupts/{id}/resolve            ★审批与咨询统一入口
                     -- approval: decision=approve|edit|reject|respond（edit 带 edited_args，respond 带 message）
                     -- consult:  decision=option|text（option 带 choice，text 带 text）

配置     /subagents（CRUD）| /skills（CRUD + 物化）| /mcp-servers（CRUD + /{id}/probe + /{id}/confirm）
         GET|PUT /settings                                   通用设置（单行）
         GET /widgets/weather?latitude=&longitude=|?city=     ★动态组件天气
         GET /widgets/city?latitude=&longitude=               ★反向地理编码（失败回落 settings.city）
         GET /widgets/pet-state?novel_id=                      ★宠物状态（writing|idle）
         GET /audit-logs?action=&entity_type=&from=&to=&page=  审计只读查询
```

### 通用约定

- **统一响应包装** `{code, message, data}`，HTTP 状态码与之对齐（成功 `code=0` + 2xx）。
- **错误码规范**（`core/errors.py` 常量，前端据此做差异化提示）：

| code | HTTP | 含义 |
| --- | --- | --- |
| `0` | 2xx | 成功 |
| `40001` | 400 | 参数格式错误 |
| `40301` | 403 | 越权（WorkspaceGuard 拒绝 / 跨项目写入） |
| `40401` | 404 | 资源不存在 |
| `40901` | 409 | **版本冲突**（乐观锁失败，见下） |
| `40902` | 409 | 运行状态冲突（如对已结束的 run 发 resolve） |
| `41001` | 410 | SSE 事件缓冲已丢失（断线过久或进程重启，前端应重拉消息树） |
| `42201` | 422 | **显式命名校验失败**（空名/默认名/纯序号名） |
| `42202` | 422 | 大纲解析失败（模板结构被破坏） |
| `50001` | 500 | 服务端内部错误 |
| `50201` | 502 | LLM Provider 调用失败（含降级链耗尽） |
| `50301` | 503 | 依赖不可用（calibre 缺失、MCP 探测失败、FTS5 不可用等） |
- **正文写入乐观锁**（防「自动保存」与「Agent 写同一章」互相覆盖）：
  `PUT /chapters/{cid}/content` 请求体必须携带读取时拿到的 `base_version`；服务端比对文件 frontmatter 的 `version`，不一致返回 **409 + `code=40901`**，`data` 内含服务端最新 `content / version / updated_at / last_writer(manual|agent)`；前端弹 `ConflictBanner` 让用户选择「查看 diff 合并」或「强制覆盖」（强制覆盖需再次带最新 `base_version`）。写入成功则 `version += 1` 并按策略快照 `chapter_revisions`。
- **SSE 契约**：事件信封 `{event, run_id, session_id, seq, ts, data}`，`seq` 每 run 内单调递增；事件类型与 `data` 字段见「二、(2)」事件表。

  - **续传**：服务端为每个 run 维护**内存环形缓冲（默认最近 500 事件，可配）**，客户端断线重连带 `Last-Event-ID: {seq}`，服务端从缓冲补发；
  - **缓冲溢出或进程重启后不可续传**：服务端返回 `error` 事件（`code=41001`, `message="stream buffer lost"`）并关闭流，前端**回落为重新拉取 `GET /agent/sessions/{sid}/messages`** 重建消息列表；
  - `POST messages` 返回 202 后，连接前产生的事件由环形缓冲补齐（先建流再发消息也可，两种顺序都支持）。
- **项目管理 API 定位**：「项目 / 卷章 / 大纲检索与读写」是卷章增删改命名及大纲维护等写作业务操作的**唯一入口**（前端调用，服务层做显式命名校验、乐观锁与派生索引同步）；Agent **不通过工具调用这些接口**，而是用内置文件工具直接读写工作区文件。两条写入路径都收敛到 `WorkspaceGuard` + 派生索引同步；卷章 `sort_order` 则只能经 `sync_order_from_outline` 派生写入，不存在第三套顺序入口。

## 七、分阶段实施路线

> 依赖链为**线性**：`M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7`（各里程碑内部「后端接口」与「前端页面」可并行）。中断与恢复基础机制在 **M3**，M4 的咨询卡片/`awaiting_consult` 恢复在其上叠加。

| 阶段 | 目标 |
| --- | --- |
| **M0** | 脚手架与地基：`pyproject` 改造（`uv_build` 配置 + `writer` CLI 入口，见「八、③」）、**新增根 `.gitignore`**（`data/ .env .venv/ __pycache__/ src/web/node_modules/ src/web/dist/` 等）、`uv sync`、`.vscode/settings.json` 固定 `python.defaultInterpreterPath` 到 `.venv`、FastAPI 骨架（统一响应/异常/错误码/日志/CORS/lifespan）、SQLite + Alembic 初始迁移、**FTS5 两步探测**、工作区目录结构约定、JSONL 日志配置、前端构建链引入（Vite+TS，按「三、(1)」迁移步骤接管现有 vendor/importmap 骨架，验证后删 vendor/）+ Element Plus + axios + SSE 客户端、三栏布局空壳、`.env.example`、**deepagents 0.7.23 API spike**（把「设计依据」表逐条在真实装配下验证并回写） |
| **M1** | 小说基础功能：`novels` 登记表 + 项目 CRUD（`novel.yaml` + `volumes.yaml` 文件读写，**创建必须显式命名**、初始化大纲模板文件、生成 `project_id`）、卷/章 CRUD（**卷/章名必填**，短 ID 永不变；**顺序只按创建先后追加，无重排接口/拖拽/跨卷移动**）、正文读写**乐观锁**（`base_version` + 409）、阅读模式（目录顶端「大纲」节点可阅读、`reading_progress` 进度记忆）、编辑模式（Tiptap + 自动保存 + 字数统计 + 卷章树（只读顺序）+ `ConflictBanner`；大纲经目录节点 OutlineEditor 编辑）、通用设置、`chapter_revisions` 快照、**zip 导出**（markdown/epub/mobi/txt + 大纲原 md；EPUB 结构校验；calibre 探测，未装则置灰 mobi） |
| **M2** | 审计与通用设置：审计日志埋点（SQLite `audit_logs` + JSONL 备份）与只读查询接口、通用设置页（主题/字号/字体/页宽/自动保存间隔/回落城市）。**（AI 工具配置页移到 M3，与 Provider API 同批交付，避免配置页早于其后端）** |
| **M3** | Agent 核 v1（端到端打通）+ **中断恢复基础设施**：LLM Provider/Model 管理（密钥走 `.env` 读写 + 掩码展示）+ 连接测试 + 降级链（`ModelFallbackMiddleware`）；韧性中间件装配（`ModelRetry/ModelCallLimit`）；deepagents 工厂（`CompositeBackend` 路由、**8 条有序 permissions** 半隔离、`AsyncSqliteSaver`、`WorkspaceGuard`、`context_schema`）；Agent 内置工具（`ls/read_file/write_file/edit_file/delete/glob/grep` + `web_search`）；自定义中间件（`RecapInjection/WorkspaceGuard/FileChangeNotify/WriteApproval`）+ 显式 `TodoListMiddleware`；**中断与恢复基础机制**（`agent_interrupts` 表 + `interrupts/{id}/resolve` API + `interrupt_on`/`WriteApprovalMiddleware` 接线 + checkpoint 续跑）；SSE 流式（含 `file.changed`/`plan.update`/环形缓冲续传）+ 右侧会话面板 + 审批卡片；消息树与运行日志（SQLite）；Langfuse v5 追踪（langchain CallbackHandler）；**AI 工具配置页** |
| **M4** | 大纲与门禁：大纲单文件结构 + `parse_outline`（**严格 H1/H2 判定** + 卷/章名**归一化**匹配）+ **`sync_order_from_outline`（★卷章 `sort_order` 唯一写入口）** + **四阶段门禁（G0 存在性→G1 衔接预检→G2 生成→G3 匹配后检，G0/G1/G3 服务层直连 LLM，G2 进 writer 子代理）** + **咨询卡片（ConsultCard，复用 M3 中断机制，`type=consult`）**与目录「大纲」节点（OutlineReader/OutlineEditor + validate 警告条）+ FTS5 派生索引（**二元切分 + unicode61**）与检索 API + 章节摘要自动生成 + `build_recap` 注入 + 上下文压缩与 token 预算 |
| **M5** | 子代理 / 技能 / HITL 完善：写作子代理与管理 UI（planner/writer/editor/continuity-checker/researcher + general-purpose）、内置与用户自建技能（`skill_configs` 物化到 `data/skills/`，经 `/skills/user/` 路由加载）、审批 UI 完善（编辑后批准/拒绝/回复补充）、子代理并行流与泳道展示、门禁判定 Langfuse 复盘看板 |
| **M6** | MCP 与动态组件：内置 Open-Meteo 天气 MCP（`MCPServer`）+ `services/weather.py` + MCP 管理 UI（第三方 MCP 经 `langchain-mcp-adapters` 接入；stdio 型白名单 + 二次确认 + 审计）、左上角动态组件（宠物**两态**动画 + 浏览器定位城市 + 天气；**无「今日统计」**） |
| **M7** | 打磨与交付：Dockerfile + docker-compose、性能优化、错误边界与空态、端到端验收清单、测试补齐（见「十一」）、文档定稿（**同步修订 README 消除登录/后台差异**） |

## 八、依赖清单

**① 已在 `.venv` 中（只需在 `pyproject.toml` 显式声明为直接依赖，防止传递依赖被上游裁剪）**：

| 包 | 实测版本 | 来源 |
| --- | --- | --- |
| `deepagents` | 0.7.23 | 直接依赖 |
| `langchain` | 1.4.7 | deepagents 依赖 |
| `langchain-core` | 1.7.0 | 传递 |
| `langchain-openai` | 1.5.1 | 直接依赖 |
| `langchain-anthropic` | 1.7.6 | deepagents 依赖 |
| `langchain-google-genai` | 4.4.2 | deepagents 依赖 |
| `langgraph` | 1.2.16 | 传递 |
| `langfuse` | 5.0.4 | 直接依赖 |
| `mcp` | 2.3.0 | 传递 |
| `httpx` | 0.28.1 | 传递 |
| `pyyaml` | 6.0.3 | 传递 |
| `sse-starlette` | 3.5.0 | 传递 |
| `python-multipart` | 0.0.32 | 传递 |
| `starlette` / `uvicorn` | 1.9.0 / 0.54.0 | 传递 |
| `pydantic` | 2.14.1 | 传递 |
| `tiktoken` | 0.14.0 | 传递（可用于精确 token 计数） |
| `tenacity` | 9.1.4 | 传递 |
| `python-dotenv` | 1.2.4 | 直接依赖 |

**② 需新增（实测确认缺失，`uv add` 后更新 `uv.lock`）**：

- Web/DB：`fastapi`、`uvicorn[standard]`、`sqlalchemy[asyncio]`、`aiosqlite`、`alembic`、`pydantic-settings`
- Agent 持久化与集成：`langgraph-checkpoint-sqlite`（`AsyncSqliteSaver`）、**`langchain-mcp-adapters`**（MCP → LangChain tool，deepagents 不内建；若与 mcp 2.x 不兼容则按「二、(3)」回落自封装）
- 业务：`jinja2`（提示词模板）、`python-frontmatter`（章节/摘要 YAML frontmatter）、`structlog`（JSONL 结构化日志）
- 开发/测试：`pytest`、`pytest-asyncio`、`ruff`

**③ 构建配置**：**不换 `hatchling`**。`app` 是合法 Python 包名，`uv_build` 只需显式声明模块位置（已实测：按下述配置 `uv build` 产出的 wheel 正确包含 `app/` 与 entry point）：

```toml
[build-system]
requires = ["uv_build>=0.12.16,<0.13.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-root = "src"
module-name = "app"

[project.scripts]
writer = "app.cli:main"   # 现有 ai_agent_writer_pro:main 指向不存在模块，M0 替换
```

**④ 不需要**：`cryptography`（API Key 只存环境变量，无需 Fernet 加解密）。
**⑤ 保留但不使用**：`lmnr`（可观测性改用 Langfuse）、`claude-agent-sdk`（Agent 引擎统一用 deepagents，Anthropic 走 `langchain-anthropic`）——虚拟环境中无需卸载，本项目开发不使用；`demo-claude-agent.py` / `demo-laminar.py` 仅作原理参考。
**⑥ 前端**：`vue@3`、`vite`、`typescript`、`pinia`、`vue-router`、`element-plus`、`axios`、`@tiptap/vue-3` + `starter-kit` + `placeholder` + `character-count` + `highlight`、`markdown-it`、`dompurify`、`lottie-web`、`dayjs`、`sass`。`vue / vue-router / pinia` 现以 esm-browser 发行版存于 `src/web/vendor/`（importmap 无构建运行），M0 引入 Vite 后改为 npm 依赖并删除 `vendor/`（详见「三、(1)」）。本机 Node `v24.15.0` / npm `11.12.1`，满足 Vite 要求。

## 九、风险与对策

| 风险 | 对策 |
| --- | --- |
| deepagents 版本 API 细节差异（文档与实装不完全一致） | M0 先做最小 spike：在真实装配下验证 `create_deep_agent` 的 `backend / permissions / checkpointer / subagents / skills / interrupt_on / context_schema` 组合行为与 `astream(subgraphs=True)` 事件形状，以及 Qwen 无 harness profile 时的提示词/工具裁剪表现；结论回写「设计依据」表后再定型 `builder.py` |
| **mcp 2.x 破坏性变更**（`mcp.server.fastmcp` 已移除，更名 `MCPServer`） | 内置天气服务一律用 `mcp.server.mcpserver.MCPServer`；`agent/mcp/` 加薄适配层隔离版本差异；若必须用 1.x API 则显式 pin `mcp<2` 并同步改依赖清单 |
| **`langchain-mcp-adapters` 与 mcp 2.x 兼容性未知**（当前未安装） | M0 安装后立即做兼容性 spike；不兼容则回落「用 mcp 官方 client 自封装 `@tool`」（接口对上层不变） |
| **权限规则默认放行**（无规则匹配时 `_check_fs_permission` 返回 `allow`） | 8 条规则**顺序固定**、兜底 `deny /**` 必须最后；以常量集中定义并加注释；每条规则配套越权单元测试 + 顺序回归测试 |
| **`/skills/**` 漏开 read 会导致技能全部不可用**（技能靠 `read_file` 渐进披露） | 权限清单已含第 4 条 read allow；M5 加集成测试：断言 Agent 能读到内置技能 `SKILL.md` 全文 |
| **stdio 型第三方 MCP 等价于受限的任意命令执行**（与「不开放 execute」原则冲突） | 可执行文件白名单（默认仅 `npx / uvx / python`）+ 用户二次确认（`mcp_servers.confirmed_at`）+ 命令全文落审计；默认不预置任何第三方 stdio MCP |
| `permissions` 与沙箱 backend 不兼容会直接抛 `ValueError`（`FilesystemMiddleware.__init__` 校验） | 禁用 `LocalShellBackend` / `LangSmithSandbox`；只用 `FilesystemBackend` + `CompositeBackend`（`execute` 因 `supports_execution()==False` 自动对模型隐藏） |
| FTS5 中文检索效果差（`trigram` 对 2 字词零命中） | **二元切分 + `unicode61`** 为主（索引/查询两侧同一函数）；`trigram` 仅 ≥3 字可选补充；`LIKE` 降级（`data.degraded=true`） |
| Agent 改文件与派生索引不一致 | 文件为内容事实源；写入后**同步**更新派生索引，失败标 `project_index.dirty` 并告警；`writer rebuild-index` / `POST /novels/{id}/index/rebuild` 全量重建 |
| 跨项目只读被绕过（`permissions` 不覆盖自定义工具/MCP） | Agent 不注册碰路径的自定义工具（只有 `web_search`）；服务层文件写入统一经 `WorkspaceGuard`（resolve 归一化 + 根目录约束 + 符号链接检查 + 跨项目拒绝）+ 越权单元测试；第三方 MCP 仅接免认证开源服务 |
| **自动保存与 Agent 写同一章互相覆盖** | 正文乐观锁（`base_version` + 409/`40901`）+ `ConflictBanner` 合并选择；Agent 写入前快照 `chapter_revisions`；`file.changed` 驱动前端提示「本章已被 AI 修改」 |
| 长篇上下文爆炸与成本 | token 预算 + 前情提要 + `DeltaChannel` 增量 checkpoint + summarization 驱逐卸载 + 子代理上下文隔离 + `ModelCallLimitMiddleware` + Langfuse 用量看板 |
| SSE 断线丢事件 | `seq` + `Last-Event-ID` 续传 + 服务端环形缓冲（默认 500 事件）；缓冲丢失/进程重启返回 `41001`，前端回落重拉消息树 |
| Windows 路径 / 编码 | 统一 `pathlib` + 显式 `encoding="utf-8"`；路径解析集中在 `core/paths.py`；`WorkspaceGuard` 拒绝含 `..` 的路径 |
| **解释器漂移**（系统 `python` 为 Anaconda 3.12.14，与 `.venv` 同版本但不同发行版） | 一律 `uv sync` / `uv run`；`.vscode/settings.json` 固定 `python.defaultInterpreterPath` 指向 `.venv`；文档与 CI 明示 |
| UI 保存密钥需写回 `.env`，有并发写/格式破坏风险 | 「读全文 → 替换/追加白名单键 → 临时文件 + `os.replace` 原子写 → 备份 `.env.bak`」+ 进程内互斥锁；`.gitignore` 在 M0 新增（仓库根目前没有），确保 `.env` / `data/` 不入库 |
| 门禁误报/死循环（预检过严卡住流程、后检反复重生成） | 重试上限（后检 ≤2 次，每次重生前快照）+ 咨询卡片兜底 + G1「显式确认跳过」（落审计）；预检/后检提示词独立版本化调优；判定（理由+分数）写 Langfuse observation/score；`gate_result_json` 留存便于回归 |
| 大纲质量决定生成质量（简介过粗/缺失导致正文跑偏） | G0 强制存在性门禁；咨询卡片引导补全；内置 skill `novel-planning` 提供模板并可代写大纲；OutlineEditor 支持预览与 validate 校验 |
| **大纲解析正则误判**（宽松正则会把 `## 第三章 卷土重来` 当卷节） | 严格层级判定 `^#(?!#)\s` / `^##(?!#)\s`；附录节下的 H2 不参与章匹配；解析加回归测试（含该陷阱用例） |
| 小说重命名与大纲文件名耦合（`{小说名称}-大纲.md` 随 title 变化） | 重命名事务内同步重命名 outline 文件，并更新 `novel.yaml.title/outline_file` 与 `novels.title/outline_file`（DB 值是文件值的缓存）；任一步失败整体回滚；`outline_file` 为唯一寻址依据 |
| mobi 无轻量自研生成方案（本机未安装 calibre） | EPUB 产物经**可选外部工具** calibre `ebook-convert` 转换；启动时探测（带超时），未检测到则置灰 mobi 并提示安装（`50301`）；txt 走自研拼接，不依赖外部工具 |
| 大纲格式被用户写坏（标题层级/卷章命名不符模板） | `GET /novels/{id}/outline/validate` 校验（含错误行号）+ 编辑页警告条（不阻断保存）+ G0 咨询卡片兜底；模板与 `novel-planning` 技能提供格式参照；匹配以**归一化后名称完全一致**为准（序数词可省略） |
| **卷章顺序双源漂移**（大纲小节先后 vs `sort_order`；手工重排带来批量 frontmatter 写入与乐观锁冲突） | **顺序只由大纲派生**：唯一写入口 `sync_order_from_outline`；前端无拖拽/上移下移，后端无 `reorder`/`move`，章不可跨卷移动；回填不产生修订快照、不递增 `version`；`validate` 对序数与顺序冲突给警告；配套「卷章顺序派生」测试域 |
| **浏览器定位受限**（需 HTTPS 或 localhost，用户可拒绝授权） | 定位为可选增强：拒绝/失败/超时 → 回落 `user_settings.city` → 再失败则组件只显示宠物与占位天气；坐标不入库、不写审计 |
| **Langfuse v5 与仓库 demo 写法不兼容**（无 `Langfuse.trace`） | 按「三、(1) 可观测」的 v5 API 实现（`langfuse.langchain.CallbackHandler` + `start_as_current_observation` + `create_score`）；不照抄 `demo-langfuse.py`；未配 Key 时全链路 no-op 降级 |
| `demo/` 脚本与实装版本漂移，误导后续开发 | 「十」逐条标注 demo 可用性与版本差异；实现时以本手册与实测结论为准，demo 仅作原理参考 |

## 十、参考资料与仓库现状

**仓库现状**（起步基线）：

- **前端已有无构建骨架**：`src/web/index.html`（importmap）+ `vendor/`（vue 3.5.44 / vue-router 5.4.0 / pinia 4.1.0）+ `main.js`、`App.js`、`router.js`、`stores/app.js`、`views/{HomeView,AboutView}.js`、`style.css`（hash 路由）；M0 按「三、(1)」迁移到 Vite + TS 并删 `vendor/`。
- **后端未起步**：`src/app/` 不存在，M0 开始搭建。
- **依赖锁定现状**：`uv.lock` 已含「八、①」全部包；**尚缺**「八、②」清单（fastapi / sqlalchemy / aiosqlite / alembic / pydantic-settings / structlog / jinja2 / python-frontmatter / langgraph-checkpoint-sqlite / langchain-mcp-adapters + 测试依赖）。
- **环境实测**：`.venv` Python 3.12.14（uv 管理）；系统 `python` = Anaconda 3.12.14（注意解释器漂移）；SQLite 3.53.4（FTS5 可用，`unicode61` / `trigram` / `porter` 均可建表，`bm25()` 可用）；Node v24.15.0 / npm 11.12.1；uv 0.12.16；calibre 未安装（mobi 导出默认置灰）。
- **`demo/` 脚本可用性**（防止照抄踩坑）：`demo.py`（原生 Agent-Loop 原理，可参考）· `demo-deepagents.py`（`create_deep_agent(model, tools, system_prompt)` + `invoke`，与 0.7.23 兼容，可作 M0 spike 起点）· `demo-langfuse.py`（**v2 写法 `langfuse.trace()`，与已装 5.0.4 不兼容，M3 须按「三、(1)」的 v5 API 重写**）· `demo-claude-agent.py` / `demo-laminar.py`（本项目不采用的路线，仅原理参考）。
- **已知仓库缺陷（M0 待办）**：① 仓库根**没有 `.gitignore`**（仅 uv 自动生成的 `.venv/.gitignore`），`.env` / `data/` 有入库风险，M0 必建；② `pyproject.toml` 的 `[project.scripts]` 指向不存在的 `ai_agent_writer_pro:main`（当前 `uv_build` 产出空包），M0 按「八、③」替换；③ `.vscode/settings.json` 只设了 env manager，未固定 `python.defaultInterpreterPath`；④ `requirements.txt` 与 `pyproject.toml` 双份依赖清单并存（内容一致），统一以 `pyproject + uv.lock` 为准，M0 决定去留；⑤ `README.md` 的「用户登陆/前后台」表述与本手册冲突（单用户、无鉴权、无后台管理端），M7 修订。
- `doc/web参考.jpg` 为三栏布局参考（左：宠物+城市天气 / 新建删除小说 / 项目列表 / 左下设置与 AI 工具；中：阅读+编辑模式 / 目录 / 正文；右：Agent 面板），已与「三、(3)」逐区核对一致；`doc/Writer-Develop.md` 为早期纲要（两问两答结构），结论已并入本手册，**冲突时以本手册为准**，M7 定稿时同步精简。

**参考资料**：

- Deep Agents 官方文档：overview / backends / permissions / subagents / skills / human-in-the-loop / streaming（部分行为标注需 ≥0.7.3，与实装 0.7.23 有细节差异，一律以「设计依据」表的实测结论为准）
- langchain 1.x middleware：`ModelRetryMiddleware` / `ModelFallbackMiddleware` / `ModelCallLimitMiddleware` / `TodoListMiddleware` / `HumanInTheLoopMiddleware`（`InterruptOnConfig`）
- Open-Meteo（免费开源天气 API 含 Geocoding；**M6 需实测中国城市覆盖与中文返回**）、MCP 规范与 mcp 2.x 迁移指南（FastMCP→MCPServer）、Agent Skills 规范
- Langfuse v5 文档（OTel 版 SDK：observations / scores / langchain CallbackHandler）

## 十一、测试与验收策略

**框架**：`pytest` + `pytest-asyncio`；API 层用 `httpx.ASGITransport` 直连 FastAPI app（不起真实端口）；Agent 相关测试用**假模型**（langchain `GenericFakeChatModel` 或自建 stub ChatModel）驱动，不打真实 LLM；文件类测试用 `tmp_path` 构造临时工作区。代码风格用 `ruff`（与 deepagents/langchain 同工具链）。

**必测清单**（风险表中承诺的测试都落在这里）：

| 域 | 用例 |
| --- | --- |
| 权限矩阵 | 「二、(5)」8 条规则**逐条**越权用例：当前项目读写通过；其它项目写被拒（含 `write_file/edit_file/delete` 三个工具）；其它项目读通过；`/skills/**` 读通过、写被拒；`/**` 兜底读/写均被拒；**规则顺序回归**（断言常量数组与文档顺序一致） |
| WorkspaceGuard | `..` 路径穿越、绝对路径逃逸、符号链接逃逸（Windows 下用 junction 替代场景标注 skip）、跨项目写、非授权写 `summaries/` |
| 大纲解析 | 严格层级判定回归（含陷阱用例：`## 第三章 卷土重来` 不得判为卷节）；附录 H2 不参与章匹配；归一化比较（全角空格/序数词省略/标点差异视为同名）；序数与 sort_order 冲突告警；缺失小节 vs 简介为空两类缺失的区分 |
| 卷章顺序派生 | `sync_order_from_outline`：调换大纲 H1/H2 先后 → `volumes.yaml` 与各章 frontmatter 的 `sort_order` 按新顺序回填、**ID/文件名不变**；顺序回填**不产生 `chapter_revisions` 快照、不改 `version`**；大纲未登记的卷/章追加到该卷末尾且出「未登记」警告；**无 reorder/move 接口可调**（断言路由不存在）；章跨卷移动无入口 |
| FTS5 | 二元切分往返（索引侧与查询侧同一函数）；2 字词命中（trigram 做不到的场景）；`bm25` 排序；FTS5 不可用时 `LIKE` 降级且 `data.degraded=true`；`story_fts` 的 `project_id` 过滤（跨项目不串数据） |
| 乐观锁 | `PUT content` 带过期 `base_version` → 409/`40901` 且回传最新内容；带最新值 → 成功且 `version+1` |
| 修订快照 | 手动保存/Agent 写入/定稿三种触发各产生快照；自动保存不产生；超过保留上限时清理最旧非定稿快照；restore 回滚后内容与 `version` 正确 |
| 显式命名 | 空名/「未命名小说」/「第 1 卷」/「第 1 章」/纯序号名 → 42201；合法名通过 |
| EPUB | `mimetype` 为 zip 首条目且 STORE 不压缩；`META-INF/container.xml`、`OEBPS/content.opf`、`nav.xhtml` 存在且 XML 合法；章节顺序与卷章树（按 `sort_order`）一致；txt 拼接顺序正确 |
| 门禁 | G0 两类缺失各出对应咨询卡片；G1 带上一卷末章锚点；G3 不匹配重生成 ≤2 次后出咨询卡片；每次重生成前有新快照；G1 显式跳过落审计 |
| SSE | `seq` 单调递增；`Last-Event-ID` 续传补齐；缓冲丢失返回 `41001` |
| 中断/咨询 | approval 四种 decision 各自恢复语义正确；consult 的 `option/text` 恢复语义正确；对已结束 run 的 resolve → 40902 |
| 导出/软删除 | zip 内含正文（按 format）+ 大纲原 md；删除后项目移入 `data/trash/` 且列表不可见 |

**每里程碑验收口径**：M1 完成「建小说→建卷章→编辑→阅读→导出」全链路手工验收 + 上述静态用例；M3 完成「配 Provider→发消息→SSE 流式→审批→取消」端到端（假模型）；M4 完成「无大纲→咨询→补大纲→G1→G2→G3→摘要」端到端（假模型）；M7 出端到端验收清单文档。

## 十二、运行手册

**启动**（开发环境，Windows PowerShell）：

```powershell
uv sync                                  # 安装依赖（含 M0 新增项）
uv run writer migrate                    # 封装 alembic upgrade head
uv run writer dev                        # = uvicorn app.main:app --reload（默认 127.0.0.1:8000）
cd src/web; npm install; npm run dev     # M0 迁移后；迁移前可用 python -m http.server 直开 vendor 版
```

**数据目录与备份**：`data/app.db`（业务库）、`data/agent_state.db`（LangGraph checkpoint）、`data/workspaces/`（小说内容，**最重要**）、`data/skills/`、`data/exports/`、`data/trash/`、`data/logs/*.jsonl`。备份 = 停服后整目录复制 `data/`；恢复 `app.db` 与文件不一致时用 `uv run writer rebuild-index` 从工作区重建派生数据（`novels` 登记表同理可扫描重建）。

**`.env` 键清单**（`.env.example` 同步维护）：

```
APP_PORT=8000
DATA_ROOT=./data
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

**运维操作**：配置热重载 `POST /api/v1/config/reload`；索引重建 `POST /api/v1/novels/{id}/index/rebuild` 或 CLI `writer rebuild-index`；摘要重建 `POST /api/v1/novels/{id}/summaries/rebuild`；日志按日期轮转（`data/logs/app-*.jsonl` / `error-*.jsonl` / `audit-*.jsonl`），审计同时入 SQLite。
