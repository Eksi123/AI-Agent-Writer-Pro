# -*- coding: utf-8 -*-
"""
模块: app.agent.workflows.writing_steps.orchestrator
文件名: orchestrator.py
作者: xionggaoqing
时间: 2026-10-27
说明: 流程编排状态机（手册「二、(6)」流程图的可执行版）。

    `advance_workflow` 从当前阶段推进到「完成 / 失败 / 需用户决策」三种停点：
    - 需决策 → 抛 `ConsultRequired`（runner 捕获后落 consult 中断 + 暂停）；
    - S2 中触发写入审批 → 由 runner 的事件循环照常处理（本模块把 graph 交给 runner）；
    - 完成 → `summarize_chapter` → 更新派生索引 → stage=done。

    resume：用户决策经 `resolve` 接口落库后，runner 以 `resume_decision` 重新调用
    `advance_workflow`，据 `pending_consult` 类型路由。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop.knowledge import build_recap, summarize_chapter
from app.agent.loop.prompts import render
from app.agent.more.langfuse import create_score, current_trace_id, observation
from app.agent.workflows.writing_steps.state import (
    CONSULT_OUTLINE_MISSING,
    CONSULT_POSTCHECK,
    CONSULT_PRECHECK,
    POSTCHECK_MAX_RETRY,
    ConsultRequired,
    WorkflowState,
    WorkflowTarget,
    llm_json,
)
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.paths import chapter_file
from app.db.base import new_id, utcnow
from app.db.models import ChapterRevision, Novel
from app.db.session import session_scope
from app.services import audit, files
from app.services.outline import normalize_title, parse_outline
from app.services.workspace import WorkspaceGuard

logger = get_logger(__name__)


@dataclass
class WorkflowRunner:
    """固定流程运行所需的注入依赖。"""

    db: AsyncSession
    state: WorkflowState
    #: 直连 LLM（S1/S3 校验 + 摘要）；与 agent 主模型可以相同
    validator_model: Any
    #: S2 用的 deep agent graph（懒构建：None 表示本步不需要）
    graph: Any = None
    graph_config: dict[str, Any] | None = None
    #: SSE 事件发射（workflow.update）
    emit: Any = None
    run_id: str = ""

    async def emit_workflow(self, stage: str, result: str, **extra: Any) -> None:
        if self.emit is None:
            return
        await self.emit.publish(
            "workflow.update",
            {"workflow_stage": stage, "workflow_result": result, "retry_count": self.state.postcheck_retry, **extra},
        )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


async def advance_workflow(runner: WorkflowRunner, *, resume_decision: dict[str, Any] | None = None) -> WorkflowState:
    """推进流程状态机直到停点。返回最新状态（调用方负责落库）。

    Raises:
        ConsultRequired: 需要用户决策（runner 层落 consult 中断并暂停）。
    """
    state = runner.state

    # resume 路由：先应用用户决策
    if resume_decision is not None and state.pending_consult:
        _apply_resume(state, resume_decision)
        state.pending_consult = ""

    while True:
        if state.stage == "S0":
            if state.outcome == "agent_write_outline":
                # 用户要求 Agent 代写大纲小节：交回 runner 执行一次 agent 流，再重查 S0
                return state
            await _wf_outline(runner)
        elif state.stage == "S1":
            await _wf_precheck(runner)
        elif state.stage == "S2":
            # S2 是 agent 流式运行：交回 runner 执行（返回特殊阶段标记）
            return state
        elif state.stage == "S3":
            await _wf_postcheck(runner)
        elif state.stage == "done" or state.stage == "failed":
            return state
        else:  # pragma: no cover - 防御
            state.stage = "failed"
            state.outcome = f"未知流程阶段：{state.stage}"
            return state


def build_outline_draft_prompt(state: WorkflowState) -> str:
    """S0「让 Agent 代写大纲」指令（用户选择 agent_write 时由 runner 送入 agent 流）。"""
    assert state.target is not None
    target = state.target
    instruction = ""
    for item in reversed(state.history):
        if item.get("event") == "agent_write_outline":
            instruction = str(item.get("instruction") or "")
            break
    return render(
        "outline_draft.md.j2",
        volume_title=target.volume_title,
        chapter_title=target.chapter_title,
        instruction=instruction,
    )


def _apply_resume(state: WorkflowState, decision: dict[str, Any]) -> None:
    """把咨询决策应用到状态机。

    决策形状（resolve API 归一化后）：`{decision: option|text, choice?, text?}`。
    自由文本（无 choice）按各阶段语义解释：S0/S1 → 给 Agent 的补充指示；S3 → 重生成指示。
    """
    kind = state.pending_consult
    choice = str(decision.get("choice") or "")
    text = str(decision.get("text") or decision.get("message") or "")

    if kind == CONSULT_OUTLINE_MISSING:
        if choice == "cancel":
            state.stage = "failed"
            state.outcome = "cancelled_at_g0"
        elif choice == "user_edited" and not text:
            state.record("S0", "consult_resolved", choice=choice)
            state.stage = "S0"
            state.outcome = ""
        else:
            # agent_write 或自由文本 → 让 Agent 代写大纲小节（可带用户指示）
            state.record("S0", "consult_resolved", choice=choice or "text", text=text[:200])
            state.stage = "S0"
            state.history.append(
                {"stage": "S0", "event": "agent_write_outline", "instruction": text[:500]}
            )
            state.outcome = "agent_write_outline"

    elif kind == CONSULT_PRECHECK:
        if choice == "cancel":
            state.stage = "failed"
            state.outcome = "cancelled_at_precheck"
        elif choice == "force_skip":
            state.precheck_skipped = True
            state.precheck_passed = False
            state.stage = "S2"
            state.outcome = ""
            state.record("S1", "force_skipped", reason=text[:200])
        else:  # recheck / 自由文本（用户已改大纲）
            state.stage = "S1"
            state.outcome = ""
            state.record("S1", "recheck_requested", note=text[:200])

    elif kind == CONSULT_POSTCHECK:
        if choice == "cancel":
            state.stage = "failed"
            state.outcome = "cancelled_at_postcheck"
        elif choice in ("accept", "user_edited"):
            state.stage = "done"
            state.outcome = f"postcheck_{choice}"
        else:
            # retry 或自由文本 → 带反馈重生成（重试次数已用尽，由用户显式授权再来一次）
            state.postcheck_retry += 1
            state.postcheck_feedback = text or state.postcheck_feedback
            state.stage = "S2"
            state.outcome = ""
            state.record("S3", "user_retry", retry=state.postcheck_retry, text=text[:200])


# ---------------------------------------------------------------------------
# S0 大纲存在性
# ---------------------------------------------------------------------------


async def _wf_outline(runner: WorkflowRunner) -> None:
    state = runner.state
    assert state.target is not None
    target = state.target

    guard = WorkspaceGuard(target.project_id)
    novel = await runner.db.get(Novel, target.project_id)
    outline_rel = ""
    if novel is not None:
        outline_rel = novel.outline_file
    outline_text = guard.read(outline_rel) if outline_rel and guard.exists(outline_rel) else ""
    parsed = parse_outline(outline_text)

    section = parsed.find_chapter(target.volume_title, target.chapter_title)

    if section is None:
        await runner.emit_workflow("S0", "missing_section")
        state.record("S0", "missing_section", volume=target.volume_title, chapter=target.chapter_title)
        state.pending_consult = CONSULT_OUTLINE_MISSING
        raise ConsultRequired(
            CONSULT_OUTLINE_MISSING,
            {
                "question": (
                    f"大纲中没有「{target.volume_title} · {target.chapter_title}」的章简介小节。\n"
                    "大纲章简介是 AI 生成正文的必要前提，请先补充。"
                ),
                "missing_kind": "section",
                "options": [
                    {"value": "agent_write", "label": "让 Agent 代写大纲小节"},
                    {"value": "user_edited", "label": "我自己去「大纲」节点补，补好了再检查"},
                    {"value": "cancel", "label": "取消本次生成"},
                ],
                "allow_text": True,
                "workflow_stage": "S0",
            },
        )

    if section.intro_empty:
        await runner.emit_workflow("S0", "empty_intro")
        state.record("S0", "empty_intro", line=section.line_no)
        state.pending_consult = CONSULT_OUTLINE_MISSING
        raise ConsultRequired(
            CONSULT_OUTLINE_MISSING,
            {
                "question": (
                    f"大纲小节「{target.volume_title} · {target.chapter_title}」（第 {section.line_no} 行）"
                    "存在，但「- 简介：」为空。\n请填写本章剧情要点后再生成。"
                ),
                "missing_kind": "intro",
                "options": [
                    {"value": "agent_write", "label": "让 Agent 代写章简介"},
                    {"value": "user_edited", "label": "我自己填写，填好了再检查"},
                    {"value": "cancel", "label": "取消本次生成"},
                ],
                "allow_text": True,
                "workflow_stage": "S0",
            },
        )

    # 命中：带入简介与衔接要点
    state.intro = section.intro
    state.link = section.link_note
    state.appendix = _appendix_text(parsed)
    state.anchor = _precheck_anchor(parsed, target)
    state.record("S0", "passed", intro_len=len(state.intro))
    await runner.emit_workflow("S0", "passed")
    state.stage = "S1"


def _appendix_text(parsed: Any, limit: int = 3000) -> str:
    parts: list[str] = []
    for app in parsed.appendices:
        parts.append(f"## {app.title}\n" + "\n".join(app.lines))
    text = "\n\n".join(parts)
    return text[:limit]


def _precheck_anchor(parsed: Any, target: WorkflowTarget) -> str:
    """S1 锚点：上一卷末章简介；第一卷返回空（改用附录设定）。"""
    volumes = parsed.volumes
    idx = next(
        (i for i, v in enumerate(volumes) if v.normalized == normalize_title(target.volume_title)),
        -1,
    )
    if idx <= 0:
        return ""
    prev = volumes[idx - 1]
    if not prev.chapters:
        return ""
    last = prev.chapters[-1]
    return (
        f"上一卷「{prev.title}」末章「{last.title}」简介：{last.intro or '（空）'}\n"
        f"衔接要点：{last.link_note or '（未填写）'}"
    )


# ---------------------------------------------------------------------------
# S1 衔接预检（独立 LLM 校验）
# ---------------------------------------------------------------------------


async def _wf_precheck(runner: WorkflowRunner) -> None:
    state = runner.state
    assert state.target is not None

    # 收集本卷全部章简介（预检对象是「该卷各章简介之间的衔接」）
    guard = WorkspaceGuard(state.target.project_id)
    novel = await runner.db.get(Novel, state.target.project_id)
    outline_rel = novel.outline_file if novel is not None else ""
    outline_text = guard.read(outline_rel) if outline_rel and guard.exists(outline_rel) else ""
    parsed = parse_outline(outline_text)
    vol_section = parsed.find_volume(state.target.volume_title)
    chapters = [
        {"title": ch.title, "intro": ch.intro, "link": ch.link_note}
        for ch in (vol_section.chapters if vol_section else [])
    ]
    if not chapters:
        chapters = [{"title": state.target.chapter_title, "intro": state.intro, "link": state.link}]

    prompt = render(
        "workflow_precheck.md.j2",
        volume_title=state.target.volume_title,
        chapters=chapters,
        anchor=state.anchor,
        appendix=state.appendix[:2000],
    )

    with observation("workflow_precheck", metadata={"run_id": runner.run_id}):
        result = await _llm_json(runner.validator_model, prompt)

    passed = bool(result.get("passed", False))
    issues = result.get("issues") or []
    state.precheck_result = result
    state.precheck_passed = passed
    state.record("S1", "passed" if passed else "failed", issues=len(issues), score=result.get("score"))
    trace_id = current_trace_id()
    if trace_id:
        create_score("workflow_precheck_passed", 1 if passed else 0, trace_id=trace_id)

    if passed:
        await runner.emit_workflow("S1", "passed")
        state.stage = "S2"
        return

    await runner.emit_workflow("S1", "failed", issues=issues)
    state.pending_consult = CONSULT_PRECHECK
    suggestions = result.get("suggestions") or []
    issue_lines = "\n".join(
        f"- [{i.get('between', '?')}·{i.get('type', '?')}] {i.get('detail', '')}"
        for i in issues[:10]
        if isinstance(i, dict)
    )
    suggestion_lines = "\n".join(f"- {s}" for s in suggestions[:8])
    raise ConsultRequired(
        CONSULT_PRECHECK,
        {
            "question": (
                "衔接预检未通过。问题清单：\n"
                f"{issue_lines or '- （模型未给出明细）'}\n\n"
                "修改建议：\n"
                f"{suggestion_lines or '- （无）'}\n\n"
                f"总体结论：{result.get('summary', '')}\n"
                "你可以修改大纲后重新检查，或强制跳过（将记录审计）。"
            ),
            "issues": issues,
            "suggestions": suggestions,
            "options": [
                {"value": "recheck", "label": "我已修改大纲，重新检查"},
                {"value": "force_skip", "label": "强制跳过预检"},
                {"value": "cancel", "label": "取消本次生成"},
            ],
            "allow_text": True,
            "workflow_stage": "S1",
        },
    )


# ---------------------------------------------------------------------------
# S2 生成指令（供 runner 在 agent 流上执行）
# ---------------------------------------------------------------------------


async def prepare_generate(runner: WorkflowRunner) -> str:
    """S2 前置：快照旧正文（source=agent）+ 构建前情提要 + 返回生成指令。

    ★生成前必须先快照（手册「二、(6) S2」），保证重生成可回滚。
    自带 session 作用域（调用点在 advance_workflow 的 session_scope 之外）。
    """

    state = runner.state
    assert state.target is not None
    target = state.target

    async with session_scope() as db:
        # 快照旧正文（幂等：同 run 同 version 只快照一次）
        await _snapshot_before_generation(db, target, run_id=runner.run_id)
        # 前情提要
        recap = await build_recap(db, target.project_id, before_chapter_id=target.chapter_id)

    state.record("S2", "prepared", recap_len=len(recap), retry=state.postcheck_retry)
    await runner.emit_workflow("S2", "generating", retry=state.postcheck_retry)

    return render(
        "workflow_generate.md.j2",
        volume_title=target.volume_title,
        chapter_title=target.chapter_title,
        target_path=target.target_path,
        intro=state.intro,
        link=state.link,
        recap=recap[:6000],
        feedback=state.postcheck_feedback,
        word_target=state.word_target,
    )


async def _snapshot_before_generation(db: AsyncSession, target: WorkflowTarget, *, run_id: str) -> None:
    path = chapter_file(target.project_id, target.volume_id, target.chapter_id)
    if not path.exists():
        return
    try:
        meta, content = files.read_chapter(target.project_id, target.volume_id, target.chapter_id)
    except (FileNotFoundError, OSError):
        return
    if not (content or "").strip():
        return  # 空章无需快照


    exists = (
        await db.execute(
            select(ChapterRevision).where(
                ChapterRevision.chapter_id == target.chapter_id,
                ChapterRevision.run_id == run_id,
                ChapterRevision.version == int(meta.get("version") or 1),
            )
        )
    ).scalars().first()
    if exists is not None:
        return  # 本 run 已对该版本快照过（重生成幂等）

    db.add(
        ChapterRevision(
            id=new_id(),
            novel_id=target.project_id,
            chapter_id=target.chapter_id,
            version=int(meta.get("version") or 1),
            content_md=content,
            word_count=files.count_words(content),
            source="agent",
            run_id=run_id,
            created_at=utcnow(),
        )
    )
    await db.flush()
    await audit.record(
        db,
        "chapter.snapshot.before_generation",
        entity_type="chapter",
        entity_id=f"{target.project_id}:{target.chapter_id}",
        detail={"run_id": run_id, "version": int(meta.get("version") or 1)},
    )


# ---------------------------------------------------------------------------
# S3 匹配后检（独立 LLM 校验）
# ---------------------------------------------------------------------------


async def _wf_postcheck(runner: WorkflowRunner) -> None:
    state = runner.state
    assert state.target is not None
    target = state.target

    try:
        _meta, content = files.read_chapter(target.project_id, target.volume_id, target.chapter_id)
    except FileNotFoundError:
        content = ""

    if not content.strip():
        # 生成失败（文件为空）→ 视同不匹配
        state.postcheck_result = {"matched": False, "reason": "章节文件为空，生成未落盘"}
        await _postcheck_fail(runner, empty=True)
        return

    prompt = render(
        "workflow_postcheck.md.j2",
        volume_title=target.volume_title,
        chapter_title=target.chapter_title,
        intro=state.intro,
        link=state.link,
        content=content[:24000],
        feedback=state.postcheck_feedback,
    )

    with observation("workflow_postcheck", metadata={"run_id": runner.run_id, "retry": state.postcheck_retry}):
        result = await _llm_json(runner.validator_model, prompt)

    matched = bool(result.get("matched", False))
    state.postcheck_result = result
    state.record("S3", "passed" if matched else "failed", score=result.get("score"), retry=state.postcheck_retry)
    trace_id = current_trace_id()
    if trace_id:
        create_score("workflow_postcheck_matched", 1 if matched else 0, trace_id=trace_id)
        create_score("chapter_words", files.count_words(content), trace_id=trace_id)

    if matched:
        await runner.emit_workflow("S3", "passed", score=result.get("score"))
        state.stage = "done"
        state.outcome = "passed"
        await _finish(runner, content)
        return

    await _postcheck_fail(runner, empty=False)


async def _postcheck_fail(runner: WorkflowRunner, *, empty: bool) -> None:
    state = runner.state
    result = state.postcheck_result

    if not empty and state.postcheck_retry < POSTCHECK_MAX_RETRY:
        # 自动重生成（≤2 次）：带反馈回到 S2
        state.postcheck_retry += 1
        state.postcheck_feedback = _feedback_text(result)
        state.record("S3", "auto_retry", retry=state.postcheck_retry)
        await runner.emit_workflow("S3", "retry", retry=state.postcheck_retry)
        state.stage = "S2"
        return

    # 重生成用尽 → 咨询
    await runner.emit_workflow("S3", "failed", issues=result.get("missing_points"))
    state.pending_consult = CONSULT_POSTCHECK
    missing = "\n".join(f"- {p}" for p in (result.get("missing_points") or [])[:8])
    deviations = "\n".join(f"- {d}" for d in (result.get("deviations") or [])[:8])
    raise ConsultRequired(
        CONSULT_POSTCHECK,
        {
            "question": (
                f"后检未通过（已自动重生成 {state.postcheck_retry} 次）。结论：{result.get('reason', '')}\n"
                + (f"未覆盖要点：\n{missing}\n" if missing else "")
                + (f"偏离/夹带：\n{deviations}\n" if deviations else "")
                + "你可以：认可现状 / 自己手动修改 / 补充指示后再生成一次。"
            ),
            "result": result,
            "options": [
                {"value": "accept", "label": "认可现状，完成"},
                {"value": "user_edited", "label": "我自己手动修改正文或大纲"},
                {"value": "retry", "label": "补充指示后再生成"},
                {"value": "cancel", "label": "取消"},
            ],
            "allow_text": True,
            "workflow_stage": "S3",
        },
    )


def _feedback_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    if result.get("reason"):
        parts.append(f"结论：{result['reason']}")
    for p in result.get("missing_points") or []:
        parts.append(f"未覆盖要点：{p}")
    for d in result.get("deviations") or []:
        parts.append(f"偏离/夹带：{d}")
    if result.get("incomplete"):
        parts.append("正文不完整（有占位或未写完的段落）")
    return "\n".join(parts)[:2000]


async def _finish(runner: WorkflowRunner, content: str) -> None:
    """完成：摘要生成 → 派生索引 → 审计。"""
    state = runner.state
    assert state.target is not None
    target = state.target


    try:
        summary = await summarize_chapter(
            runner.db,
            target.project_id,
            target.volume_id,
            target.chapter_id,
            model=runner.validator_model,
            force=True,
        )
        state.record("done", "summarized", **{k: summary.get(k) for k in ("skipped", "reason") if k in summary})
    except Exception as exc:  # 摘要失败不阻断完成
        logger.warning("workflow_summarize_failed", error=str(exc))
        state.record("done", "summarize_failed", error=str(exc))

    try:
        from app.services import novels as novels_service
        from app.services import search as search_service

        await novels_service.refresh_project_index(runner.db, target.project_id)
        await search_service.reindex_project(runner.db, target.project_id)
    except Exception as exc:  # pragma: no cover
        logger.warning("workflow_reindex_failed", error=str(exc))

    await audit.record(
        runner.db,
        "workflow.completed",
        entity_type="chapter",
        entity_id=f"{target.project_id}:{target.chapter_id}",
        detail={
            "run_id": runner.run_id,
            "postcheck_retry": state.postcheck_retry,
            "precheck_skipped": state.precheck_skipped,
            "words": files.count_words(content),
        },
    )
    await runner.emit_workflow("done", "completed", words=files.count_words(content))


# ---------------------------------------------------------------------------
# 目标解析与直连 LLM
# ---------------------------------------------------------------------------


async def resolve_target(db: AsyncSession, project_id: str, chapter_id: str) -> WorkflowTarget:
    """由 chapter_id 解析流程目标（卷/章标题、虚拟路径）。"""

    vol_id_found: str | None = None
    meta_found: dict[str, Any] | None = None

    volumes_list = files.read_volumes(project_id)
    for vol in volumes_list:
        vol_id = str(vol.get("id"))
        for path in files.list_chapter_files(project_id, vol_id):
            cid = path.stem[1:]
            if cid == chapter_id:
                vol_id_found = vol_id
                meta_found, _ = files.read_chapter(project_id, vol_id, cid)
                break
        if meta_found is not None:
            break

    if meta_found is None or vol_id_found is None:
        raise NotFoundError(f"章节不存在：{chapter_id}")

    vol = files.find_volume(volumes_list, vol_id_found) or {}
    return WorkflowTarget(
        project_id=project_id,
        volume_id=vol_id_found,
        volume_title=str(vol.get("title") or ""),
        chapter_id=chapter_id,
        chapter_title=str(meta_found.get("title") or ""),
        target_path=f"/projects/{project_id}/chapters/v{vol_id_found}/c{chapter_id}.md",
    )


async def _llm_json(model: Any, prompt: str) -> dict[str, Any]:
    """直连 LLM 做结构化校验（S1/S3）。失败返回 {passed:false} 保守结果。"""
    try:
        resp = await model.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        logger.error("workflow_llm_failed", error=str(exc))
        return {"passed": False, "matched": False, "reason": f"校验模型调用失败：{exc}", "_error": True}

    content = getattr(resp, "content", resp)
    if not isinstance(content, str):
        content = str(content)
    result = llm_json(content)
    if not result:
        # 模型没按 JSON 输出：保守判定 + 附原文供人工查看
        return {"passed": False, "matched": False, "reason": "校验模型输出无法解析为 JSON", "raw": content[:500]}
    return result


def initial_state(target: WorkflowTarget, *, word_target: int = 3000) -> WorkflowState:
    """新建流程状态（S0 起步）。"""
    return WorkflowState(stage="S0", target=target, word_target=word_target)
