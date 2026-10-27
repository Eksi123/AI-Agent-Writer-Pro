# 项目组织架构评估

> 评估时间：2026-10-27
> 评估范围：`src/app`（后端）目录结构、分层依赖、文件粒度、命名一致性
> **本文档只做评估与建议，未对代码做任何修改。**
> 基准：`doc/Writer-Develop-Plan.md`「三、(1) 工程结构」

---

## 一、结论

**当前结构不是最优雅的。** 可读性与可追溯性合格（agent/ 子包与手册章节 1:1 对应是本项目最有价值的组织特征），但在两个维度上有明确缺陷：

1. **依赖方向**：`services/` → `agent/` 存在 **5 个文件、9 处 import（涉及 6 类被依赖符号）**的反向依赖，违反手册「目录边界」的双向禁令；根因是**手册自身的规则冲突**，不是实现走样。
2. **单文件职责**：`agent/loop/runner.py` 1420 行承担 7 类职责，是典型的上帝文件；连带导致 `api/agent.py` 越过 services 层直接 import agent 内部函数。

全项目 **172 处函数内延迟导入**是上述循环依赖的量化证据（其中相当部分是为规避 import 环而被迫下沉到函数体内）。

功能正确性不受影响（399 passed / 1 skipped），**当前结构可以继续用**。重构属于「改善可维护性」而非「修复缺陷」，可按第六节优先级分步做。

---

## 二、客观数据

### 2.1 规模分布

全项目 86 个 py 文件、15827 行。

| 区域 | 行数 | 占比 |
| --- | --- | --- |
| `services/` | 5365 | 34% |
| `agent/loop/` | 3180 | 20% |
| `api/` | 1751 | 11% |
| `db/` | 862 | 5% |
| `agent/gates/` | 849 | 5% |
| `core/` | 786 | 5% |
| `agent/llm/` | 589 | 4% |
| `schemas/` | 424 | 3% |
| 其余 agent 子包 | 1521 | 10% |
| `main.py` / `cli.py` | 385 | 2% |

### 2.2 最大文件 TOP 10

| 行数 | 文件 | 判断 |
| --- | --- | --- |
| **1420** | `agent/loop/runner.py` | 🔴 36 个顶层定义 / 43 处直接 DB 访问 / 7 类职责 |
| 744 | `services/llm.py` | 🟡 Provider+Model+测试+重载，偏大但内聚 |
| 676 | `agent/gates/orchestrator.py` | 🟡 G0-G3 四阶段 + 快照 + 摘要，属合理聚合 |
| 616 | `services/volumes.py` | 🟡 卷章 CRUD + 乐观锁 + 快照 |
| 592 | `services/exporters.py` | 🟡 4 种格式 + EPUB 生成器 |
| 446 | `services/agent.py` | 🟢 |
| 424 | `schemas/__init__.py` | 🟡 全部 DTO 挤在单 `__init__.py` |
| 421 | `services/novels.py` | 🟢 |
| 402 | `agent/loop/middleware/write_approval.py` | 🟢 单中间件 |
| 398 | `agent/loop/builder.py` | 🟢 |

### 2.3 循环依赖信号：函数内延迟导入

| 数量 | 文件 |
| --- | --- |
| 21 | `agent/loop/runner.py` |
| 19 | `cli.py`（命令惰性加载，**属合理设计**） |
| 13 | `agent/loop/builder.py` |
| 12 | `agent/loop/middleware/write_approval.py` |
| 8 | `agent/mcp/adapter.py` |
| 7 | `main.py` / `services/llm.py` / `gates/orchestrator.py` / `middleware/file_change.py` |
| 6 | `services/agent.py` |
| 5 | `api/agent.py` / `services/skills.py` / `services/story.py` / `llm/factory.py` / `mcp/server.py` |

**合计 172 处**（`cli.py` 的 19 处是刻意的命令惰性加载，不计入问题；其余 ~150 处中相当部分为规避 import 环）。

### 2.4 `__init__.py` 承载实质逻辑

| 行数 | 文件 | 风格 |
| --- | --- | --- |
| 386 | `agent/knowledge/__init__.py` | 实现全在 `__init__` |
| 255 | `agent/memory/__init__.py` | 实现全在 `__init__` |
| 153 | `agent/subagents/__init__.py` | 实现全在 `__init__` |
| 120 | `agent/tracing/__init__.py` | 实现全在 `__init__` |
| 38 | `agent/prompts/__init__.py` | 实现全在 `__init__` |
| 18 | `agent/mcp/__init__.py` | 纯 re-export |
| 18 | `agent/tools/__init__.py` | 纯 re-export |
| 28 | `agent/gates/__init__.py` | 纯 re-export |
| 24 | `agent/loop/middleware/__init__.py` | 纯 re-export |
| 16 | `agent/llm/__init__.py` | 纯 re-export |
| 14 | `agent/sandbox/__init__.py` | 纯 re-export |
| 13 | `agent/loop/__init__.py` | 纯 re-export |

**同一个 agent/ 包内两种风格并存**：找代码时无法预期实现落在 `__init__.py` 还是同名模块里。

### 2.5 目录粒度不对称

- `services/`：**扁平 15 个文件、5365 行**，无任何子包
- `agent/`：**12 个子包**，最大子包 `loop/` 3180 行

---

## 三、问题一：`runner.py` 是上帝文件

### 3.1 职责清单（7 类）

| 职责 | 主要定义 | 行数区间 |
| --- | --- | --- |
| run 生命周期管理 | `RunManager`（start/resume/cancel/_spawn） | 49-217 |
| 普通运行编排 | `_execute_run` | 218-364 |
| **门禁运行编排** | `_execute_gate_run` / `_persist_gate` / `_persist_gate_consult` / `_extract_consult_decision` | **408-708（~300 行）** |
| LangGraph→SSE 事件映射 | `_stream_graph` / `_handle_message_chunk` / `_handle_update` / `_emit_message_complete` / `_extract_todos` / `_subagent_name` | 709-1011 |
| 消息树增量同步 | `_MessageSyncer` / `_is_incomplete` / `_role_of` / `_status_of` / `_model_of` | 1012-1150 |
| 模型与会话上下文解析 | `_resolve_models` / `_max_context_of` / `_make_recap_provider` / `_pinned_chapters_of` / `_session_context_of` | 1151-1270 |
| **会话数据访问** | `list_sessions` / `list_tool_calls` / `_require_session` / `_project_word_count` | **1339-1420** |

### 3.2 最能说明问题的错位

`_execute_gate_run`（门禁运行编排，~230 行）住在 `agent/loop/runner.py`，而门禁状态机 `_gate_g0/_gate_g1/_gate_g3` 住在 `agent/gates/orchestrator.py`。**同一个功能被劈成两半放在两个子包**，读门禁流程必须跨文件跳转。

### 3.3 连带破坏 api 分层

`api/agent.py` 直接 import agent 内部函数：

```python
# src/app/api/agent.py:33-34
from app.agent.loop.events import buffer_lost_event, sse_bus
from app.agent.loop.runner import list_sessions, list_tool_calls, run_manager
```

其余 13 个 api 模块都经 `services/` 取数据，**只有 `api/agent.py` 越层直连 agent 内部**。`list_sessions` / `list_tool_calls` / `_require_session` / `_project_word_count` 这 4 个纯数据访问函数本应住在 `services/agent.py`。

---

## 四、问题二：`services/` ↔ `agent/` 双向依赖

### 4.1 手册规则自相矛盾（根因）

手册「三、(1) 目录边界」原文：

> `src/app/agent/` 存放 **AI Agent 的全部代码逻辑**（LLM 接入、Agent-Loop、门禁、工具、子代理、技能、MCP、记忆、知识上下文、沙箱、提示词、追踪）；其余后端内容放在 `src/app` 其余目录；**Agent 逻辑不外溢到 api/services，非 Agent 业务代码也不进入 agent/**。

但手册的目录树同时写明：

> `agent/llm/  # registry / factory / params（四层覆盖）/ budget（token 预算）`

于是必然冲突：**LLM Provider CRUD 是业务服务**（`services/llm.py`），它必须用 ChatModel 工厂来做「连接测试」和「降级链解析」——而工厂按手册规定住在 `agent/llm/`。这条禁令在此处**不可能被满足**。

### 4.2 实际反向依赖清单

| services 文件 | 依赖的 agent 符号 | 被依赖物的**真实性质** |
| --- | --- | --- |
| `services/llm.py:24` | `agent.llm.factory`（build_chat_model / get_fallback_chain / invalidate_model_cache / provider_key_status） | **LLM 基础设施**，与 Agent-Loop 无关 |
| `services/llm.py:611` | `agent.tracing.reset_client` | **可观测基础设施** |
| `services/mcp.py:24,241` | `agent.mcp.adapter`（validate_stdio_command / probe_stdio / probe_http / wrap_tools_async） | **MCP 传输层基础设施** |
| `services/skills.py:144,233,350` | `agent.sandbox.backend.BUILTIN_SKILLS_DIR` | **纯路径常量** |
| `services/subagents.py:28` | `agent.subagents.BUILTIN_SUBAGENTS` | **静态数据目录** |
| `services/search.py:266` | `agent.knowledge.summarize_finalized_chapters` | 真·Agent 能力（**唯一合理例外**） |

**上表 6 类依赖中有 5 类的被依赖物根本不是「Agent 逻辑」**——它们是路径常量、静态数据、LLM/MCP/可观测基础设施（对应 9 处 import 中的 8 处）。只有 `search.py → knowledge.summarize_finalized_chapters` 是真正的 Agent 能力调用。

### 4.3 正向依赖（允许方向，无问题）

`agent/ → services/` 共 6 个文件、19 处，这是手册允许的方向：

| agent 文件 | 处数 |
| --- | --- |
| `gates/orchestrator.py` | 5 |
| `knowledge/__init__.py` | 3 |
| `loop/middleware/file_change.py` | 3 |
| `loop/builder.py` | 2 |
| `loop/runner.py` | 2 |
| `mcp/server.py` | 4 |

**问题只出在反向。**

### 4.4 api → agent 直连清单

| api 文件 | 依赖 | 性质 |
| --- | --- | --- |
| `api/agent.py:33-34` | `agent.loop.events.sse_bus`、`agent.loop.runner.{list_sessions,list_tool_calls,run_manager}` | 🔴 越层（数据访问应经 services） |
| `api/agent.py:123,179` | `agent.memory.{load_tree,set_active_leaf}` | 🟡 消息树读写，可归 services |
| `api/chapters.py:62` | `agent.llm.rewrite.rewrite_selection` | 🟡 函数内延迟导入，且 rewrite 实为「服务层直连 LLM」（同门禁 G1/G3），性质更接近 services |

---

## 五、建议的更好组织方式

### 5.1 核心思路

**把「基础设施」从 `agent/` 下沉到 `core/`，让依赖图变成无环 DAG。**

判定标准：一个模块若**不含 Agent-Loop / 门禁 / 子代理 / 技能语义**，只是被多方共用的底层能力，它就属于 `core/`。

### 5.2 目标结构

```
app/
├── core/                       # 零业务、零 Agent 知识的基础设施
│   ├── config / errors / response / logging / paths / security / deps
│   ├── llm/                    # ★从 agent/llm 下沉
│   │   ├── factory.py          #   provider 行 → ChatModel（services/llm 与 agent 共用）
│   │   ├── params.py           #   四层参数覆盖
│   │   └── budget.py           #   token 预算
│   ├── mcp/                    # ★从 agent/mcp/adapter 拆出传输层
│   │   ├── transport.py        #   stdio/http/sse 探测 + call_tool 自封装
│   │   └── policy.py           #   命令白名单 validate_stdio_command
│   └── catalog.py              # ★BUILTIN_SUBAGENTS / BUILTIN_SKILLS_DIR / SKILL_PATHS
│                               #   静态数据与路径常量集中（services 与 agent 共用）
├── db/
│   └── models/                 # ★按域拆分：novel.py / agent.py / llm.py / config.py
├── schemas/                    # ★按域拆分：novel.py / agent.py / llm.py / config.py
├── services/                   # 业务编排：只依赖 core + db，★永不 import agent/
│   ├── novels / volumes / story / search / outline / exporters / files
│   ├── naming / audit / workspace / weather
│   ├── llm.py                  #   现在可顶层 import core.llm.factory（环消除）
│   ├── mcp.py                  #   现在可顶层 import core.mcp.*
│   ├── skills.py / subagents.py#   现在可顶层 import core.catalog
│   └── agent.py                # ★接收 runner 迁出的数据访问：
│                               #   list_sessions / list_tool_calls
│                               #   require_session / project_word_count
├── agent/                      # 可依赖 core + services + db（单向，无环）
│   ├── loop/
│   │   ├── runner.py           # 只留 run 生命周期（~350 行）
│   │   ├── streaming.py        # ★LangGraph→SSE 事件映射（~250 行）
│   │   ├── message_sync.py     # ★_MessageSyncer（~120 行）
│   │   ├── builder.py / events.py / emitter.py / context.py / checkpointer.py
│   │   └── middleware/         #   4 个中间件不变
│   ├── gates/
│   │   ├── orchestrator.py     #   状态机 G0-G3（~450 行）
│   │   └── runner.py           # ★_execute_gate_run 从 loop/runner 迁入（~230 行）
│   ├── knowledge/
│   │   ├── recap.py            # ★build_recap / build_pinned_chapters
│   │   └── summarize.py        # ★summarize_chapter / summarize_finalized_chapters
│   ├── memory/
│   │   └── tree.py             # ★消息树（从 __init__ 外提）
│   ├── subagents/
│   │   └── builder.py          # ★装配逻辑（目录数据已移至 core/catalog）
│   ├── mcp/
│   │   ├── weather_server.py   #   内置天气 MCP + LangChain tool
│   │   └── tools.py            #   第三方 MCP → LangChain tool（调 core.mcp.transport）
│   ├── tracing/
│   │   └── langfuse.py         # ★从 __init__ 外提
│   ├── tools/                  #   web_search.py + registry.py（已拆分，保持）
│   ├── skills/                 #   8 个内置 SKILL.md（保持）
│   ├── sandbox/                #   backend.py + permissions.py（保持）
│   └── prompts/                #   Jinja2 环境 + 模板（保持）
└── api/                        # 只依赖 services + agent 的运行入口
    ├── agent.py                #   数据访问改经 services.agent；只额外持有 run_manager
    └── 其余 13 个模块（保持）
```

### 5.3 依赖图对比

**现状（有环）：**
```
api ──→ services ──→ agent     ← 反向依赖 6 处
 │                     │
 └─────→ agent ←───────┘        ← api 越层直连
         ↓
      services                  ← 环
```

**目标（严格 DAG）：**
```
api ──→ services ──→ core / db
 │         ↑
 └──→ agent ┘（agent 可用 services，services 永不用 agent）
       ↓
     core / db
```

### 5.4 收益

1. **6 类反向依赖消除 5 类**（9 处 import 消除 8 处），剩下 1 处（`search → knowledge.summarize_finalized_chapters`）改为由 `api/story.py` 编排，或作为**文档化的唯一例外**。
2. **~150 处延迟导入大部分可提到模块顶层**——环没了就不需要函数内 import 规避，IDE 跳转与静态分析随之改善。
3. **`runner.py` 1420 → ~350 行**，门禁编排完整归入 `gates/`（消除「一个功能劈两半」）。
4. **api 层分层一致**：全部 14 个 api 模块都经 services 取数据。
5. **手册「目录边界」规则可被静态检查**：加一条 import-linter 契约即可在 CI 拦截，不必靠人工自觉。

### 5.5 代价

- 约 40-50 个文件的移动与 import 改写
- `agent/llm/` → `core/llm/` 需**同步修订手册「三、(1)」目录树**（这是修正手册的内在矛盾，不是偏离手册）
- 需要一轮全量回归（现有 399 个测试是良好的安全网）

---

## 六、优先级建议（性价比排序）

| 优先级 | 动作 | 成本 | 收益 | 是否需改手册 |
| --- | --- | --- | --- | --- |
| **P0** | `runner.py` 的 4 个数据访问函数（`list_sessions` / `list_tool_calls` / `_require_session` / `_project_word_count`）移到 `services/agent.py`；`api/agent.py` 改从 services 取 | 极低（~30 行改动） | 恢复 api 分层一致性，14 个 api 模块归一 | 否 |
| **P1** | `BUILTIN_SKILLS_DIR` → `core/paths.py`；`BUILTIN_SUBAGENTS` → `core/catalog.py` | 低 | 消 2 处反向依赖（纯常量/数据，风险极低） | 否 |
| **P2** | `agent/llm/` → `core/llm/`；`agent/mcp/adapter` 传输层 → `core/mcp/` | 中 | 消最主要 3 处反向依赖 + 大量延迟导入可提升到顶层 | **是**（修订目录树） |
| **P3** | 拆分 `runner.py`：`streaming.py` / `message_sync.py` / `gates/runner.py` | 中 | 消除上帝文件，门禁逻辑归位 | 否 |
| **P4** | `knowledge` / `memory` / `subagents` / `tracing` 的 `__init__.py` 逻辑外提；`schemas/` 与 `db/models/` 按域拆分 | 中 | 可发现性与风格统一 | 否 |
| **P5** | 引入 import-linter，把「services 不得 import agent」写成 CI 契约 | 低 | 防止架构再度漂移 | 否 |

**P0 + P1 合计不到一小时**，就能解决「api 越层」和 2 处循环依赖，是明确值得做的。
**P2 需要先改手册**，建议连同手册勘误一起做（手册该条规则本身不自洽）。
**P3~P5 属改善性重构**，可择机进行，不阻塞任何功能开发。

---

## 七、值得肯定的部分

评估不能只看问题。现有结构有三处明显优点，**上述方案完整保留**：

### 7.1 agent/ 子包与手册章节 1:1 对应

`llm / loop / gates / tools / subagents / skills / mcp / memory / knowledge / sandbox / prompts / tracing`
与手册「二、(1)~(6)」+「三、(1)」目录树逐项对应。读手册能直接定位代码，这是本项目**最有价值的组织特征**，任何重构都不应破坏它。

### 7.2 api/services 同名配对

10 对同名模块（agent / audit / llm / mcp / novels / outline / skills / story / subagents / volumes），分层命名一致、无缩写、无歧义路径。
> 唯一副作用：`agent` 一词三义（`api/agent.py`、`services/agent.py`、`agent/` 包）。但三者层次清晰，实际阅读中不构成困扰。

### 7.3 「铁律」的落地方式扎实

| 铁律 | 落地方式 |
| --- | --- |
| 权限规则顺序即语义 | `PERMISSION_RULES` 常量数组 + 顺序回归测试（断言与手册表逐行一致） |
| 错误码规范 | `ErrorCode` 常量 + `CODE_TO_HTTP` / `CODE_TO_MESSAGE` 双映射表 |
| 路径与编码统一 | `core/paths.py` 集中解析，全部 `pathlib` + 显式 `encoding="utf-8"` |
| ID 稳定、顺序派生 | `sort_order` 唯一写入口 `sync_order_from_outline`，无 reorder 接口（测试断言路由不存在） |

这类「把设计约束编码成常量 + 配回归测试」的做法，比写在文档里靠人工自觉可靠得多。

---

## 八、附：数据采集方式

本文所有数字由以下命令采集，可复现：

```powershell
# 文件规模分布
Get-ChildItem src/app -Recurse -Filter *.py | ForEach-Object {
  [pscustomobject]@{ Lines = (Get-Content $_.FullName).Count; Path = $_.FullName }
} | Sort-Object Lines -Descending | Select-Object -First 20

# 反向依赖（services/api → agent）
git grep -n "from app.agent\|import app.agent" -- 'src/app/services/*.py' 'src/app/api/*.py'

# 正向依赖（agent → services）
git grep -c "from app.services" -- 'src/app/agent/**/*.py'

# 函数内延迟导入（循环依赖信号）
# 正则：^\s+(from |import )

# 命名重叠
Get-ChildItem src/app -Recurse -Filter *.py | Group-Object Name |
  Where-Object { $_.Count -gt 1 }
```
