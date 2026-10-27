# 端到端验收清单（M7，手册「十一 · 每里程碑验收口径」）

> 环境准备：`uv sync` → `uv run writer migrate` → `uv run writer dev`；前端 `cd src/web; npm install; npm run build`（单体部署）或 `npm run dev`。
> 自检：`uv run writer doctor` 应显示 FTS5 可用、依赖版本齐全。

## 一、写作业务链路（M1/M2，手工验收）

| # | 步骤 | 预期 |
| --- | --- | --- |
| 1 | 新建小说（填书名「星海拾荒人」） | 项目出现在左栏；`data/workspaces/{id}/` 生成 novel.yaml / volumes.yaml / outline/{书名}-大纲.md（模板） |
| 2 | 新建书名填「未命名小说」或「第 1 卷」 | 前端拒绝；直连 API 返回 422/42201 |
| 3 | 新建卷「荒原初醒」+ 章「拾荒少年的清晨」 | 卷章树按创建顺序追加显示；无拖拽/上移下移控件 |
| 4 | 编辑正文 → 等待自动保存 | AutosaveIndicator 显示已保存；字数统计更新 |
| 5 | 编辑模式大纲节点改卷名 → 保存 | 警告条提示归一化不一致（如有）；卷章顺序按大纲小节回填；章节 ID 与文件名不变 |
| 6 | 两个窗口同时编辑同一章 | 后保存方收到 409/40901，ConflictBanner 提供合并/覆盖；覆盖需携带最新 base_version |
| 7 | 修订历史 → 回滚 | 内容与 version 正确恢复；回滚前当前内容自动快照 |
| 8 | 阅读模式：目录顶端「大纲」节点 | 大纲可阅读；翻页/进度记忆（重进项目定位上次位置） |
| 9 | 导出 markdown/epub/txt | zip 内含正文（按 sort_order）+ 大纲原 md；epub 的 mimetype 为首条目且不压缩 |
| 10 | 导出 mobi（未装 calibre） | 选项置灰；直连 API 返回 503/50301 |
| 11 | 删除小说 | 移入 data/trash/{id}-{ts}/；列表不再显示 |
| 12 | 通用设置改主题/字号/回落城市 → 保存 | 刷新后仍生效（user_settings 单行表） |
| 13 | `GET /api/v1/audit-logs` | 上述操作均有审计记录（action/entity/detail） |
| 14 | 编辑模式选中一段文字 → 点「AI 改写/扩写/缩写/润色」 | 按钮仅在选中时启用；返回 diff 对话框（句级高亮删除/新增、字数增减统计） |
| 15 | diff 对话框「接受改写」 | 选区被替换并带 AI 来源标记（底色+下划线）；「放弃」不改动任何内容 |
| 16 | 点击 AI 来源标记 | 回滚为原文；改写过程不直接写文件（接受后走正常保存链路：乐观锁+快照） |
| 17 | 未配置模型时点改写 | 400/40001 可读错误（不静默失败）；审计有 chapter.rewrite 记录 |

## 二、LLM 配置链路（M3）

| # | 步骤 | 预期 |
| --- | --- | --- |
| 1 | AI 工具 → 添加 Provider（openai_compatible + DashScope 密钥） | 密钥写入 `.env`；卡片显示掩码 `sk-****abcd`，不回显明文 |
| 2 | 环境变量留空添加 Provider | 卡片显示「未配置密钥」红色警示 |
| 3 | 添加模型并设为默认 | /llm/overview 的 default_model_ref 更新 |
| 4 | 测试连接 | 成功显示延迟 ms；密钥错误给可读失败原因（HTTP 仍 200，ok=false） |
| 5 | 配置降级链后主模型故意配错 | 运行时自动降级，agent_runs.fallback_count > 0 |
| 6 | `POST /config/reload` | config_version +1；Agent 工厂/模型缓存失效 |

## 三、Agent 会话链路（M3，端到端）

| # | 步骤 | 预期 |
| --- | --- | --- |
| 1 | 右侧面板新建会话 → 发消息 | 202 + run_id；SSE 依次收到 run.start → message.delta（流式打字）→ message.complete → run.end |
| 2 | 刷新页面后重进会话 | 消息树完整恢复（messages 表） |
| 3 | 让 AI 读其它项目的文件 | 读取成功（跨项目只读） |
| 4 | 让 AI 写其它项目的文件 | 被 permissions/WorkspaceGuard 拒绝（40301 语义），AI 收到拒绝回执 |
| 5 | 让 AI 删除文件 | 触发审批卡片（interrupt.request）；批准后真实删除；拒绝后 AI 收到拒绝 ToolMessage 并调整 |
| 6 | 审批卡片「编辑后批准」 | 以 edited_args 执行；审计记录决策 |
| 7 | 运行中点「停止」 | 协作式取消；run 状态 cancelled；消息历史无悬空 tool_call（PatchToolCalls 修复） |
| 8 | SSE 断线重连（带 Last-Event-ID） | 从环形缓冲补发；缓冲丢失 → error(41001) → 前端回落重拉消息树 |
| 9 | 同会话运行中再发消息 | 409/40902 |
| 10 | 重新生成 | 同父兄弟分支（branch_index +1）；可切分支查看历史版本 |
| 11 | 让 AI 写大纲文件 | file.changed 事件 → 卷章顺序自动回填 → 前端目录树热更新 |
| 12 | 让 AI 整章覆盖已有正文 | 触发参数级审批（WriteApproval：overwrite）；批准前先有 chapter_revisions(source=agent) 快照 |
| 13 | write_todos | plan.update 事件 → PlanTodoList 渲染 |
| 14 | Langfuse 配置后运行 | trace 出现；agent_runs.langfuse_trace_id 落库；未配置时功能不受影响 |
| 15 | 会话菜单「导出会话」 | 下载 Markdown（含标题/项目/时间元信息 + 全部消息 + 工具调用行）；空会话时菜单项禁用 |
| 16 | 输入区「＋ 上下文」添加子代理/技能/已读章节 Chips | 增删即持久化（PATCH context）；刷新/重进会话后 Chips 回填 |
| 17 | 添加「已读章节」Chip 后发消息 | 该章标题+摘要/节选进入系统提示（recap），AI 可引用其内容 |
| 18 | 只选 writer 子代理后发消息 | 装配的子代理仅 writer（+deepagents 自动追加的 general-purpose） |
| 19 | 选中技能 Chip 后运行 | 技能物化到 data/session-skills/{sid}/ 并挂载 /skills/session-{sid}/；取消选择后目录清空重建 |

## 四、门禁生成链路（M4，端到端）

| # | 步骤 | 预期 |
| --- | --- | --- |
| 1 | 目标章大纲无小节 → 「生成本章」 | G0 咨询卡片（missing_kind=section，三选项）；run 状态 awaiting_consult |
| 2 | 小节存在但简介为空 | G0 咨询卡片文案不同（missing_kind=intro）；不自动插入占位小节 |
| 3 | 选「让 Agent 代写大纲」 | planner 补写大纲小节后自动重查 G0 |
| 4 | 选「我自己补」→ 补完大纲 → 重跑 | G0 通过 → G1 |
| 5 | G1 预检不通过 | 咨询卡片含问题清单+修改建议；上一卷末章锚点带入（第一卷用附录设定） |
| 6 | G1 选「强制跳过」 | 进入 G2；g1_skipped 落审计 |
| 7 | G2 生成 | 生成前旧正文快照（source=agent）；正文覆盖简介要点；GateStatusBar G0→G3 流转 |
| 8 | G3 不匹配 | 自动重生成（每次重生前再快照）；≤2 次后咨询卡片（认可现状/手动修改/补充指示再生成） |
| 9 | G3 通过 | summarize_chapter 生成摘要文件（summaries/ch-{id}.md，frontmatter 含 key_events）；派生索引更新；检索能命中摘要 |
| 10 | 门禁 run 详情 | gate_stage / gate_retry_count / gate_result_json 全程流转可查 |

## 五、子代理与技能（M5）

| # | 步骤 | 预期 |
| --- | --- | --- |
| 1 | AI 工具 → 写作子代理 | 内置 5 个（planner/writer/editor/continuity-checker/researcher）已播种；停用后主代理不再委派 |
| 2 | 编辑 writer 提示词 → 生成章节 | 新提示词生效；「恢复出厂」还原 |
| 3 | 新建子代理（fork + skills） | API 拒绝 40001（fork 禁用 skills） |
| 4 | 新建用户技能 → 启用 | 物化到 data/skills/{name}/SKILL.md；Agent 可 read_file 渐进披露；停用即移除物化 |
| 5 | 技能改名 | 物化目录同步迁移；frontmatter name == 目录名 |
| 6 | 会话委派子代理 | 子代理泳道事件（subagent.start/delta/end）在面板展示 |
| 7 | `POST /skills/resync` | DB 与物化目录一致性修复 |

## 六、MCP 与动态组件（M6）

| # | 步骤 | 预期 |
| --- | --- | --- |
| 1 | 添加 stdio MCP（command=powershell） | 拒绝 40001（白名单仅 npx/uvx/python）；路径形式命令同样拒绝 |
| 2 | 添加 stdio MCP（npx） | 创建后 needs_confirm=true，不可直接启用 |
| 3 | 二次确认 | 弹窗展示完整命令；确认后 confirmed_at 落库；命令全文在 audit-logs（mcp.confirm / mcp.create） |
| 4 | 修改命令 | confirmed_at 清空，须重新确认 |
| 5 | 探测 | 工具列表缓存展示；不可达服务返回 503/50301 可读错误 |
| 6 | 启用 http MCP → 会话中调用 | 工具以 mcp_{server}_{tool} 注入主代理 |
| 7 | 左上角组件 | 定位授权 → 显示定位城市天气；拒绝 → 回落设置城市；宠物 writing/idle 两态随最近正文变更切换（阈值 pet_idle_minutes） |
| 8 | Agent 问「杭州天气」 | 内置天气 MCP 工具返回当前天气（Open-Meteo 免 Key） |

## 七、部署与恢复（M7）

| # | 步骤 | 预期 |
| --- | --- | --- |
| 1 | `docker compose up -d` | healthcheck 通过；访问 127.0.0.1:8000 出 SPA |
| 2 | 容器重启 | data 卷保留（小说内容/DB/技能不丢） |
| 3 | 备份恢复：复制 data/ 目录到新环境 → `writer rebuild-index` | 派生索引与 project_index 从工作区重建；novels 登记与文件一致 |
| 4 | app.db 删除后启动 | create_all + alembic stamp head 自动重建（登记表可从工作区扫描重建的口径见手册「十二」） |
| 5 | 全量测试 | `pytest -q` 全绿；`ruff check src tests` 无告警；`npm run build` 通过 |
| 6 | `python main.py` | 后端 8000 + 前端 5173 同时拉起，日志混合输出；Ctrl+C 两端一起优雅退出（无残留进程占端口） |
| 7 | `python main.py --prod` | dist 缺失时自动 npm ci + build；单体访问 127.0.0.1:8000 出 SPA，API 同源 |
| 8 | `python main.py --backend-only` / `--frontend-only` / `--port 9000` | 各模式独立可用；自定义端口时前端代理跟随（VITE_BACKEND） |
| 9 | 无 uv 环境跑 main.py | 回落当前解释器 + PYTHONPATH=src 直跑 uvicorn |

## 自动化测试映射（手册「十一 · 必测清单」→ 测试文件）

| 必测域 | 测试文件 |
| --- | --- |
| 权限矩阵（8 条逐条 + 顺序回归） | `tests/test_permissions.py` |
| WorkspaceGuard（穿越/逃逸/跨项目/summaries 保护） | `tests/test_workspace_guard.py` |
| 大纲解析（严格层级/归一化/序数冲突/缺失分类） | `tests/test_outline_order.py` |
| 卷章顺序派生（回填不改 ID/不写快照/无 reorder 接口） | `tests/test_outline_order.py` `tests/test_volumes_chapters.py` |
| FTS5（二元切分/2字词/bm25/降级/项目过滤） | `tests/test_fts_search.py` |
| 乐观锁 + 修订快照 | `tests/test_content_lock.py` |
| 显式命名 | `tests/test_naming.py` |
| EPUB 结构 | `tests/test_export.py` |
| SSE（seq 单调/Last-Event-ID 续传/41001） | `tests/test_sse_bus.py` `tests/test_agent_e2e.py` |
| 中断（approval 四决策/已结束 run resolve→40902） | `tests/test_agent_interrupts.py` |
| 消息树（分支/active_leaf/活跃路径裁剪） | `tests/test_agent_units.py` `tests/test_agent_interrupts.py` |
| 门禁（G0 两类缺失/G1 锚点与跳过/G3 重生成≤2/快照） | `tests/test_gates.py` |
| 导出/软删除 | `tests/test_export.py` `tests/test_novels_api.py` |
| 选区 AI 改写（四模式/校验/不落盘/审计/错误可读） | `tests/test_rewrite.py` |
| 会话上下文 Chips（pinned 章节/子代理过滤/技能物化/解析与审计） | `tests/test_session_context.py` |
| 子代理装配/技能物化/MCP 白名单/天气（mock） | `tests/test_subagents_skills.py` `tests/test_subagent_assembly.py` `tests/test_mcp_widgets.py` |
| Agent 端到端（假模型：配置→发消息→SSE→审批→取消） | `tests/test_agent_e2e.py`（fixture：`tests/fake_model.py`） |
