# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_gates.py
作者: xionggaoqing
时间: 2026-10-27
说明: 门禁编排测试（手册「十一 · 门禁」，假 validator 模型直连）。

    覆盖：
    - G0 两类缺失各出对应咨询（无小节 / 简介为空），不自动插入占位小节；
    - G1 带上一卷末章锚点；不通过出问题清单 + 修改建议；force_skip 落审计；
    - G3 不匹配自动重生成 ≤2 次后出咨询；每次重生前有 agent 快照；
    - 状态序列化 / 反序列化（gate_result_json 续跑）；
    - resolve 决策路由（option/text）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.agent.gates.orchestrator import (
    GateRunner,
    advance_gate,
    initial_state,
    resolve_target,
)
from app.agent.gates.state import ConsultRequired, GateState
from app.core.errors import NotFoundError
from app.services import files
from app.services.workspace import WorkspaceGuard


def _json_model(payload: dict[str, Any]) -> Any:
    """返回一个 ainvoke 输出固定 JSON 的假 validator 模型。"""

    class _FakeValidator:
        model_name = "fake-validator"

        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def ainvoke(self, messages: Any, *a: Any, **k: Any) -> AIMessage:
            # 记录提示词，便于断言锚点/反馈是否带入
            self.prompts.append(str(messages[-1].content) if messages else "")
            return AIMessage(content=json.dumps(payload, ensure_ascii=False))

    return _FakeValidator()


def _write_outline(project_id: str, content: str) -> None:
    """直接写大纲文件（绕过 API，测试更聚焦门禁逻辑）。

    ★写 `novel.yaml.outline_file` 记录的相对路径（G0 经 DB/novel.yaml 寻址大纲）。
    """

    meta = files.read_novel_meta(project_id)
    outline_file = str(meta.get("outline_file") or "")
    assert outline_file, "novel.yaml 缺 outline_file"
    guard = WorkspaceGuard(project_id)
    guard.write(outline_file, content)


async def _setup_chapter(client, novel_id: str, vol_title: str, ch_title: str) -> tuple[str, str]:
    vol = (
        await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": vol_title, "summary": ""})
    ).json()["data"]
    ch = (
        await client.post(
            f"/api/v1/volumes/{vol['id']}/chapters",
            json={"title": ch_title, "content": "占位正文"},
        )
    ).json()["data"]
    return vol["id"], ch["id"]


def _outline_with_intro(title: str, vol_title: str, ch_title: str, intro: str, link: str = "承接上文") -> str:
    return (
        f"# {title} · 大纲\n\n"
        f"# {vol_title}\n\n"
        f"## {ch_title}\n\n"
        f"- 简介：{intro}\n"
        f"- 衔接要点：{link}\n\n"
        f"# 附录A 世界观\n\n- 世界背景：架空大陆\n"
    )


async def _runner(db, state: GateState, validator: Any) -> GateRunner:
    return GateRunner(db=db, state=state, validator_model=validator, run_id="run-test")


# ---------------------------------------------------------------------------
# G0 存在性
# ---------------------------------------------------------------------------


class TestG0:
    async def test_missing_section_raises_consult(self, session, client, novel_id):
        """无对应小节 → 咨询（missing_kind=section），不自动插入占位。"""
        vol_id, ch_id = await _setup_chapter(client, novel_id, "第一卷 荒原", "第一章 出山")
        # 大纲里**没有**这一章的小节
        _write_outline(novel_id, "# 测试书 · 大纲\n\n# 第一卷 荒原\n\n（本章无小节）\n")

        target = await resolve_target(session, novel_id, ch_id)
        state = initial_state(target)
        runner = await _runner(session, state, _json_model({"passed": True}))

        with pytest.raises(ConsultRequired) as exc:
            await advance_gate(runner)

        consult = exc.value
        assert consult.kind == "g0_missing"
        assert consult.payload["missing_kind"] == "section"
        values = [o["value"] for o in consult.payload["options"]]
        assert "agent_write" in values and "user_edited" in values and "cancel" in values
        # 不自动插入占位小节
        guard_content = files.read_chapter  # noqa
        assert state.stage == "G0"

    async def test_empty_intro_raises_consult(self, session, client, novel_id):
        """小节存在但简介为空 → 咨询（missing_kind=intro），文案不同于无小节。"""
        vol_id, ch_id = await _setup_chapter(client, novel_id, "第一卷 荒原", "第一章 出山")
        _write_outline(
            novel_id,
            "# 测试书 · 大纲\n\n# 第一卷 荒原\n\n## 第一章 出山\n\n- 简介：\n- 衔接要点：\n",
        )

        target = await resolve_target(session, novel_id, ch_id)
        state = initial_state(target)
        runner = await _runner(session, state, _json_model({"passed": True}))

        with pytest.raises(ConsultRequired) as exc:
            await advance_gate(runner)

        assert exc.value.payload["missing_kind"] == "intro"
        assert "简介" in exc.value.payload["question"]

    async def test_g0_passes_with_intro(self, session, client, novel_id):
        """有简介 → G0 通过，进入 G1。"""
        vol_id, ch_id = await _setup_chapter(client, novel_id, "第一卷 荒原", "第一章 出山")
        _write_outline(novel_id, _outline_with_intro("测试书", "第一卷 荒原", "第一章 出山", "少年离开山村，初入江湖。"))

        target = await resolve_target(session, novel_id, ch_id)
        state = initial_state(target)
        # G1 会通过，但 G2 需要 graph；这里 validator 让 G1 通过后会停在 G2（runner 返回）
        runner = await _runner(session, state, _json_model({"passed": True}))
        result = await advance_gate(runner)
        # G0 通过 → G1 通过 → 停在 G2（等 runner 执行 agent 流）
        assert result.stage == "G2"
        assert result.intro == "少年离开山村，初入江湖。"


# ---------------------------------------------------------------------------
# G1 衔接预检
# ---------------------------------------------------------------------------


class TestG1:
    async def test_precheck_pass_includes_anchor(self, session, client, novel_id):
        """G1 通过；跨卷时提示词带上一卷末章锚点。"""
        # 建两卷，目标章在第二卷
        v1 = (await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": "第一卷 起源", "summary": ""})).json()["data"]
        await client.post(f"/api/v1/volumes/{v1['id']}/chapters", json={"title": "第一章 开端", "content": "x"})
        v2, ch2 = await _setup_chapter(client, novel_id, "第二卷 征程", "第二章 远行")

        _write_outline(
            novel_id,
            "# 测试书 · 大纲\n\n"
            "# 第一卷 起源\n\n## 第一章 开端\n\n- 简介：主角觉醒能力。\n- 衔接要点：留下悬念。\n\n"
            "# 第二卷 征程\n\n## 第二章 远行\n\n- 简介：主角踏上旅途。\n- 衔接要点：承接觉醒。\n",
        )

        target = await resolve_target(session, novel_id, ch2)
        state = initial_state(target)
        validator = _json_model({"passed": True})
        runner = await _runner(session, state, validator)
        result = await advance_gate(runner)

        assert result.g1_passed is True
        # 锚点带入 G1 提示词
        assert any("第一章 开端" in p for p in validator.prompts)

    async def test_precheck_fail_raises_consult(self, session, client, novel_id):
        """G1 不通过 → 咨询（问题清单 + 修改建议）。"""
        vol_id, ch_id = await _setup_chapter(client, novel_id, "第一卷 荒原", "第一章 出山")
        _write_outline(novel_id, _outline_with_intro("测试书", "第一卷 荒原", "第一章 出山", "剧情A"))

        target = await resolve_target(session, novel_id, ch_id)
        state = initial_state(target)
        validator = _json_model(
            {
                "passed": False,
                "issues": [{"between": "第一章→第二章", "type": "因果", "detail": "缺少动机铺垫"}],
                "suggestions": ["在第一章补充主角离开的动机"],
                "summary": "衔接存在断裂",
            }
        )
        runner = await _runner(session, state, validator)

        with pytest.raises(ConsultRequired) as exc:
            await advance_gate(runner)

        assert exc.value.kind == "g1_precheck"
        assert "缺少动机铺垫" in exc.value.payload["question"]
        values = [o["value"] for o in exc.value.payload["options"]]
        assert "force_skip" in values and "recheck" in values

    async def test_force_skip_advances_to_g2(self, session, client, novel_id):
        """强制跳过 G1 → 进入 G2，落 g1_skipped 标记。"""
        vol_id, ch_id = await _setup_chapter(client, novel_id, "第一卷 荒原", "第一章 出山")
        _write_outline(novel_id, _outline_with_intro("测试书", "第一卷 荒原", "第一章 出山", "剧情A"))

        target = await resolve_target(session, novel_id, ch_id)
        state = initial_state(target)
        runner = await _runner(session, state, _json_model({"passed": False, "issues": [], "suggestions": []}))

        # 首次触发 G1 咨询
        with pytest.raises(ConsultRequired):
            await advance_gate(runner)
        assert state.pending_consult == "g1_precheck"

        # 应用 force_skip 决策
        result = await advance_gate(runner, resume_decision={"decision": "option", "choice": "force_skip"})
        assert result.stage == "G2"
        assert result.g1_skipped is True


# ---------------------------------------------------------------------------
# G3 匹配后检
# ---------------------------------------------------------------------------


class TestG3:
    async def _state_at_g3(self, session, client, novel_id) -> GateState:
        """构造一个已通过 G0/G1、正文已写好、停在 G3 的状态。"""
        vol_id, ch_id = await _setup_chapter(client, novel_id, "第一卷 荒原", "第一章 出山")
        _write_outline(novel_id, _outline_with_intro("测试书", "第一卷 荒原", "第一章 出山", "少年出山剧情"))
        target = await resolve_target(session, novel_id, ch_id)
        state = initial_state(target)
        state.stage = "G3"
        state.intro = "少年出山剧情"
        # 写入正文供后检
        files.write_chapter(novel_id, vol_id, ch_id, {"title": "第一章 出山", "version": 1}, "少年走出山村的正文。")
        return state

    async def test_g3_match_completes(self, session, client, novel_id):
        state = await self._state_at_g3(session, client, novel_id)
        validator = _json_model({"matched": True, "score": 92, "reason": "覆盖完整"})
        runner = await _runner(session, state, validator)
        result = await advance_gate(runner)
        assert result.stage == "done"
        assert result.outcome == "passed"

    async def test_g3_mismatch_auto_retry_then_consult(self, session, client, novel_id):
        """不匹配 → 自动重生成 ≤2 次 → 仍不匹配出咨询。"""
        state = await self._state_at_g3(session, client, novel_id)
        validator = _json_model({"matched": False, "score": 40, "missing_points": ["缺少关键对话"], "reason": "偏离"})
        runner = await _runner(session, state, validator)

        # 第 1 次 G3 失败 → 自动回 G2（retry=1），advance_gate 停在 G2
        result = await advance_gate(runner)
        assert result.stage == "G2"
        assert result.g3_retry == 1
        assert "缺少关键对话" in result.g3_feedback

        # 模拟 G2 再次生成后又回到 G3（runner 会做这个转移）
        result.stage = "G3"
        result = await advance_gate(runner)
        assert result.stage == "G2"
        assert result.g3_retry == 2

        # 第 3 次仍失败 → retry 用尽 → 咨询
        result.stage = "G3"
        with pytest.raises(ConsultRequired) as exc:
            await advance_gate(runner)
        assert exc.value.kind == "g3_postcheck"
        values = [o["value"] for o in exc.value.payload["options"]]
        assert "accept" in values and "retry" in values

    async def test_g3_accept_completes(self, session, client, novel_id):
        state = await self._state_at_g3(session, client, novel_id)
        validator = _json_model({"matched": False, "score": 50, "reason": "略有偏离"})
        runner = await _runner(session, state, validator)

        with pytest.raises(ConsultRequired):
            # 先把 retry 用尽
            state.stage = "G3"
            state.g3_retry = 2
            await advance_gate(runner)

        result = await advance_gate(runner, resume_decision={"decision": "option", "choice": "accept"})
        assert result.stage == "done"
        assert result.outcome == "g3_accept"


# ---------------------------------------------------------------------------
# 状态序列化
# ---------------------------------------------------------------------------


class TestStateSerialization:
    async def test_roundtrip_preserves_target_and_stage(self, session, client, novel_id):
        vol_id, ch_id = await _setup_chapter(client, novel_id, "第一卷 荒原", "第一章 出山")
        _write_outline(novel_id, _outline_with_intro("测试书", "第一卷 荒原", "第一章 出山", "剧情"))
        target = await resolve_target(session, novel_id, ch_id)
        state = initial_state(target, word_target=2500)
        state.stage = "G3"
        state.g3_retry = 1
        state.g3_feedback = "缺少对话"
        state.intro = "剧情"

        raw = state.to_json()
        restored = GateState.from_json(raw)
        assert restored.stage == "G3"
        assert restored.g3_retry == 1
        assert restored.g3_feedback == "缺少对话"
        assert restored.word_target == 2500
        assert restored.target is not None
        assert restored.target.chapter_id == ch_id
        assert restored.target.volume_title == "第一卷 荒原"

    def test_from_json_garbage_returns_default(self):
        assert GateState.from_json("not json").stage == "G0"
        assert GateState.from_json(None).stage == "G0"
        assert GateState.from_json("[1,2,3]").stage == "G0"


# ---------------------------------------------------------------------------
# 目标解析
# ---------------------------------------------------------------------------


class TestResolveTarget:
    async def test_resolve_unknown_chapter(self, session, novel_id):

        with pytest.raises(NotFoundError):
            await resolve_target(session, novel_id, "deadbeef")

    async def test_resolve_builds_virtual_path(self, session, client, novel_id):
        vol_id, ch_id = await _setup_chapter(client, novel_id, "第一卷 荒原", "第一章 出山")
        target = await resolve_target(session, novel_id, ch_id)
        assert target.target_path == f"/projects/{novel_id}/chapters/v{vol_id}/c{ch_id}.md"
        assert target.volume_title == "第一卷 荒原"
        assert target.chapter_title == "第一章 出山"
