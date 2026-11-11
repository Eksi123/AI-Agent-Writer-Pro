# AI Agent Writer Pro —— Agent 核心设计

> 本文从 `Project-Develop.md` 中提取，聚焦 **Agent 核心设计**：Agent 主体设计、工具设计、
> Harness 设计、Agent 核心与外部的接口交互。面向需要理解或修改 Agent 行为的工程师。

---

## 目录

- [一、Agent Harness 设计哲学](#一agent-harness-设计哲学)
- [二、Agent 主体设计](#二agent-主体设计)
- [三、工具设计](#三工具设计)
- [四、Harness 安全体系](#四harness-安全体系)
- [五、子代理与技能](#五子代理与技能)
- [六、流程编排](#六流程编排)
- [七、上下文治理](#七上下文治理)
- [八、Agent 与外部的接口交互](#八agent-与外部的接口交互)

---

## 一、Agent Harness 设计哲学

### 1.1 什么是 Agent Harness

「Harness」原意是**马具**——套在马身上的那副挽具。马有力量，但往哪走、走多快、能不能踩进麦田，
由挽具决定。

```
大模型（LLM）      = 马       有强大的语言能力，但没有边界感、没有记忆、不会自己干活
Agent Harness      = 挽具     决定它能用哪些工具、看到哪些文件、什么时候必须停下来问人
Agent 应用         = 马车     挽具 + 马 + 车身，才是能载客的完整交通工具
```

### 1.2 为什么不自研 Harness

本项目选择 **deepagents**（构建在 LangGraph 之上），理由：

- 框架能力（Agent-Loop / 沙箱 / 权限 / 子代理 / 技能 / 压缩 / 中断恢复）已经过大规模验证；
- 自研的维护面会迅速超过业务本身；
- 本项目真正的价值在**写作领域**，不在通用 Agent 基建。

于是职责被清晰切成三层：

```
┌─────────────────────────────────────────────┐
│  ③ 本项目：写作领域化封装                     │
│     流程 S0→S3 / 前情提要 / 章节摘要          │
│     消息树分支 / 8 条权限规则 / 自定义中间件   │
├─────────────────────────────────────────────┤
│  ② deepagents：Agent Harness                 │
│     create_deep_agent / 沙箱 backend          │
│     permissions / subagents / skills          │
│     HarnessProfile（模型适配）                │
├─────────────────────────────────────────────┤
│  ① LangGraph + langchain：图执行与模型抽象     │
│     astream / checkpointer / interrupt        │
│     AgentMiddleware 钩子                      │
└─────────────────────────────────────────────┘
```

**本项目只做 ③。** 判断标准：*它是在做「写作」的事，还是在做「通用 Agent」的事？*

### 1.3 一句话总结

```
把「通用能力」交给框架（deepagents Harness）
把「领域规则」做成确定性代码（流程、顺序、命名、乐观锁）
把「模糊判断」留给模型（写正文、查连贯、拟大纲）
把「不可逆动作」交给人（审批、咨询、快照回滚）
```

---

## 二、Agent 主体设计

### 2.1 目录组织（7 个顶层目录）

```
agent/
├── loop/          运行核心：装配/运行/流式/上下文/LLM 调用/提示词/中间件/checkpoint
├── subagents/     子代理：builtin/{planner,writer,reviewer,web_searcher,recaller}/ + user/
├── tools/         工具与技能：builtin/（web_search/outline_grep/ask_user/create_structure）
│                  + registry.py（装配清单）+ users/（用户技能装载）
├── workflows/     写作固定流程：writing_steps/ + book_consistency/ + novel_export/
├── harness/       安全措施：sandbox/ + llm/ + approval/ + guard/ + lock/
├── tracing/       运行消息与日志：sse/ + messages/
└── more/          其他：mcp/（天气 MCP + 第三方适配）+ langfuse/（观测）
```

### 2.2 Agent 装配（`loop/builder.py`）

`build_agent()` 调用 `create_deep_agent()` 装配完整 Agent，核心参数：

| 参数              | 本项目的值                                                                 |
| ----------------- | -------------------------------------------------------------------------- |
| `model`         | 用户配置的模型（可多级降级，`resolve_models()` 预解析 ChatModel 实例）   |
| `backend`       | `CompositeBackend`：3 条虚拟路径路由                                     |
| `permissions`   | 8 条有序规则，兜底 `deny /**`                                            |
| `tools`         | `web_search` / `outline_grep` / `ask_user` / 6 个结构工具 + 天气 MCP |
| `skills`        | `["/skills/user/"]` + 会话精选 `/skills/session-{sid}/`                |
| `subagents`     | 5 个内置子代理（planner/writer/reviewer/web-searcher/recaller）            |
| `middleware`    | 17 层固定顺序（详见 2.3）                                                  |
| `interrupt_on`  | `{}`（置空，风险确认由 WriteApprovalMiddleware 按参数判定）              |
| `checkpointer`  | `AsyncSqliteSaver`（独立库 `agent_state.db`）                          |
| `system_prompt` | Jinja2 渲染的项目元信息 + 铁律                                             |

### 2.3 中间件栈（17 层固定顺序）

中间件的顺序不是随便排的，前一层是后一层的前提：

```
⓪ WriteApproval        ★最外层：参数级高危写入 → interrupt（harness/approval）
① ModelCallBudget      调用预算：超限先总结再 consult 询问继续/结束（harness/llm/call_budget）
② ModelRetry           模型超时/429/5xx/连接错误退避重试 2 次         ┐
③ ModelSwitchOnFailure 当前模型连续失败达阈值（3次）才切换备用模型    ┘ harness/llm/resilience
④ TodoList             给模型 write_todos 工具（计划可见）
⑤ RecapInjection       注入前情提要 + 预算检查（请求级，不回写 state）
⑥ HiddenContext        注入 run 级隐藏上下文（选区润色提示词）
⑦ ContextGuard         上下文两层保险第二层：provider 未命中时检查用户是否有明确获取指令
⑧ PairingRepair        工具消息配对自愈（多并行 tool call 错位修复）
⑨ WastefulLoopGuard    浪费型循环分级熔断（SOFT=3 次/HARD=5 次/工具层硬阻断）
⑩ SteerInjection       注入运行中追加的引导消息
⑪ ReadScopeGuard       大纲/章节正文的读取授权守卫（harness/guard/read_scope）
⑫ WorkspaceGuard       ★路径校验 / 审计 / tool 事件 / 错误指纹阻断（harness/guard）
⑬ FileChangeNotify     文件变了 → 通知前端热更新 + 同步索引
⑭ ContentLock          写章正文时加 AI 编辑锁（harness/lock）
⑮ PromptCache          显式前缀缓存断点（DashScope/Anthropic 折扣计费，最内层）
```

此外还有 3 个辅助中间件按需装配：

- `ContextOverflowGuardMiddleware`：设定 Context Length > 模型真实窗口时的兜底
- `ContextWindowSyncMiddleware`：上下文窗口配置实时同步（压缩阈值动态跟随）
- `SafeDeleteMiddleware`：把 Agent 的 delete 从永久物理删除改为可恢复删除（走回收站）

**分组逻辑**：

- ②③ 是**韧性层**（模型与成本）
- ④⑤⑥⑦⑧⑨⑩ 是**上下文与行为治理层**
- ⓪①⑪⑫⑬⑭⑮ 是**安全与协作层**

**langchain 中间件钩子约束**：`AgentMiddleware` **只有** `before_agent`/`after_agent`、
`before_model`/`after_model`、`wrap_model_call`、`wrap_tool_call`（各带 `a*` 异步版）。
★**没有** `before_tool` / `after_tool` / `on_node_end` / `on_run_end`。这直接决定了：

1. 工具级校验/审计/通知全部落在 `awrap_tool_call`；
2. **运行收尾不在中间件里做**，由 `loop/lifecycle.py:finalize()` 在 `astream` 结束后统一执行。

### 2.4 Agent 运行生命周期（`loop/runner.py`）

`RunManager` 进程内单例，持 `_tasks` / `_cancel_flags` / `_locks` / `_sessions` 四个字典。

- **`start()`**：用户消息入消息树 → 建 `agent_runs(pending)` → 会话置 running → `sse_bus.open()` → `_spawn()` → 返回 run_id
- **`resume()`**：校验 run 非终态 → `sse_bus.reopen()` → `_spawn(resume_value=...)`
- **`cancel()`**：置取消标志 → 给 3 秒优雅窗口 → 超时硬 cancel → run 置 cancelled
- **`_spawn()`**：per-run `asyncio.Lock` 串行化（防 resolve 与进行中的 run 并发写同一 thread）

**全局单飞**：同一时刻只允许一个会话处于「AI 思考执行中」——其它会话发消息时
`run_manager.start()` 返回 None（不起 run），把用户消息与固定提示回复
（`GLOBAL_BUSY_REPLY`）一并落消息树，接口仍回 202 但 `data.run_id=null` + `data.rejected="global_busy"`。

### 2.5 流式事件映射（`loop/streaming.py`）

**双 stream_mode**：`["updates","messages"]` —— `messages` 拿 token 级增量，`updates` 拿节点完成与中断。

**子代理泳道**：`subgraphs=True` 拿子代理事件，用 `namespace` 区分泳道。新 namespace 首次出现发
`subagent.start`，顶层 `tools` 节点完成时对所有活跃子代理发 `subagent.end`。

**消息树落库**：只在 astream 收尾（正常结束/中断暂停）做一次 force 同步，中途思考/工具消息不逐次持久化。

### 2.6 中断与恢复（HITL）

借助 LangGraph 的 `interrupt()` + `AsyncSqliteSaver` 把中断**持久化**。

审批支持**四种决策**：

| 决策        | 语义                                                       |
| ----------- | ---------------------------------------------------------- |
| `approve` | 原样执行                                                   |
| `edit`    | **改参数后执行**（如把「覆盖全章」改成「只改这段」） |
| `reject`  | 不执行，告诉模型「用户拒绝了」                             |
| `respond` | 不执行，把用户的补充说明回给模型                           |

**Resume 失败关闭（fail-closed）**：本次决定对不上任何挂起中断时，若库里还有另一张未决卡
正对应某个挂起中断，则绝不把这个决定投递给它——保持挂起，用户仍会看到那张真正待批的卡。

### 2.7 Harness Profile 的有意留白

deepagents 提供 HarnessProfile 机制按模型定制调教方案。**本项目刻意不注册任何 HarnessProfile**：

1. **匹配不上**：项目传已构建的 ChatModel 实例，框架无法精确匹配 profile；
2. **不该在应用层替模型做主**：项目已用十余层中间件精确控制运行行为。

正确的适配途径是在中间件栈里加层，而不是注册 profile。

---

## 三、工具设计

### 3.1 设计原则

**边界约定**：**写作业务函数原则上不作为 Agent 工具**（下沉为 REST 接口），
Agent 对小说**正文**的操作一律通过 deepagents 内置文件工具完成。

**有意收窄的例外**：卷/章的**结构操作**（建/改名/删除）必须是工具，因为：

- 建卷需要处理 `volumes.yaml` 的 id 分配、sort_order 顺延，Agent 用文件工具手搓必然出错；
- 改名需要 DB + 文件 + 大纲三处同步，只改 frontmatter 会导致目录树与正文标题不一致；
- 删除需要回收站归档、修订快照清理、卷内重编号，直接 unlink 会绕过一切保护。

### 3.2 自定义工具清单（9 个）

| 工具               | 模块                                  | 职责                                                                       | 权限分配                       |
| ------------------ | ------------------------------------- | -------------------------------------------------------------------------- | ------------------------------ |
| `web_search`     | `tools/builtin/web_search.py`       | 免费双源检索（Wikipedia API + DuckDuckGo）                                 | 全部代理可用                   |
| `outline_grep`   | `tools/builtin/outline_grep.py`     | 按需摘录大纲（精确定位章简介/衔接要点，回落 FTS 关键词检索，上限 1200 字） | 全部代理可用                   |
| `ask_user`       | `tools/builtin/ask_user.py`         | 多问题卡片询问用户（落 consult 中断 + `consult.request`，resume 续跑）   | 全部代理可用                   |
| `create_volume`  | `tools/builtin/create_structure.py` | 新建卷（薄封装 `services/volumes.py`，校验 + 审计 + 索引刷新）           | 主 Agent + Writer              |
| `create_chapter` | `tools/builtin/create_structure.py` | 新建章节                                                                   | 主 Agent + Writer              |
| `rename_volume`  | `tools/builtin/create_structure.py` | 卷重命名（**只改名字**，不改 sort_order）                            | 主 Agent + Writer              |
| `rename_chapter` | `tools/builtin/create_structure.py` | 章节重命名                                                                 | 主 Agent + Writer              |
| `delete_volume`  | `tools/builtin/create_structure.py` | 删除卷（走回收站归档 + 级联清理）                                          | 仅主 Agent（影响全局，需审批） |
| `delete_chapter` | `tools/builtin/create_structure.py` | 删除章节（走回收站归档）                                                   | 仅主 Agent                     |

**文件类工具**（ls/read_file/write_file/edit_file/delete/glob/grep）不在本仓库落文件——
由 deepagents `FilesystemMiddleware` 按 backend + permissions 在运行时生成。

### 3.3 `outline_grep` 设计要点

按需获取原则：AI 不整篇 `read_file` 大纲，而是先精确摘录目标章小节：

- `chapter_title`（可带 `volume_title`）→ 精确定位该章「简介 + 衔接要点」；
- `query` → 关键词检索大纲命中片段；
- 摘录上限 1200 字，远小于整篇大纲（动辄数千至上万字），显著降低单次 run 的输入 token。

### 3.4 `ask_user` 设计要点

AI 思考处理的中间过程需要用户决策（范围确认/方案选择/补充信息）时调用：

- 落 `agent_interrupts(type=consult, kind=ask_user)` 行 + 发 SSE `consult.request`；
- 前端渲染多问题卡片（单选/多选选项 + 自定义答案输入、翻页、末题右下角「提交」）；
- resume 重放时按 `tool_call_id` 命中已决行：不建行、不发事件、不改状态。

---

## 四、Harness 安全体系

### 4.1 沙箱：让 AI 活在虚拟路径里

Agent 看到的不是 `E:\git\...\data\workspaces\{uuid}\chapters\...`，而是
`/projects/{pid}/chapters/v1/c3.md`。由 `CompositeBackend` 完成映射：

```
/projects/            →  data/workspaces/     写作内容（可读可写，跨项目只读）
/skills/user/         →  data/skills/         用户自建技能
/skills/session-{id}/ →  data/session-skills/  会话精选技能
其余                  →  StateBackend（内存状态）
```

★**绝不用 `LocalShellBackend`**。它会激活 `execute`（执行任意命令）工具。
用 `FilesystemBackend` 时 `supports_execution()` 返回 False，deepagents 自动把
`execute` 从模型可见工具中移除。

### 4.2 权限：顺序即语义的 8 条规则

`permissions` 判定机制：**首条匹配生效，且无匹配时默认放行**。

| #    | 规则                                     | 意图                              |
| ---- | ---------------------------------------- | --------------------------------- |
| ①   | 当前项目 `/projects/{pid}/**` 读写     | AI 能改自己的书                   |
| ②   | 其它项目 `/projects/**` **禁写** | 但绝不能改别的书                  |
| ③   | 其它项目 `/projects/**` 可读           | 允许跨书参考                      |
| ④   | `/skills/**` 可读                      | 技能靠模型 `read_file` 加载     |
| ⑤   | `/skills/**` 禁写                      | 技能由服务层维护，AI 不许自我改造 |
| ⑥⑦ | 会话历史 / 大工具结果 可读写             | 上下文压缩的卸载区                |
| ⑧   | `/**` **兜底 deny**              | 其余一律不行                      |

★编码为 `PERMISSION_RULES` 常量 + 逐行断言的回归测试。

### 4.3 WriteApproval（参数级 HITL）

`interrupt_on` 只能做工具级开关，无法按参数判定。所以本项目自研了
`WriteApprovalMiddleware`：

`classify_risk()` 三类高危：

1. `write_file` 目标是**已存在的非空章节文件**且新旧内容不同 → 整章覆盖；
2. `edit_file` 的 `old_string`/`new_string` 超 800 字符 → 大范围改写；
3. `delete` 任何项目内文件/目录 → 不可逆。

★必须装配在中间件栈**最外层**（`GraphInterrupt` 不能被吞）。

### 4.4 WorkspaceGuard（第二道路径防线）

`permissions` 只作用于内置文件工具，自定义工具和 MCP 工具它管不着。
`WorkspaceGuardMiddleware` 补上这个盲区：

- `_TOOL_OPERATION` 把 7 个文件工具映射到 read/write；
- `check_path` 校验（`..` 拒绝 / 跨项目写拒绝 / 技能区只读 / 越界拒绝）；
- **错误指纹记忆与阻断**：对 `(tool_name, args_hash, error_fingerprint)` 计数，
  2 次注入告警 ToolMessage，3 次直接阻断该调用路径。

### 4.5 ReadScopeGuard（读取授权守卫）

大纲 / 章节正文的读取 token 成本高，默认不主动注入上下文。AI 真要读时按四档放行：

1. 会话已授权 → 放行；
2. 本 run 是写作意图 → 放行；
3. 用户消息显式提及读取 → 放行并记会话授权；
4. 否则弹确认卡（批准 / 本次会话允许 / 拒绝）。

★只覆盖主 agent，**不覆盖子代理**——recaller 子代理的职责正是在隔离上下文里读摘要/正文。

### 4.6 SafeDelete（可恢复删除）

把 Agent 的 `delete` 工具从**永久物理删除**改为与 REST 删除同语义的可恢复删除：

- 章节文件 → 调 `volumes.delete_chapter`，走回收站归档；
- 项目内其它路径 → 整体搬进 `data/trash/agent-deleted/{ts}/`；
- 服务层失败则返回错误 ToolMessage（**fail-closed**，绝不退化成物理删除）。

### 4.7 ContentLock（AI 编辑锁）

Agent 写章正文时加锁（`content_locks.acquire`）+ 广播 `content.lock`；
锁定期内用户保存被拒（409/40903）；run 收尾统一释放。

### 4.8 LLM 容错与成本治理（`harness/llm/`）

| 模块                 | 职责                                                                                               |
| -------------------- | -------------------------------------------------------------------------------------------------- |
| `resilience.py`    | ModelRetry（退避重试 2 次）+ ModelSwitchOnFailure（当前模型**连续失败 3 次**才切换备用模型） |
| `call_budget.py`   | ModelCallBudget（默认 60 次/次委派 20 次）：超限先总结已完成工作再 consult 询问继续/结束           |
| `budget.py`        | token 预算计数 + `budget_report`（供前端 `context.compact` 事件）                              |
| `wasteful_loop.py` | 浪费型循环分级熔断：SOFT=3 次注入纠正 / HARD=5 次注入换方向 / 工具层硬阻断                         |
| `prompt_cache.py`  | 显式前缀缓存断点（DashScope/Anthropic 折扣计费，最多 2 个断点）                                    |

---

## 五、子代理与技能

### 5.1 内置子代理（5 个）

| 子代理           | UI 展示名    | 工种                                                         | 默认工具                                | workflows 绑定                            |
| ---------------- | ------------ | ------------------------------------------------------------ | --------------------------------------- | ----------------------------------------- |
| `planner`      | Planner      | 策划大纲（三级大纲/分卷节奏/伏笔布局）                       | read/write/edit/ls/grep/glob            | chapter-workflow（S0/S1）                 |
| `writer`       | Writer       | 撰写修改**全流程**（生成正文 + 润色校对）              | read/write/edit/ls/grep/glob + 结构工具 | chapter-workflow（S2）                    |
| `reviewer`     | Reviewer     | 大纲与内容**检查者**（连贯性 + 大纲结构 + 简介匹配度） | read/ls/grep/glob（**只读**）     | chapter-workflow + book-consistency-audit |
| `web-searcher` | Web_Searcher | 资料检索                                                     | web_search                              | —                                        |
| `recaller`     | Recaller     | 记忆召回（在隔离上下文里读摘要/正文再回传精炼结论）          | read/ls/grep/glob                       | —                                        |

每个子代理一个目录（`spec.py` + `prompt.md`），提示词独立文件便于版本化与「恢复默认」。

**子代理的核心价值是上下文隔离**。默认 `mode=isolated` 意味着每个子代理在自己的干净上下文里
干活，只把结论带回来。`SubagentContextScrubMiddleware` 在子代理结束时清空中间上下文。

**子代理同样受中间件保护**：主栈中的安全/通知中间件（WriteApproval / WorkspaceGuard /
ContentLock / FileChangeNotify / SafeDelete 等）必须同样挂到子代理栈上，且同构。

### 5.2 技能（Skills）

技能 = **用户自建**（无随包出厂技能），每个是一个 `SKILL.md`，物化到 `data/skills/{name}/`，
挂载在虚拟路径 `/skills/user/` 下。**渐进式披露**——不会一次性塞进提示词，模型只在需要时
自己 `read_file` 去读。

**技能与子代理的组合关系**：**子代理是「谁来干」，技能是「按什么章法干」**。

### 5.3 子代理派发语义

```
主 Agent 统筹决策
  → 经 task 工具派发
  → 子 Agent 独立执行（隔离上下文）
  → SubagentContextScrubMiddleware 清空中间上下文
  → 结果回传主 Agent 汇总
```

---

## 六、流程编排

### 6.1 四阶段状态机（S0→S3）

「AI 写正文必须以大纲章简介为前提」——这条要求做成**服务层硬编排**的状态机：

```
S0 大纲检查   →  这章在大纲里有小节和简介吗？
S1 前置校验   →  简介与上一章结尾接得上吗？
S2 生成正文   →  ★唯一进 Agent 循环的阶段
S3 后置校验   →  写出来的东西符合简介吗？
```

**执行主体切分**（核心设计）：

| 阶段         | 谁来执行                                         | 为什么                               |
| ------------ | ------------------------------------------------ | ------------------------------------ |
| S0 / S1 / S3 | **服务层直连 ChatModel + JSON 结构化输出** | 这是是非题，要可预测、可单测、成本低 |
| S2           | **deep agent 循环**                        | 需要读写文件、调 writer 子代理       |

「能用确定性代码解决的事，绝不交给概率系统」——这条原则贯穿全项目。

### 6.2 咨询兜底

每道流程都能**发咨询卡片让人拍板**：

- S0 缺简介：「让 AI 代写大纲」或「我去补」
- S1 不合理：修改建议 + 「强制跳过」（需显式确认并落审计）
- S3 不匹配：`POSTCHECK_MAX_RETRY=2` + 仍不匹配 → 咨询卡片

**上限 + 兜底咨询**，杜绝死循环。

### 6.3 其他固定流程

| 流程                               | 模块                                    | 说明                                                                                  |
| ---------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------- |
| **全书一致性审查**（A1→A3） | `workflows/book_consistency/audit.py` | 按卷分批直连 LLM，报告落 `outline/consistency-audit.md`。reviewer 子代理绑定        |
| **导出成书预检**（E1→E4）   | `workflows/novel_export/preflight.py` | 纯规则不调 LLM（结构/内容/格式三级 checklist）。不阻断导出，返回 checklist 供用户过目 |

---

## 七、上下文治理

### 7.1 核心矛盾

模型上下文有限，而书会越来越长。

### 7.2 分层策略

| 手段                       | 做法                                                                            |
| -------------------------- | ------------------------------------------------------------------------------- |
| **前情提要替代原文** | 每章定稿后生成结构化摘要，后续章节只带摘要                                      |
| **跨卷锚点**         | 每卷第 1 章自动补上一卷末章锚点；第一卷用大纲附录的人物/世界观设定              |
| **按需精选**         | 会话级「已读章节」Chips（上限 8 章防膨胀）                                      |
| **预算监控**         | 可用量 = `max_context − reserve_for_output`，超了发 `context.compact`      |
| **摘要压缩**         | 触发时把驱逐消息卸载到 `/conversation_history/`                               |
| **增量化**           | checkpoint 用 `DeltaChannel(snapshot_frequency=50)` 存增量                    |
| **隔离**             | 子代理独立上下文（`mode=isolated`）                                           |
| **硬上限**           | `ModelCallBudget(60)` 软上限 + `recursion_limit(60)` 硬上限                 |
| **浪费熔断**         | `WastefulLoopGuard`：同路径反复读/写 ≥ 5 次硬阻断                            |
| **显式缓存**         | `PromptCache`：在稳定内容（system + 用户消息）上打断点，跨 run/run 内复用折扣 |
| **上下文守卫**       | `ContextGuard` + `ContextOverflowGuard`：两层保险（确定性定位 + 超限兜底）  |
| **配对修复**         | `PairingRepair`：多并行 tool call 偶发错位时原地修复，避免模型反复重读附件    |

**设计哲学**：上下文是最贵的资源，每一段进入提示词的文字都要有明确理由。

### 7.3 前情提要生成（`loop/knowledge/recap.py`）

`build_recap` 构建：书名/题材/梗概 + 近 6 章摘要（无摘要则正文节选 ≤600 字）+ 跨卷衔接锚点。

### 7.4 章节摘要（`loop/knowledge/summarize.py`）

`summarize_chapter`：★**唯一可写 `summaries/` 的入口**（WorkspaceGuard 强制）。
正文 <30 字跳过，无可用模型返回 `skipped` 而非抛错。
输出 JSON：`key_events` / `characters` / `foreshadowing` / `ending_state`。

---

## 八、Agent 与外部的接口交互

### 8.1 Agent 与服务层的交互

```
agent/ → services/（允许方向）
  ├─ workflows/ 用大纲解析、文件写入
  ├─ knowledge/ 用章节数据
  ├─ loop/ 用 LLM Provider CRUD
  └─ more/mcp/ 用天气服务

services/ → agent/（仅两处文档化例外）
  ├─ services/agent.py → agent.tracing.messages（消息树读写）
  └─ services/search.py → agent.loop.knowledge（章节摘要生成）
```

### 8.2 Agent 与 API 层的交互

```
api/ → agent/（仅运行入口）
  └─ api/agent.py 持有 run_manager + sse_bus（起 run / 订阅流）
     api/chapters.py 持有 rewrite（服务层直连 LLM 的选区改写）
```

### 8.3 Agent 与前端的交互通道

```
前端 → 后端（REST）
  POST /agent/sessions/{sid}/messages     发消息（202 + run_id）
  POST /agent/runs/{id}/cancel            取消
  POST /agent/runs/{id}/steer             添加引导消息
  POST /agent/interrupts/{id}/resolve     审批/咨询决策
  PATCH /agent/sessions/{sid}/context     更新上下文选择

后端 → 前端（SSE）
  GET /agent/runs/{id}/stream             17 类事件流
  file.changed → 前端热更新正文与目录树
  content.lock → 前端锁定编辑器
  workflow.update → 流程状态条流转
  interrupt.request → 审批卡片
  consult.request → 咨询卡片
  context.compact → 上下文压缩提示
  usage → token 用量
```

### 8.4 Agent 与文件系统的交互

```
Agent 工具调用
  │
  ├─ 内置文件工具（deepagents FilesystemMiddleware 提供）
  │   ls / read_file / write_file / edit_file / delete / glob / grep
  │   ↓
  │   CompositeBackend 虚拟路径映射
  │   ↓
  │   permissions 8 条规则校验（第一道防线）
  │
  ├─ 自定义工具（9 个，registry.py 装配）
  │   web_search / outline_grep / ask_user / 6 个结构工具
  │   ↓
  │   WorkspaceGuardMiddleware 路径校验（第二道防线）
  │
  └─ MCP 工具（天气 MCP + 第三方）
      ↓
      WorkspaceGuardMiddleware 路径校验（第二道防线）
```

### 8.5 Agent 与可观测（Langfuse）

三条链路：

1. **自动埋点为主**：`langfuse.langchain.CallbackHandler` 自动产出 trace/span/generation；
2. **手动补充**：流程 S0/S1/S3 等服务层直连 LLM 调用用 `observation()` 包裹；
3. **评分回写**：run 结束写 `run_tokens` / `project_words` / `fallback_used` 三项 score。

★未配置 Key 时全链路自动 no-op 降级为本地结构化日志。

---

> 本文提取自 [`Project-Develop.md`](./Project-Develop.md)，完整技术细节请参见原文。
