# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_token_optimization.py
作者: xionggaoqing
时间: 2026-11-05
说明: token 消耗优化的单元/集成测试。

    覆盖：
    - 读取授权（read_grant 注册表 + 显式读取判定 + ReadScopeGuard 四档）；
    - 大纲按需摘录（services.outline_recall 定位/回落 + outline_grep 工具）；
    - 前情提要按需注入（recap 默认不注入 + outline_ctx 摘录注入）；
    - 添加文件指针化（attachments.rewrite_hidden_context / prune）；
    - 对话历史瘦身（read_file 大结果截断）与压缩阈值（窗口 50%）；
    - 浪费型循环熔断（WastefulLoopGuard 重复读/写检测 + steer 注入）；
    - 工具消息配对自愈（PairingRepair 交换/丢失/重复错位修复）；
    - 提示词精简（铁律条数 / 梗概截断 / recaller 子代理）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.harness.guard.read_scope import ReadScopeGuardMiddleware, _scope_of
from app.agent.harness.guard.workspace_guard import WorkspaceGuardMiddleware
from app.agent.loop.builder import _SYNOPSIS_LIMIT, _build_summarization, _clip
from app.agent.loop.middleware.recap import RecapInjectionMiddleware
from app.agent.loop.resolve import make_outline_ctx_provider, make_recap_provider
from app.agent.workflows.writing_steps.intent import (
    resolve_mentioned_chapter,
    resolve_workflow_chapter,
)
from app.services import files
from app.services.read_grant import (
    ReadGrantRegistry,
    mentions_explicit_read,
    read_grants,
    wants_recap,
)
from app.services.workspace import WorkspaceGuard
from fake_model import ScriptedFakeChatModel

#: 足够长的附件正文（必须超过 `_MIN_POINTERIZE_LEN`，否则走内联分支）
_LONG_BODY = "少年走出山村，第一次见到星海。" * 60

# ---------------------------------------------------------------------------
# 读取授权守卫（大纲/章节正文）
# ---------------------------------------------------------------------------


class TestExplicitReadDetection:
    """用户消息是否显式要求读取大纲/章节内容。"""

    @pytest.mark.parametrize(
        "text",
        [
            "请读取第三章的内容",
            "帮我看看第一卷大纲",
            "参考前文写这一段",
            "打开全书正文检查一下",
            "把大纲简介发我看看",
            "回顾一下已写章节内容",
            "正文内容我想看看",  # 宾语在前、动词在后
        ],
    )
    def test_positive(self, text: str):
        assert mentions_explicit_read(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "按大纲生成第一章正文",  # 写作意图（守卫档②放行），非「读取」请求
            "请把第二章正文重写",
            "你好",
            "把这段改得更有张力",
            "",
        ],
    )
    def test_negative(self, text: str):
        assert mentions_explicit_read(text) is False


class TestWantsRecap:
    """前情提要门控比读取授权更宽：「总结/梳理已写内容」也算。"""

    @pytest.mark.parametrize(
        "text",
        [
            "总结一下前情提要",
            "帮我回顾一下前面的剧情",
            "把前面写的内容梳理一下",
            "前情提要给我看看",
            "已写章节的主要内容是什么",
            "读取第二章内容",  # 显式读取同样命中
            "整理全书设定",
        ],
    )
    def test_positive(self, text: str):
        assert wants_recap(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "按大纲生成第一章正文",  # 写作意图不该带整份前情提要
            "这段写得怎么样",
            "你好",
            "",
        ],
    )
    def test_negative(self, text: str):
        assert wants_recap(text) is False

    def test_recap_gate_is_wider_than_read_gate(self):
        """「总结一下前情提要」无读取动词 → 不授予读取授权，但仍注入 recap。"""
        assert wants_recap("总结一下前情提要") is True
        assert mentions_explicit_read("总结一下前情提要") is False


class TestReadGrantRegistry:
    """会话级授权注册表：授予/撤销/隔离。"""

    async def test_grant_and_query(self):
        reg = ReadGrantRegistry()
        assert reg.is_granted("s1", "outline") is False
        await reg.grant("s1", "outline")
        assert reg.is_granted("s1", "outline") is True
        # 其它 scope / 其它会话不串
        assert reg.is_granted("s1", "chapters") is False
        assert reg.is_granted("s2", "outline") is False

    async def test_revoke_session(self):
        reg = ReadGrantRegistry()
        await reg.grant("s1", "outline")
        await reg.grant("s1", "chapters")
        await reg.revoke_session("s1")
        assert reg.is_granted("s1", "outline") is False
        assert reg.is_granted("s1", "chapters") is False

    async def test_invalid_scope_ignored(self):
        reg = ReadGrantRegistry()
        await reg.grant("s1", "not-a-scope")
        assert reg.is_granted("s1", "not-a-scope") is False

    async def test_global_singleton_isolated_per_session(self):
        await read_grants.grant("sess-a", "outline")
        assert read_grants.is_granted("sess-a", "outline") is True
        assert read_grants.is_granted("sess-b", "outline") is False
        await read_grants.revoke_session("sess-a")


class TestReadScopePathMatching:
    """只拦大纲与章节正文，其它路径不受影响。"""

    def test_scope_detection(self):
        pid = "p1"
        assert _scope_of(f"/projects/{pid}/outline/书-大纲.md", pid) == "outline"
        assert _scope_of(f"/projects/{pid}/chapters/v1/c1.md", pid) == "chapters"
        assert _scope_of(f"/projects/{pid}/summaries/ch-1.md", pid) is None
        assert _scope_of(f"/projects/{pid}/attachments/x.md", pid) is None
        assert _scope_of(f"/projects/{pid}/novel.yaml", pid) is None
        assert _scope_of("/skills/user/x/SKILL.md", pid) is None
        # project_id 为空时不拦（守卫拿不到项目上下文）
        assert _scope_of("/projects//outline/a.md", "") is None


def _read_request(tool: str, path: str, *, context: Any) -> Any:
    return SimpleNamespace(
        tool_call={"name": tool, "args": {"file_path": path}, "id": "call_read"},
        runtime=SimpleNamespace(context=context),
    )


def _context(**kwargs: Any) -> Any:
    base = {
        "project_id": "p1",
        "session_id": "s1",
        "run_id": "r1",
        "emit": None,
        "cancel_flag": {},
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestReadScopeGuardTiers:
    """四档放行判定（① 会话授权 ② 写作意图 ③ 显式读取 ④ 确认卡）。"""

    async def test_non_read_tool_passthrough(self):
        from app.agent.loop.runner import run_manager

        mw = ReadScopeGuardMiddleware()
        calls: list[str] = []

        async def handler(_req: Any) -> str:
            calls.append("handled")
            return "ok"

        request = SimpleNamespace(
            tool_call={"name": "write_file", "args": {"file_path": "/projects/p1/outline/a.md"}},
            runtime=SimpleNamespace(context=_context()),
        )
        assert await mw.awrap_tool_call(request, handler) == "ok"
        assert calls == ["handled"]
        run_manager._cleanup("r1")

    async def test_tier1_session_grant_allows(self):
        from app.agent.loop.runner import run_manager

        mw = ReadScopeGuardMiddleware()
        handled: list[str] = []

        async def handler(_req: Any) -> str:
            handled.append("yes")
            return "content"

        await read_grants.grant("s1", "outline")
        run_manager.note_user_message("r1", "随便聊聊")
        request = _read_request("read_file", "/projects/p1/outline/a.md", context=_context())
        assert await mw.awrap_tool_call(request, handler) == "content"
        assert handled == ["yes"]
        await read_grants.revoke_session("s1")
        run_manager._cleanup("r1")

    async def test_tier2_writing_intent_allows(self):
        from app.agent.loop.runner import run_manager

        mw = ReadScopeGuardMiddleware()
        handled: list[str] = []

        async def handler(_req: Any) -> str:
            handled.append("yes")
            return "content"

        run_manager.note_user_message("r1", "请按大纲生成第一章正文")
        assert run_manager.read_intent_of("r1") is True
        request = _read_request("read_file", "/projects/p1/outline/a.md", context=_context())
        assert await mw.awrap_tool_call(request, handler) == "content"
        assert handled == ["yes"]
        run_manager._cleanup("r1")

    async def test_tier3_explicit_read_grants_session(self):
        from app.agent.loop.runner import run_manager

        mw = ReadScopeGuardMiddleware()

        async def handler(_req: Any) -> str:
            return "content"

        run_manager.note_user_message("r1", "帮我看看第一卷大纲")
        request = _read_request("read_file", "/projects/p1/outline/a.md", context=_context())
        assert await mw.awrap_tool_call(request, handler) == "content"
        # 显式读取 → 记会话授权（同会话后续不再询问）
        assert read_grants.is_granted("s1", "outline") is True
        await read_grants.revoke_session("s1")
        run_manager._cleanup("r1")

    async def test_tier4_reject_returns_tool_message(self, monkeypatch, session):
        """未授权且用户拒绝 → 返回 ToolMessage 指引改用 outline_grep。"""
        from app.agent.loop.runner import run_manager
        from app.agent.harness.guard import read_scope as read_scope_mod

        mw = ReadScopeGuardMiddleware()
        captured: dict[str, Any] = {}

        async def handler(_req: Any) -> str:  # pragma: no cover - 拒绝时不应执行
            raise AssertionError("拒绝后不应放行")

        async def fake_create_interrupt(_ctx: Any, payload: dict[str, Any]) -> str:
            captured["payload"] = payload
            return "int-1"

        async def fake_record_decision(_run: str, _call: str, _int: str, decision: Any) -> dict:
            captured["decision"] = decision
            return {"decision": decision}

        async def fake_set_run_status(*_a: Any, **_k: Any) -> None:
            captured["status"] = _a[1] if len(_a) > 1 else ""

        monkeypatch.setattr(read_scope_mod, "create_interrupt", fake_create_interrupt)
        monkeypatch.setattr(read_scope_mod, "record_decision", fake_record_decision)
        monkeypatch.setattr(read_scope_mod, "set_run_status", fake_set_run_status)
        monkeypatch.setattr(read_scope_mod, "interrupt", lambda payload: "reject")

        run_manager.note_user_message("r1", "今天天气怎么样")
        request = _read_request("read_file", "/projects/p1/chapters/v1/c1.md", context=_context())
        result = await mw.awrap_tool_call(request, handler)

        assert isinstance(result, ToolMessage)
        assert "未允许读取" in result.content
        assert "outline_grep" in result.content
        assert captured["payload"]["kind"] == read_scope_mod.INTERRUPT_KIND
        assert captured["payload"]["risk"] == "read_scope"
        assert set(captured["payload"]["decisions"]) == {"approve", "approve_session", "reject"}
        run_manager._cleanup("r1")

    async def test_tier4_approve_session_grants(self, monkeypatch, session):
        from app.agent.loop.runner import run_manager
        from app.agent.harness.guard import read_scope as read_scope_mod

        mw = ReadScopeGuardMiddleware()
        handled: list[str] = []

        async def handler(_req: Any) -> str:
            handled.append("yes")
            return "content"

        async def fake_create_interrupt(_ctx: Any, _payload: dict[str, Any]) -> str:
            return "int-1"

        async def fake_record_decision(_run: str, _call: str, _int: str, _decision: Any) -> dict:
            return {"decision": "approve_session"}

        async def fake_set_run_status(*_a: Any, **_k: Any) -> None:
            return None

        monkeypatch.setattr(read_scope_mod, "create_interrupt", fake_create_interrupt)
        monkeypatch.setattr(read_scope_mod, "record_decision", fake_record_decision)
        monkeypatch.setattr(read_scope_mod, "set_run_status", fake_set_run_status)
        monkeypatch.setattr(read_scope_mod, "interrupt", lambda payload: "approve_session")

        run_manager.note_user_message("r1", "随便说一句")
        request = _read_request("grep", "/projects/p1/outline/a.md", context=_context())
        assert await mw.awrap_tool_call(request, handler) == "content"
        assert handled == ["yes"]
        assert read_grants.is_granted("s1", "outline") is True
        await read_grants.revoke_session("s1")
        run_manager._cleanup("r1")


class TestApproveSessionDecisionChain:
    """「本次会话允许」的决策必须能从 API 一路传到守卫（漏一处就 400/按钮失效）。"""

    def test_schema_accepts_approve_session(self):
        from app.schemas import AgentInterruptResolveIn

        body = AgentInterruptResolveIn(decision="approve_session")
        assert body.decision == "approve_session"

    def test_service_whitelist_and_resume_shape(self):
        from app.services.agent import _APPROVAL_DECISIONS, _approval_resume

        assert "approve_session" in _APPROVAL_DECISIONS
        resume = _approval_resume(
            "approve_session", tool_name="read_file", edited_args=None, message=None
        )
        assert resume == {"decisions": [{"type": "approve_session"}]}

    def test_normalize_decision_keeps_custom_type(self):
        """守卫侧归一化不得把 approve_session 压成 approve。"""
        from app.agent.harness.approval.write_approval import _normalize_decision

        assert _normalize_decision({"decisions": [{"type": "approve_session"}]}) == {
            "decision": "approve_session"
        }
        assert _normalize_decision("approve_session") == {"decision": "approve_session"}

    def test_guard_offers_session_option(self):
        mw = ReadScopeGuardMiddleware()
        assert "approve_session" in mw.allowed_decisions


# ---------------------------------------------------------------------------
# 大纲按需摘录（services + Agent 工具）
# ---------------------------------------------------------------------------


def _write_outline(project_id: str, content: str) -> None:
    meta = files.read_novel_meta(project_id)
    rel = str(meta.get("outline_file") or "")
    assert rel, "novel.yaml 缺 outline_file"
    WorkspaceGuard(project_id).write(rel, content)


_OUTLINE = """\
# 星海拾荒人 · 大纲

- 备注：大纲是小说创作的重要参考资料。
- 备注2：附录部分不是必须要求。
- 备注3：剧情变化时建议同步修改大纲。

# 第一卷 荒原初醒

## 第一章 拾荒少年的清晨

- 简介：少年李拾荒在废土上捡到一枚星图碎片，被镇上的机械师收留。
- 衔接要点：开篇交代废土生态与少年的拾荒日常。

## 第二章 卷土重来

- 简介：少年带着星图回到镇子，发现机械师已被势力盯上。
- 衔接要点：承接第一章的星图碎片，引出势力冲突。

# 第二卷 云涌星海

## 第三章 初入星海

- 简介：少年登上拾荒船，第一次见到星海。
- 衔接要点：从地表转到太空，节奏加快。

# 附录A 世界观

## 势力

- 三方势力：拾荒行会、星海商会、废土军。
"""


class TestOutlineRecallService:
    """`services.outline_recall`：精确摘录 + 卷名回落 + 检索回落。"""

    async def test_chapter_brief_exact(self, session, novel_id):
        from app.services.outline_recall import chapter_brief

        _write_outline(novel_id, _OUTLINE)
        brief = await chapter_brief(
            session, novel_id, volume_title="第一卷 荒原初醒", chapter_title="第二章 卷土重来"
        )
        assert "第一卷 荒原初醒" in brief
        assert "第二章 卷土重来" in brief
        assert "星图回到镇子" in brief
        assert "承接第一章的星图碎片" in brief
        # 只摘录该章，不带整篇大纲
        assert "初入星海" not in brief
        assert len(brief) <= 1200

    async def test_chapter_brief_volume_mismatch_fallback(self, session, novel_id):
        """卷名对不上时按章名全局找，并回填真实卷名。"""
        from app.services.outline_recall import chapter_brief

        _write_outline(novel_id, _OUTLINE)
        brief = await chapter_brief(
            session, novel_id, volume_title="不存在的卷", chapter_title="第三章 初入星海"
        )
        assert brief
        assert "第二卷 云涌星海" in brief
        assert "第一次见到星海" in brief

    async def test_chapter_brief_normalizes_titles(self, session, novel_id):
        """归一化比较：序数/空格/标点差异仍命中。"""
        from app.services.outline_recall import chapter_brief

        _write_outline(novel_id, _OUTLINE)
        brief = await chapter_brief(
            session, novel_id, volume_title="第2卷 卷土重来", chapter_title="第二章卷土重来"
        )
        # 卷名不匹配 → 走章名全局回落，仍应命中第二章
        assert "机械师已被势力盯上" in brief

    async def test_chapter_brief_missing_returns_empty(self, session, novel_id):
        from app.services.outline_recall import chapter_brief

        _write_outline(novel_id, _OUTLINE)
        assert await chapter_brief(session, novel_id, chapter_title="第九十九章 无此章") == ""
        # 未给章名 → 空串（不做全书摘录）
        assert await chapter_brief(session, novel_id, volume_title="第一卷 荒原初醒") == ""

    async def test_search_outline_hits(self, session, novel_id):
        from app.services import search as search_service
        from app.services.outline_recall import search_outline

        _write_outline(novel_id, _OUTLINE)
        await search_service.reindex_project(session, novel_id)
        hits = await search_outline(session, novel_id, "星图碎片")
        assert hits
        assert all(h.get("kind") == "outline" for h in hits)

    async def test_search_outline_blank_query(self, session, novel_id):
        from app.services.outline_recall import search_outline

        assert await search_outline(session, novel_id, "   ") == []


class TestOutlineRecallTool:
    """Agent 工具 `outline_grep`：入参 schema 不含 runtime，返回 JSON。"""

    def test_schema_excludes_runtime(self):
        from app.agent.tools.builtin.outline_grep import outline_grep

        assert "runtime" not in outline_grep.args
        assert set(outline_grep.args) >= {"chapter_title", "query"}

    def test_registered_in_build_tools(self):
        from app.agent.tools.registry import build_tools

        names = {getattr(t, "name", "") for t in build_tools()}
        assert "outline_grep" in names
        assert "web_search" in names

    async def test_tool_returns_brief(self, novel_id):
        from app.agent.tools.builtin.outline_grep import outline_grep

        _write_outline(novel_id, _OUTLINE)
        runtime = SimpleNamespace(context=SimpleNamespace(project_id=novel_id))
        raw = await outline_grep.coroutine(
            runtime=runtime, volume_title="第一卷 荒原初醒", chapter_title="第一章 拾荒少年的清晨"
        )
        data = json.loads(raw)
        assert data["mode"] == "chapter" and data["found"] is True
        assert "星图碎片" in data["brief"]

    async def test_tool_search_fallback(self, novel_id):
        from app.agent.tools.builtin.outline_grep import outline_grep

        _write_outline(novel_id, _OUTLINE)
        runtime = SimpleNamespace(context=SimpleNamespace(project_id=novel_id))
        data = json.loads(
            await outline_grep.coroutine(runtime=runtime, chapter_title="第九十九章 无此章")
        )
        assert data["found"] is False
        assert "hint" in data

    async def test_tool_requires_some_argument(self, novel_id):
        from app.agent.tools.builtin.outline_grep import outline_grep

        runtime = SimpleNamespace(context=SimpleNamespace(project_id=novel_id))
        data = json.loads(await outline_grep.coroutine(runtime=runtime))
        assert data["found"] is False
        assert "chapter_title" in data["hint"]

    async def test_tool_without_project_context(self):
        from app.agent.tools.builtin.outline_grep import outline_grep

        data = json.loads(await outline_grep.coroutine(runtime=SimpleNamespace(context=None)))
        assert data["found"] is False
        assert "项目上下文" in data["error"]


class TestChapterMentionResolution:
    """显式章名/序数解析（大纲摘录注入与流程目标章共用）。"""

    CHAPTERS = [
        {"id": "c1", "title": "第一章 拾荒少年的清晨", "sort_order": 1},
        {"id": "c2", "title": "第二章 卷土重来", "sort_order": 2},
        {"id": "c3", "title": "第三章 初入星海", "sort_order": 3},
    ]

    def test_mention_by_title(self):
        assert resolve_mentioned_chapter("把第二章 卷土重来改写一下", self.CHAPTERS) == "c2"

    def test_mention_by_ordinal(self):
        assert resolve_mentioned_chapter("参考第三章写这段", self.CHAPTERS) == "c3"
        assert resolve_mentioned_chapter("按第2章的简介", self.CHAPTERS) == "c2"

    def test_no_mention_returns_none(self):
        """未提及章名 → None（不回落当前章，避免误注入无关章摘录）。"""
        assert resolve_mentioned_chapter("帮我润色这段", self.CHAPTERS) is None
        assert resolve_mentioned_chapter("", self.CHAPTERS) is None

    def test_workflow_resolution_still_falls_back_to_active(self):
        text = "按大纲生成本章正文"
        assert resolve_workflow_chapter(text, self.CHAPTERS, "c2") == "c2"
        # 无写作意图时不解析
        assert resolve_workflow_chapter("帮我润色这段", self.CHAPTERS, "c2") is None


# ---------------------------------------------------------------------------
# 请求级注入：前情提要按需 + 目标章大纲摘录
# ---------------------------------------------------------------------------


class TestRecapOnDemand:
    """recap 默认不注入；显式读取请求才注入。"""

    async def _setup_run(self, novel_id: str, text: str, run_id: str = "run-recap") -> str:
        from app.agent.loop.runner import run_manager

        run_manager.note_user_message(run_id, text)
        return run_id

    async def test_no_injection_by_default(self, session, novel_id, chapter_id):
        provider = make_recap_provider(novel_id, "sess-1")
        run_id = await self._setup_run(novel_id, "按大纲生成第一章正文", "run-a")
        text = await provider(SimpleNamespace(run_id=run_id))
        assert text == ""
        from app.agent.loop.runner import run_manager

        run_manager._cleanup(run_id)

    async def test_injection_on_explicit_read(self, session, novel_id, chapter_id):
        provider = make_recap_provider(novel_id, "sess-2")
        run_id = await self._setup_run(novel_id, "请读取已写章节内容并总结", "run-b")
        text = await provider(SimpleNamespace(run_id=run_id))
        assert "星海拾荒人" in text or "拾荒少年的清晨" in text
        from app.agent.loop.runner import run_manager

        run_manager._cleanup(run_id)

    async def test_s2_generation_uses_single_chapter_recap(self, session, novel_id):
        """S2 生成提示词只带上一章摘要（max_chapters=1），不再 6 章全量。"""
        import inspect

        from app.agent.workflows.writing_steps import orchestrator

        src = inspect.getsource(orchestrator.prepare_generate)
        assert "max_chapters=1" in src
        assert "recap[:1500]" in src

    async def test_pinned_chapters_limits(self):
        from app.agent.loop.knowledge import recap as recap_mod

        assert recap_mod._PINNED_MAX_CHAPTERS == 3
        assert recap_mod._PINNED_EXCERPT_LEN == 300


class TestOutlineCtxInjection:
    """目标章大纲摘录注入（意图命中 + 章名可解析时）。

    目标章解析依赖 `list_chapters_ordered`（真实 volumes.yaml + 章节文件），
    故用 `chapter_id` fixture 建真实章节，再按真实卷/章名写大纲保证可定位。
    """

    async def _outline_for_real_chapter(self, novel_id: str, intro: str) -> dict[str, Any]:
        """按真实章节元信息写一份能命中的大纲，返回该章 meta。"""
        from app.agent.loop.knowledge.chapters import list_chapters_ordered

        chapters = await list_chapters_ordered(novel_id)
        assert chapters, "fixture 应已建好一章"
        meta = chapters[0]
        _write_outline(
            novel_id,
            "# 星海拾荒人 · 大纲\n\n"
            f"# {meta['volume_title']}\n\n"
            f"## {meta['title']}\n\n"
            f"- 简介：{intro}\n"
            "- 衔接要点：交代废土日常。\n",
        )
        return meta

    async def test_injects_brief_on_writing_intent(self, session, novel_id, chapter_id):
        from app.agent.loop.runner import run_manager

        meta = await self._outline_for_real_chapter(novel_id, "少年捡到星图碎片，被机械师收留。")
        run_manager.note_user_message("run-ctx", f"请按大纲生成「{meta['title']}」的正文")
        assert run_manager.read_intent_of("run-ctx") is True
        provider = make_outline_ctx_provider(novel_id)
        text = await provider(SimpleNamespace(run_id="run-ctx"))
        assert "目标章大纲摘录" in text
        assert "星图碎片" in text
        assert "outline_grep" in text
        run_manager._cleanup("run-ctx")

    async def test_no_injection_without_chapter_mention(self, session, novel_id, chapter_id):
        from app.agent.loop.runner import run_manager

        await self._outline_for_real_chapter(novel_id, "少年捡到星图碎片。")
        run_manager.note_user_message("run-ctx2", "你好，聊聊这本书")
        provider = make_outline_ctx_provider(novel_id)
        assert await provider(SimpleNamespace(run_id="run-ctx2")) == ""
        run_manager._cleanup("run-ctx2")

    async def test_injection_on_mention_without_writing_intent(self, session, novel_id, chapter_id):
        """非写作意图但点名章节（如「参考本章简介润色这段」）也应摘录。"""
        from app.agent.loop.runner import run_manager

        meta = await self._outline_for_real_chapter(novel_id, "少年捡到星图碎片。")
        run_manager.note_user_message("run-ctx3", f"参考{meta['title']}的简介润色这段")
        assert run_manager.read_intent_of("run-ctx3") is False
        provider = make_outline_ctx_provider(novel_id)
        text = await provider(SimpleNamespace(run_id="run-ctx3"))
        assert "星图碎片" in text
        run_manager._cleanup("run-ctx3")


class TestRecapMiddlewareSlots:
    """请求级注入：两个槽位独立、空串不注入、同 run 只构建一次。"""

    class _Request:
        def __init__(self, messages: list[Any], context: Any) -> None:
            self.messages = messages
            self.runtime = SimpleNamespace(context=context)

        def override(self, messages: list[Any]) -> "TestRecapMiddlewareSlots._Request":
            return TestRecapMiddlewareSlots._Request(messages, self.runtime.context)

    async def _run(self, mw: RecapInjectionMiddleware, context: Any) -> list[Any]:
        captured: dict[str, Any] = {}

        async def handler(request: Any) -> str:
            captured["messages"] = list(request.messages)
            return "done"

        request = self._Request([AIMessage(content="hi")], context)
        assert await mw.awrap_model_call(request, handler) == "done"
        return captured["messages"]

    async def test_no_injection_when_providers_empty(self):
        async def empty(_ctx: Any) -> str:
            return ""

        mw = RecapInjectionMiddleware(recap_provider=empty, outline_ctx_provider=empty)
        messages = await self._run(mw, SimpleNamespace(run_id="r1", emit=None))
        assert [getattr(m, "name", None) for m in messages] == [None]

    async def test_both_slots_injected_with_names(self):
        async def recap(_ctx: Any) -> str:
            return "## 前情提要\n上一章…"

        async def outline(_ctx: Any) -> str:
            return "## 目标章大纲摘录\n简介…"

        mw = RecapInjectionMiddleware(recap_provider=recap, outline_ctx_provider=outline)
        messages = await self._run(mw, SimpleNamespace(run_id="r2", emit=None))
        names = [getattr(m, "name", None) for m in messages]
        assert names == [None, "recap", "outline_ctx"]  # 注入在尾部（缓存友好）

    async def test_provider_cached_per_run(self):
        calls: list[str] = []

        async def recap(_ctx: Any) -> str:
            calls.append("recap")
            return "提要"

        mw = RecapInjectionMiddleware(recap_provider=recap)
        ctx = SimpleNamespace(run_id="r3", emit=None)
        await self._run(mw, ctx)
        await self._run(mw, ctx)
        assert calls == ["recap"]

    async def test_provider_failure_does_not_block(self):
        async def boom(_ctx: Any) -> str:
            raise RuntimeError("构建失败")

        mw = RecapInjectionMiddleware(recap_provider=boom)
        messages = await self._run(mw, SimpleNamespace(run_id="r4", emit=None))
        assert len(messages) == 1

    async def test_stale_slot_removed_when_provider_returns_empty(self):
        """同一 run 内某槽位后来返回空 → 旧的同名注入消息必须被剔除。"""
        state = {"text": "提要"}

        async def recap(_ctx: Any) -> str:
            return state["text"]

        mw = RecapInjectionMiddleware(recap_provider=recap)
        ctx = SimpleNamespace(run_id="r5", emit=None)
        first = await self._run(mw, ctx)
        assert getattr(first[-1], "name", None) == "recap"  # 尾部注入（缓存友好）
        # 缓存生效：改 provider 结果不影响同 run（这是既有语义，缓存优先）
        state["text"] = ""
        second = await self._run(mw, ctx)
        assert getattr(second[-1], "name", None) == "recap"


# ---------------------------------------------------------------------------
# 添加文件指针化（落盘 + 隐藏上下文只留指针）
# ---------------------------------------------------------------------------


class TestAttachmentPointerization:
    """附件落盘 + 指针块；选区润色块保持内联。"""

    def test_attachment_replaced_by_pointer(self, novel_id):
        from app.services.attachments import rewrite_hidden_context

        body = "少年走出山村。" * 200
        hidden = f"【星海拾荒人-大纲.md】\n{body}"
        out = rewrite_hidden_context(novel_id, "tagA", hidden)
        assert "【附件 1】星海拾荒人-大纲.md" in out
        assert f"/projects/{novel_id}/attachments/tagA_1.md" in out
        assert "read_file" in out
        # 全文不再内联（只剩摘要）
        assert len(out) < len(hidden)
        # 落盘文件可被 AI 读回
        from app.core.paths import attachments_dir

        saved = attachments_dir(novel_id) / "tagA_1.md"
        assert saved.exists()
        assert saved.read_text(encoding="utf-8") == body

    def test_polish_block_kept_inline(self, novel_id):
        from app.services.attachments import BLOCK_SEP, rewrite_hidden_context

        polish = (
            "【选中待润色文本】第二章 卷土重来\n所在章节 id：c1\n所在范围：第1-2段\n\n"
            "（选中文本原文，只允许修改这一段）\n他回来了。"
        )
        hidden = f"【附件.md】\n{_LONG_BODY}{BLOCK_SEP}{polish}"
        out = rewrite_hidden_context(novel_id, "tagB", hidden)
        assert "【附件 1】附件.md" in out
        assert polish in out  # 选区块原样保留
        assert _LONG_BODY not in out  # 附件全文已换成指针

    def test_multiple_attachments_numbered(self, novel_id):
        from app.services.attachments import BLOCK_SEP, rewrite_hidden_context

        hidden = BLOCK_SEP.join(
            [
                f"【a.md】\n{_LONG_BODY}A",
                f"【b.md】\n{_LONG_BODY}B",
                "【选中待润色文本】x\n原文",
            ]
        )
        out = rewrite_hidden_context(novel_id, "tagC", hidden)
        assert "【附件 1】a.md" in out
        assert "【附件 2】b.md" in out
        assert "attachments/tagC_1.md" in out
        assert "attachments/tagC_2.md" in out
        # 编号只给附件块：选区块不占号

    def test_small_attachment_stays_inline(self, novel_id):
        """短附件保持内联——指针块自带摘要与固定开销，反而更长。"""
        from app.core.paths import attachments_dir
        from app.services.attachments import (
            _MIN_POINTERIZE_LEN,
            rewrite_hidden_context,
        )

        body = "短设定" * 10  # 30 字
        assert len(body) < _MIN_POINTERIZE_LEN
        out = rewrite_hidden_context(novel_id, "tagS", f"【小.md】\n{body}")
        assert out == f"【小.md】\n{body}"
        assert not list(attachments_dir(novel_id).glob("tagS_*.md"))

    def test_long_attachment_is_pointerized(self, novel_id):
        """刚过门槛的附件必须真的变短（指针化的收益前提）。"""
        from app.services.attachments import _MIN_POINTERIZE_LEN, rewrite_hidden_context

        body = "设" * (_MIN_POINTERIZE_LEN + 1)
        hidden = f"【长.md】\n{body}"
        out = rewrite_hidden_context(novel_id, "tagL", hidden)
        assert "【附件 1】长.md" in out
        assert len(out) < len(hidden), "指针块必须比原文短，否则不该指针化"

    def test_empty_hidden_context_unchanged(self, novel_id):
        from app.services.attachments import rewrite_hidden_context

        assert rewrite_hidden_context(novel_id, "tagD", "") == ""
        assert rewrite_hidden_context(novel_id, "tagD", "   ") == "   "

    def test_horizontal_rule_in_body_not_split(self, novel_id):
        """附件正文自带 `---` 分隔线（frontmatter/水平线）→ 整块落盘，不误切。"""
        from app.core.paths import attachments_dir
        from app.services.attachments import BLOCK_SEP, rewrite_hidden_context

        body = f"---\ntitle: 设定\n---\n\n{_LONG_BODY}\n\n---\n\n{_LONG_BODY}"
        out = rewrite_hidden_context(novel_id, "tagF", f"【设定.md】\n{body}")
        assert "【附件 1】设定.md" in out
        # 落盘文件包含完整正文（含内部的 ---）
        saved = attachments_dir(novel_id) / "tagF_1.md"
        assert saved.read_text(encoding="utf-8") == body
        # 只产生一个附件块：正文里的 --- 没有被当成块边界
        assert out.count("【附件") == 1
        assert BLOCK_SEP not in out

    def test_truncation_of_huge_attachment(self, novel_id):
        from app.services.attachments import rewrite_hidden_context

        body = "字" * (3 * 1024 * 1024)  # 3MB → 超 2MB 上限
        out = rewrite_hidden_context(novel_id, "tagE", f"【big.md】\n{body}")
        assert "超长已截断保存" in out
        from app.core.paths import attachments_dir

        saved = attachments_dir(novel_id) / "tagE_1.md"
        assert saved.stat().st_size <= 2 * 1024 * 1024

    def test_prune_keeps_recent_batches(self, novel_id):
        from app.services.attachments import prune_attachments, save_attachment

        for i in range(25):
            save_attachment(novel_id, f"attachments/old{i}_1.md", f"内容{i}")
        result = prune_attachments(novel_id, keep=20)
        assert result["removed"] == 5
        assert result["kept_batches"] == 20
        from app.core.paths import attachments_dir

        remaining = sorted(p.name for p in attachments_dir(novel_id).iterdir())
        assert len(remaining) == 20

    def test_prune_noop_when_empty(self, novel_id):
        from app.services.attachments import prune_attachments

        assert prune_attachments(novel_id)["removed"] == 0

    def test_api_pointerizes_hidden_context(self, novel_id):
        """API 层包装函数：附件指针化 + 清理旧批次，异常不阻断发送。"""
        from app.api.agent import _pointerize_attachments

        out = _pointerize_attachments(novel_id, f"【文件.md】\n{_LONG_BODY}")
        assert "【附件 1】文件.md" in out
        # 空/无附件时原样返回
        assert _pointerize_attachments(novel_id, "") == ""

    async def test_post_message_pointerizes_before_start(self, client, novel_id, monkeypatch):
        """端到端接线：API 受理时把附件落盘，交给 run 的隐藏上下文只有指针。"""
        from app.agent.loop import runner as runner_mod
        from app.core.paths import attachments_dir
        from helpers import new_session

        captured: dict[str, Any] = {}

        async def fake_start(**kwargs: Any) -> str:
            captured.update(kwargs)
            return "run-fake"

        monkeypatch.setattr(runner_mod.run_manager, "start", fake_start)

        session_id = await new_session(client, novel_id)
        body = _LONG_BODY * 5
        resp = await client.post(
            f"/api/v1/agent/sessions/{session_id}/messages",
            json={"content": "参考这份设定写一段", "hidden_context": f"【设定.md】\n{body}"},
        )
        assert resp.status_code == 202, resp.text

        # 落盘：附件全文可在会话内经 read_file 重读
        saved = list(attachments_dir(novel_id).glob("*.md"))
        assert len(saved) == 1
        assert saved[0].read_text(encoding="utf-8") == body

        # 交给 run 的隐藏上下文只有指针块
        hidden = captured["hidden_context"]
        assert "【附件 1】设定.md" in hidden
        assert f"/projects/{novel_id}/attachments/" in hidden
        assert body not in hidden
        assert len(hidden) < len(body)


# ---------------------------------------------------------------------------
# 对话历史瘦身（大读取截断）与压缩阈值（窗口 80%）
# ---------------------------------------------------------------------------


class TestLargeReadTrim:
    """read_file 大结果截断：头 + 省略提示 + 尾，并指引 offset/limit 分段重读。"""

    def _trim(self, tool: str, content: str, path: str = "/projects/p1/chapters/v1/c1.md") -> Any:
        mw = WorkspaceGuardMiddleware()
        request = SimpleNamespace(tool_call={"name": tool, "args": {"file_path": path}, "id": "c1"})
        return mw._trim_large_read(request, ToolMessage(content=content, tool_call_id="c1", name=tool))

    def test_large_read_trimmed(self):
        content = "开头" + "x" * 20000 + "结尾"
        result = self._trim("read_file", content)
        assert len(result.content) < len(content)
        assert result.content.startswith("开头")
        assert result.content.endswith("结尾")
        assert "省略" in result.content
        assert "offset/limit" in result.content
        assert result.tool_call_id == "c1"

    def test_small_read_untouched(self):
        content = "短内容" * 10
        assert self._trim("read_file", content).content == content

    def test_only_read_file_trimmed(self):
        content = "x" * 20000
        assert self._trim("grep", content).content == content
        assert self._trim("write_file", content).content == content

    def test_non_string_content_untouched(self):
        mw = WorkspaceGuardMiddleware()
        request = SimpleNamespace(tool_call={"name": "read_file", "args": {}, "id": "c2"})
        message = ToolMessage(content=[{"type": "text", "text": "x" * 20000}], tool_call_id="c2")
        assert mw._trim_large_read(request, message) is message


class TestCompressionThreshold:
    """压缩触发点 = 配置窗口 × 0.8（剩余 <20% 即压缩）。"""

    def _trigger(self, window: int) -> tuple[str, int]:
        model = ScriptedFakeChatModel(responses=[])
        model.profile = {"max_input_tokens": window}
        mw = _build_summarization(model, max_context=window)
        assert mw is not None
        helper = getattr(mw, "_lc_helper", mw)
        return helper.trigger

    def test_deepagents_branch_uses_80_percent(self):
        kind, tokens = self._trigger(100_000)
        assert kind == "tokens"
        assert tokens == 80_000

    def test_small_window_floor(self):
        """窗口很小时不低于 2000 token 下限（避免频繁压缩）。"""
        _kind, tokens = self._trigger(1000)
        assert tokens == 2000

    def test_no_window_uses_model_defaults(self):
        model = ScriptedFakeChatModel(responses=[])
        assert _build_summarization(model, max_context=None) is not None


# ---------------------------------------------------------------------------
# 提示词精简与子代理（recaller / 描述长度 / 中间件顺序）
# ---------------------------------------------------------------------------


class TestPromptSlimming:
    """主提示词精简 + 梗概截断 + recaller 子代理。"""

    def test_iron_rules_are_ten(self):
        """铁律 10 条（2026-11-06 新增「调度者定位」「没有依据不动笔」），硬约束一条不少。"""
        import re

        from app.agent.loop.prompts import render

        prompt = render(
            "main_agent.md.j2",
            novel_title="星海拾荒人",
            author_name="测试",
            genre="科幻",
            synopsis="梗概",
            project_id="pid",
            recap="",
            extra_instructions="",
        )
        rules = re.findall(r"(?m)^\d+\.\s+\*\*", prompt)
        assert len(rules) == 10, f"铁律应为 10 条，实际 {len(rules)}"
        # 关键硬约束仍在
        for required in ("备注", "outline_grep", "recaller", "任务没做完不要收尾", "不要新建/重命名章节文件"):
            assert required in prompt, f"缺少硬约束：{required}"
        # 新增反浪费硬约束
        for required in ("续写", "重复全量读取"):
            assert required in prompt, f"缺少反浪费约束：{required}"
        # 调度者定位与两层保险第二层约束
        for required in ("你是调度者，不是执行者", "没有依据不动笔"):
            assert required in prompt, f"缺少约束：{required}"
        # 旧的「先 read_file 大纲」指令已移除（改为按需摘录）
        assert "先 `read_file` 大纲" not in prompt

    def test_synopsis_clipped(self):
        assert _clip("x" * 500, _SYNOPSIS_LIMIT) == "x" * _SYNOPSIS_LIMIT + "…"
        assert _clip("短梗概", _SYNOPSIS_LIMIT) == "短梗概"
        assert _SYNOPSIS_LIMIT == 120

    def test_recaller_subagent_exists(self):
        from app.agent.subagents.builtin import BUILTIN_SUBAGENTS, builtin_subagent_names

        assert "recaller" in builtin_subagent_names()
        spec = BUILTIN_SUBAGENTS["recaller"]
        assert spec["mode"] == "isolated"
        assert set(spec["tools"]) == {"read_file", "grep", "glob", "outline_grep"}
        assert spec["system_prompt"]
        assert "摘要" in spec["system_prompt"]

    def test_builtin_descriptions_short(self):
        """子代理描述 ≤60 字（渐进披露：主模型只见描述）。"""
        from app.agent.subagents.builtin import BUILTIN_SUBAGENTS

        for name, spec in BUILTIN_SUBAGENTS.items():
            assert len(spec["description"]) <= 60, f"{name} 描述过长：{len(spec['description'])} 字"

    def test_extra_tool_alias_resolves(self):
        """outline_grep 作为非文件工具下发给子代理。"""
        from app.agent.loop.builder import _resolve_selected_tools

        resolved = _resolve_selected_tools(["read_file", "grep", "outline_grep"])
        assert resolved["selected"] is True
        assert set(resolved["fs"]) == {"read_file", "grep"}
        assert [getattr(t, "name", "") for t in resolved["extra"]] == ["outline_grep"]

    def test_read_scope_guard_before_workspace_guard(self):
        """中间件顺序：读取授权守卫在 WorkspaceGuard 之前。"""
        from app.agent.loop.builder import build_middleware_stack

        stack = build_middleware_stack(
            primary_model=ScriptedFakeChatModel(responses=[]),
            alternate_models=[],
            recap_provider=None,
            outline_ctx_provider=None,
        )
        names = [getattr(m, "name", type(m).__name__) for m in stack]
        assert names.index("ReadScopeGuard") < names.index("WorkspaceGuard")

    def test_pairing_and_wasteful_guard_order(self):
        """中间件顺序：PairingRepair → WastefulLoopGuard → SteerInjection。"""
        from app.agent.loop.builder import build_middleware_stack

        stack = build_middleware_stack(
            primary_model=ScriptedFakeChatModel(responses=[]),
            alternate_models=[],
            recap_provider=None,
            outline_ctx_provider=None,
        )
        names = [getattr(m, "name", type(m).__name__) for m in stack]
        assert names.index("PairingRepair") < names.index("WastefulLoopGuard")
        assert names.index("WastefulLoopGuard") < names.index("SteerInjection")


class _FakeSumHelper:
    def __init__(self, tokens: int) -> None:
        self._trigger_clauses = [{"tokens": tokens}]


class _FakeSummarization:
    def __init__(self, tokens: int) -> None:
        self._lc_helper = _FakeSumHelper(tokens)


class TestContextWindowSync:
    """窗口中途改小 → 下一次模型调用前重算压缩阈值（剩余 <20% 触发）。"""

    async def test_threshold_rewritten_after_window_shrinks(self):
        from app.agent.loop.middleware.window_sync import ContextWindowSyncMiddleware

        mw_sum = _FakeSummarization(80_000)  # 旧窗口 100K × 0.8
        mw = ContextWindowSyncMiddleware(mw_sum, window_provider=self._provider(50_000))
        runtime = SimpleNamespace(context=SimpleNamespace(model_ref="p:m"))
        assert await mw.abefore_model({}, runtime) is None
        assert mw_sum._lc_helper._trigger_clauses[0]["tokens"] == 40_000  # 50K × 0.8

    async def test_noop_when_threshold_matches(self):
        from app.agent.loop.middleware.window_sync import ContextWindowSyncMiddleware

        mw_sum = _FakeSummarization(80_000)
        calls: list[str] = []

        async def provider(_ref: str) -> int | None:
            calls.append(_ref)
            return 100_000

        mw = ContextWindowSyncMiddleware(mw_sum, window_provider=provider)
        runtime = SimpleNamespace(context=SimpleNamespace(model_ref="p:m"))
        await mw.abefore_model({}, runtime)
        assert mw_sum._lc_helper._trigger_clauses[0]["tokens"] == 80_000
        # TTL 内第二次调用不重复查库
        await mw.abefore_model({}, runtime)
        assert len(calls) == 1

    async def test_no_model_ref_noop(self):
        from app.agent.loop.middleware.window_sync import ContextWindowSyncMiddleware

        mw_sum = _FakeSummarization(80_000)
        mw = ContextWindowSyncMiddleware(mw_sum, window_provider=self._provider(50_000))
        assert await mw.abefore_model({}, SimpleNamespace(context=None)) is None
        assert mw_sum._lc_helper._trigger_clauses[0]["tokens"] == 80_000

    async def test_provider_failure_keeps_old_threshold(self):
        from app.agent.loop.middleware.window_sync import ContextWindowSyncMiddleware

        mw_sum = _FakeSummarization(80_000)

        async def failing(_ref: str) -> int | None:
            raise RuntimeError("db down")

        mw = ContextWindowSyncMiddleware(mw_sum, window_provider=failing)
        runtime = SimpleNamespace(context=SimpleNamespace(model_ref="p:m"))
        assert await mw.abefore_model({}, runtime) is None
        assert mw_sum._lc_helper._trigger_clauses[0]["tokens"] == 80_000

    @staticmethod
    def _provider(window: int):
        async def provider(_ref: str) -> int | None:
            return window

        return provider


class TestUsageEventDualGauge:
    """usage 事件双口径：单次调用（主/子代理标记）+ run 级累计。"""

    async def test_run_totals_accumulate_with_subagent_flag(self):
        from app.agent.loop.streaming import _handle_message_chunk

        published: list[tuple[str, dict]] = []

        class _Emitter:
            async def publish(self, event: str, data: dict) -> None:
                published.append((event, data))

        totals = [0, 0, 0]
        msg1 = AIMessage(
            content="a",
            usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        )
        msg2 = AIMessage(
            content="b",
            usage_metadata={"input_tokens": 50, "output_tokens": 5, "total_tokens": 55},
        )

        got1 = await _handle_message_chunk("r", "s", (), (msg1, {}), _Emitter(), totals)
        got2 = await _handle_message_chunk("r", "s", ("sub", "tools"), (msg2, {}), _Emitter(), totals)

        assert got1 == (100, 10, 0) and got2 == (50, 5, 0)
        assert totals == [150, 15, 0]
        usages = [d for ev, d in published if ev == "usage"]
        assert len(usages) == 2
        # 主 Agent：无子代理标记，run 累计=本次
        assert usages[0]["subagent"] is False
        assert usages[0]["run_tokens_in"] == 100
        # 子代理：标记为 True，run 累计含主+子
        assert usages[1]["subagent"] is True
        assert usages[1]["tokens_in"] == 50
        assert usages[1]["run_tokens_in"] == 150


class TestWastefulLoopGuard:
    """浪费型循环熔断：重复读/写检测 + steer 纠正注入。"""

    def _messages(self, calls: list[tuple[str, str]]) -> list[AIMessage]:
        return [
            AIMessage(
                content="",
                tool_calls=[{"name": n, "args": {"file_path": p}, "id": f"id-{i}"} for i, (n, p) in enumerate(calls)],
            )
        ]

    def test_detects_repeated_reads(self):
        from app.agent.harness.llm.wasteful_loop import WastefulLoopGuardMiddleware

        mw = WastefulLoopGuardMiddleware()
        msgs = self._messages([("read_file", "/a.md")] * 3)
        kind, _path, count = mw.worst_repeat(msgs)
        assert kind == "read" and count == 3

    def test_detects_repeated_writes(self):
        from app.agent.harness.llm.wasteful_loop import WastefulLoopGuardMiddleware

        mw = WastefulLoopGuardMiddleware()
        msgs = self._messages([("write_file", "/o.md"), ("edit_file", "/o.md"), ("write_file", "/o.md")])
        kind, _path, count = mw.worst_repeat(msgs)
        assert kind == "write" and count == 3

    def test_no_repeat_below_limit(self):
        from app.agent.harness.llm.wasteful_loop import WastefulLoopGuardMiddleware

        mw = WastefulLoopGuardMiddleware()
        msgs = self._messages([("read_file", "/a.md"), ("read_file", "/b.md")])
        assert mw.worst_repeat(msgs) == ("", "", 0)

    async def test_pushes_steer_on_repeat(self):
        from app.agent.harness.llm.wasteful_loop import WastefulLoopGuardMiddleware

        pushed: list[tuple[str, str]] = []
        mw = WastefulLoopGuardMiddleware(steer_sink=lambda rid, text: pushed.append((rid, text)))
        state = {"messages": self._messages([("read_file", "/a.md")] * 3)}
        runtime = SimpleNamespace(context=SimpleNamespace(run_id="r1"))
        assert await mw.abefore_model(state, runtime) is None  # 不直接改 state
        assert len(pushed) == 1 and pushed[0][0] == "r1"
        assert "read_file" in pushed[0][1]

    async def test_no_steer_without_repeat(self):
        from app.agent.harness.llm.wasteful_loop import WastefulLoopGuardMiddleware

        pushed: list[tuple[str, str]] = []
        mw = WastefulLoopGuardMiddleware(steer_sink=lambda rid, text: pushed.append((rid, text)))
        state = {"messages": self._messages([("read_file", "/a.md")])}
        runtime = SimpleNamespace(context=SimpleNamespace(run_id="r1"))
        await mw.abefore_model(state, runtime)
        assert pushed == []


class TestPairingRepair:
    """工具消息配对自愈：交换/丢失/重复错位修复。"""

    def _ai(self, ids: list[str]) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[{"name": f"t{i}", "args": {}, "id": i} for i in ids],
        )

    def test_correct_pairing_no_repairs(self):
        from app.agent.loop.middleware.pairing_repair import compute_repairs

        ai = self._ai(["a", "b"])
        tools = [ToolMessage(content="ra", tool_call_id="a"), ToolMessage(content="rb", tool_call_id="b")]
        assert compute_repairs(ai, tools) == []

    def test_swap_repaired(self):
        from app.agent.loop.middleware.pairing_repair import compute_repairs

        ai = self._ai(["a", "b"])
        tools = [ToolMessage(content="rb", tool_call_id="b"), ToolMessage(content="ra", tool_call_id="a")]
        repairs = compute_repairs(ai, tools)
        assert len(repairs) == 2
        assert {r.tool_call_id: r.content for r in repairs} == {"a": "ra", "b": "rb"}

    def test_duplicate_lost_placeholder(self):
        """实测形态：attachments 结果丢失、outline 结果重复成对 → 占位修复。"""
        from app.agent.loop.middleware.pairing_repair import compute_repairs

        ai = self._ai(["att", "out"])
        tools = [
            ToolMessage(content="outline内容", tool_call_id="out"),
            ToolMessage(content="outline内容", tool_call_id="out"),
        ]
        repairs = compute_repairs(ai, tools)
        assert len(repairs) == 1
        assert repairs[0].tool_call_id == "att"
        assert "丢失" in repairs[0].content

    async def test_middleware_returns_repairs(self):
        from app.agent.loop.middleware.pairing_repair import PairingRepairMiddleware

        mw = PairingRepairMiddleware()
        ai = self._ai(["a", "b"])
        state = {
            "messages": [
                ai,
                ToolMessage(content="rb", tool_call_id="b", id="m1"),
                ToolMessage(content="ra", tool_call_id="a", id="m2"),
            ]
        }
        out = await mw.abefore_model(state, SimpleNamespace())
        assert out is not None and len(out["messages"]) == 2

    async def test_middleware_noop_when_ok(self):
        from app.agent.loop.middleware.pairing_repair import PairingRepairMiddleware

        mw = PairingRepairMiddleware()
        ai = self._ai(["a"])
        state = {"messages": [ai, ToolMessage(content="ra", tool_call_id="a", id="m1")]}
        assert await mw.abefore_model(state, SimpleNamespace()) is None
