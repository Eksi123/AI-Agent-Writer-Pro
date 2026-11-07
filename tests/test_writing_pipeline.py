# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_writing_pipeline.py
作者: xionggaoqing
时间: 2026-11-05
说明: AI 写作链路端到端 + 异常边界自动化用例（假模型驱动，不打真实 LLM）。

    覆盖：
    - 正常路径：纯文本回复 / 工具写入 / usage 事件 / 消息树同步；
    - 失败与恢复：provider 永久失败、重试恢复、无 provider 优雅失败；
    - 取消：运行中取消、已结束取消、取消后孤儿中断清理；
    - 递归上限：触顶优雅收尾（落说明消息、状态 completed）；
    - 收尾守护：写文件后无最终回复 → 系统守护续跑；有回复不续跑；
    - steer：运行中引导注入下一次模型调用、run 结束后 409；
    - 隐藏上下文：入模型请求、不入消息树；
    - 审批中断：delete 弹卡 → 批准续跑 / 二次 resolve 409 / 取消后 resolve 409 /
      终端态 run 不再返回未决中断（孤儿清理）；
    - 读取授权：无意图读大纲弹卡、写作意图放行、拒绝返回 outline_grep 指引；
    - 消息同步边界：用户消息副本去重、system 不入树、空回复不落 assistant；
    - 流程状态机边界：llm_json 容错、状态反序列化容错、未知阶段失败；
    - 意图判定边界：序数变体、负向规则与豁免。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from app.agent.loop.runner import _RECURSION_NOTICE, run_manager
from app.agent.workflows.writing_steps.intent import (
    has_workflow_intent,
    resolve_mentioned_chapter,
)
from app.agent.workflows.writing_steps.state import WorkflowState, llm_json
from app.core.paths import workspace_dir
from fake_model import ScriptedFakeChatModel, ai_text, ai_tool_call
from helpers import (
    delete_call,
    drain_events,
    install_fake,
    new_session,
    post_message,
    setup_provider_model,
    wait_status,
)


class _AlwaysFailChatModel(ScriptedFakeChatModel):
    """每次调用都抛异常（模拟 provider 永久不可用）。"""

    def _next_response(self, messages: list[Any]) -> Any:  # noqa: ARG002
        raise RuntimeError("provider down")


async def _tree(client, session_id: str) -> dict:
    resp = await client.get(f"/api/v1/agent/sessions/{session_id}/messages")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _roles(tree: dict) -> list[str]:
    return [m["role"] for m in tree["items"] if m["in_active_path"]]


def _texts(tree: dict) -> list[str]:
    return [str(m.get("content") or "") for m in tree["items"] if m["in_active_path"]]


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_text_reply_completes_and_syncs(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        fake = install_fake(model_ref, [ai_text("已完成第一章正文。")])
        run_id = await post_message(client, sid, "帮我写第一章")
        events = await drain_events(run_id)
        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "completed"
        assert any(e["event"] == "run.end" for e in events)
        assert any(e["event"] == "usage" for e in events)
        tree = await _tree(client, sid)
        assert _roles(tree) == ["user", "assistant"]
        assert "已完成第一章正文。" in _texts(tree)[1]
        # token 落库（usage 事件 + run 汇总一致）
        assert run["tokens_in"] > 0 and run["tokens_out"] > 0
        assert len(fake.calls) == 1

    async def test_write_file_tool_run_completes(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        path = f"/projects/{novel_id}/chapters/note.md"
        install_fake(
            model_ref,
            [ai_tool_call("write_file", {"file_path": path, "content": "正文内容"}, "c1"), ai_text("已写入。")],
        )
        run_id = await post_message(client, sid, "把这段写入 note.md")
        await drain_events(run_id)
        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "completed"
        host = workspace_dir(novel_id) / "chapters" / "note.md"
        assert host.read_text(encoding="utf-8") == "正文内容"
        calls = (await client.get(f"/api/v1/agent/sessions/{sid}/tool-calls")).json()["data"]
        assert any(c["tool_name"] == "write_file" and c["status"] == "success" for c in calls)

    async def test_user_message_not_duplicated_in_tree(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        install_fake(model_ref, [ai_text("好。")])
        run_id = await post_message(client, sid, "重复检查")
        await drain_events(run_id)
        await wait_status(client, run_id, ("completed",))
        tree = await _tree(client, sid)
        assert _texts(tree).count("重复检查") == 1
        assert "system" not in _roles(tree)


# ---------------------------------------------------------------------------
# 失败与恢复
# ---------------------------------------------------------------------------


class TestFailureAndRecovery:
    async def test_provider_permanent_failure_marks_failed(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        install_fake(model_ref, [])
        from app.agent.loop.llm.factory import register_model_override

        register_model_override(model_ref, _AlwaysFailChatModel(responses=[]))
        run_id = await post_message(client, sid, "写点什么")
        events = await drain_events(run_id)
        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "failed"
        assert "RuntimeError" in (run.get("error") or "")
        assert any(e["event"] == "error" for e in events)
        # 会话状态回落 error（finalize）
        sess = (await client.get("/api/v1/agent/sessions")).json()["data"]
        target = next(s for s in sess if s["id"] == sid)
        assert target["status"] == "error"

    async def test_retry_recovers_transient_failure(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        fake = install_fake(model_ref, [ai_text("重试后成功。")])
        fake.fail_on_call = 1  # 首次调用抛错，重试成功
        run_id = await post_message(client, sid, "写点什么")
        await drain_events(run_id)
        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "completed"
        assert len(fake.calls) >= 2  # 失败一次 + 重试

    async def test_no_provider_fails_gracefully(self, client, novel_id):
        sid = await new_session(client, novel_id)
        run_id = await post_message(client, sid, "写点什么")
        await drain_events(run_id)
        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "failed"
        assert run.get("error")


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------


class TestCancel:
    async def test_cancel_running_run(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        fake = install_fake(model_ref, [ai_text("很长 " * 50)])
        fake.sleep = 0.6
        run_id = await post_message(client, sid, "写长文")
        await wait_status(client, run_id, ("running",))
        resp = await client.post(f"/api/v1/agent/runs/{run_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["data"]["cancelled"] is True
        run = await wait_status(client, run_id, ("cancelled",))
        assert run["status"] == "cancelled"

    async def test_cancel_completed_run_returns_false(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        install_fake(model_ref, [ai_text("完成。")])
        run_id = await post_message(client, sid, "写")
        await drain_events(run_id)
        await wait_status(client, run_id, ("completed",))
        resp = await client.post(f"/api/v1/agent/runs/{run_id}/cancel")
        assert resp.json()["data"]["cancelled"] is False


# ---------------------------------------------------------------------------
# 递归上限
# ---------------------------------------------------------------------------


class TestRecursionLimit:
    async def test_recursion_limit_graceful_notice(self, client, novel_id, monkeypatch):
        monkeypatch.setenv("AGENT_RECURSION_LIMIT", "6")
        from app.core.config import get_settings

        get_settings.cache_clear()
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        # 脚本耗尽后循环最后一条（工具调用）→ 必然触顶
        install_fake(model_ref, [ai_tool_call("ls", {"path": f"/projects/{novel_id}"}, "c1")])
        run_id = await post_message(client, sid, "列目录")
        await drain_events(run_id)
        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "completed"  # 触顶不再 50001
        tree = await _tree(client, sid)
        assert any(_RECURSION_NOTICE[:12] in t for t in _texts(tree))
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 收尾守护（没干完就收尾）
# ---------------------------------------------------------------------------


class TestCompletionNudge:
    async def test_nudge_when_write_without_final_reply(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        path = f"/projects/{novel_id}/chapters/n2.md"
        fake = install_fake(
            model_ref,
            [ai_tool_call("write_file", {"file_path": path, "content": "x"}, "c1"), ai_text("")],
        )
        run_id = await post_message(client, sid, "写 n2")
        await drain_events(run_id)
        await wait_status(client, run_id, ("completed", "failed"))
        # nudge 仍触发额外一轮模型调用（其入参含系统守护提示）……
        assert len(fake.calls) >= 2
        assert any(
            any("系统守护" in str(m.content) for m in call) for call in fake.calls[1:]
        )
        # ……但内部守护消息**永不落消息树**（不作为回复内容展示在会话中）
        tree = await _tree(client, sid)
        assert not any("系统守护" in t for t in _texts(tree))

    async def test_no_nudge_with_final_reply(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        path = f"/projects/{novel_id}/chapters/n3.md"
        install_fake(
            model_ref,
            [ai_tool_call("write_file", {"file_path": path, "content": "x"}, "c1"), ai_text("写好了。")],
        )
        run_id = await post_message(client, sid, "写 n3")
        await drain_events(run_id)
        await wait_status(client, run_id, ("completed", "failed"))
        tree = await _tree(client, sid)
        assert not any("系统守护" in t for t in _texts(tree))


# ---------------------------------------------------------------------------
# steer 与隐藏上下文
# ---------------------------------------------------------------------------


class TestSteerAndHidden:
    async def test_steer_injected_into_next_model_call(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        # 第一轮先工具调用（保持 run 存活），steer 在第二轮模型调用前注入
        fake = install_fake(
            model_ref,
            [
                ai_tool_call("ls", {"path": f"/projects/{novel_id}"}, "cs1"),
                ai_text("第二步。"),
            ],
        )
        run_id = await post_message(client, sid, "分两步写")
        await wait_status(client, run_id, ("running",))
        resp = await client.post(f"/api/v1/agent/runs/{run_id}/steer", json={"content": "改用第二人称"})
        assert resp.status_code == 200
        await drain_events(run_id)
        await wait_status(client, run_id, ("completed",))
        assert len(fake.calls) >= 2
        second_call = fake.calls[1]
        assert any(getattr(m, "content", "") == "改用第二人称" for m in second_call)

    async def test_steer_after_run_end_409(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        install_fake(model_ref, [ai_text("完成。")])
        run_id = await post_message(client, sid, "写")
        await drain_events(run_id)
        await wait_status(client, run_id, ("completed",))
        resp = await client.post(f"/api/v1/agent/runs/{run_id}/steer", json={"content": "改"})
        assert resp.status_code == 409
        assert resp.json()["code"] == 40902

    async def test_hidden_context_in_request_not_in_tree(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        fake = install_fake(model_ref, [ai_text("润色完成。")])
        run_id = await post_message(client, sid, "润色这段", hidden_context="用古风散文润色以下段落")
        await drain_events(run_id)
        await wait_status(client, run_id, ("completed",))
        assert any(
            isinstance(m, SystemMessage) and "用古风散文润色以下段落" in str(m.content)
            for m in fake.calls[0]
        )
        tree = await _tree(client, sid)
        assert not any("用古风散文润色以下段落" in t for t in _texts(tree))
        assert "system" not in _roles(tree)


# ---------------------------------------------------------------------------
# 审批中断（delete 高危）
# ---------------------------------------------------------------------------


class TestApprovalInterrupt:
    async def _start_delete_run(self, client, novel_id, sid):
        _, model_ref = await setup_provider_model(client)
        install_fake(model_ref, [delete_call(novel_id), ai_text("已删除。")])
        return await post_message(client, sid, "删掉 x.md")

    async def test_delete_approve_flow(self, client, novel_id):
        sid = await new_session(client, novel_id)
        run_id = await self._start_delete_run(client, novel_id, sid)
        await wait_status(client, run_id, ("awaiting_approval",))
        tree = await _tree(client, sid)
        pending = tree["latest_run"]["pending_interrupts"]
        assert len(pending) == 1 and (pending[0].get("payload") or {}).get("risk") == "delete"
        resp = await client.post(
            f"/api/v1/agent/interrupts/{pending[0]['id']}/resolve", json={"decision": "approve"}
        )
        assert resp.status_code == 200
        await drain_events(run_id)
        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "completed"
        tree = await _tree(client, sid)
        assert tree["latest_run"]["pending_interrupts"] == []

    async def test_resolve_twice_conflict(self, client, novel_id):
        sid = await new_session(client, novel_id)
        run_id = await self._start_delete_run(client, novel_id, sid)
        await wait_status(client, run_id, ("awaiting_approval",))
        tree = await _tree(client, sid)
        iid = tree["latest_run"]["pending_interrupts"][0]["id"]
        r1 = await client.post(f"/api/v1/agent/interrupts/{iid}/resolve", json={"decision": "reject"})
        assert r1.status_code == 200
        r2 = await client.post(f"/api/v1/agent/interrupts/{iid}/resolve", json={"decision": "approve"})
        assert r2.status_code == 409
        assert r2.json()["code"] == 40902
        await drain_events(run_id)

    async def test_resolve_after_cancel_409_and_orphan_cleaned(self, client, novel_id):
        sid = await new_session(client, novel_id)
        run_id = await self._start_delete_run(client, novel_id, sid)
        await wait_status(client, run_id, ("awaiting_approval",))
        tree = await _tree(client, sid)
        iid = tree["latest_run"]["pending_interrupts"][0]["id"]
        await client.post(f"/api/v1/agent/runs/{run_id}/cancel")
        await wait_status(client, run_id, ("cancelled",))
        # 终端态 run：未决中断不再返回（孤儿清理），resolve 报状态冲突
        tree = await _tree(client, sid)
        assert tree["latest_run"]["pending_interrupts"] == []
        resp = await client.post(f"/api/v1/agent/interrupts/{iid}/resolve", json={"decision": "approve"})
        assert resp.status_code == 409

    async def test_resolve_unknown_interrupt_404(self, client, novel_id):
        resp = await client.post(
            "/api/v1/agent/interrupts/no-such-id/resolve", json={"decision": "approve"}
        )
        assert resp.status_code == 404

    async def test_multiple_pending_interrupts_resolve_one_by_one(self, client, novel_id):
        """模型一次发两个 delete → 审批逐个处理；全部批准后 run 才完成。

        回归：多中断挂起时 resume 必须按 langgraph interrupt id 路由（映射里带上
        已决兄弟的值、未决兄弟保持挂起），否则 langgraph 抛
        「multiple pending interrupts」把 run 打成 failed（用户点批准即报错）。
        并行工具调用下第二张卡的落库时机存在竞态（可能首暂停即两张、也可能
        首张批准续跑后才出现），故按「有卡就批、直到 run 收尾」循环断言。
        """
        sid = await new_session(client, novel_id)
        _, model_ref = await setup_provider_model(client)
        # 预置两个章文件，使 delete 真实生效
        ws = workspace_dir(novel_id)
        for cid in ("c1", "c2"):
            (ws / "chapters" / "v1").mkdir(parents=True, exist_ok=True)
            (ws / "chapters" / "v1" / f"{cid}.md").write_text(
                "---\ntitle: 待删章\n---\n正文。\n", encoding="utf-8"
            )
        install_fake(
            model_ref,
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "delete", "args": {"file_path": f"/projects/{novel_id}/chapters/v1/c1.md"}, "id": "d1"},
                        {"name": "delete", "args": {"file_path": f"/projects/{novel_id}/chapters/v1/c2.md"}, "id": "d2"},
                    ],
                ),
                AIMessage(content="两个文件都已删除。"),
            ],
        )
        run_id = await post_message(client, sid, "把前两章都删掉")
        await drain_events(run_id)

        approved = 0
        for _ in range(8):
            run = await wait_status(client, run_id, ("awaiting_approval", "completed", "failed"))
            if run["status"] != "awaiting_approval":
                break
            # 竞态窗口：resolve 后续跑期间 run 状态仍是 awaiting_approval（收尾才改写），
            # 而未决卡可能已清空/尚未落库 → 轮询直到出现未决卡或 run 进入终态
            pending = []
            for _ in range(60):
                run = (await client.get(f"/api/v1/agent/runs/{run_id}")).json()["data"]
                if run["status"] != "awaiting_approval":
                    break
                tree = await _tree(client, sid)
                pending = tree["latest_run"]["pending_interrupts"]
                if pending:
                    break
                await asyncio.sleep(0.1)
            if not pending:
                continue
            resp = await client.post(
                f"/api/v1/agent/interrupts/{pending[0]['id']}/resolve", json={"decision": "approve"}
            )
            assert resp.status_code == 200, resp.text
            approved += 1

        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "completed", f"逐个批准后应完成，实际 {run['status']}: {run.get('error')}"
        assert approved == 2, "两个 delete 各审批一次"
        assert not (ws / "chapters" / "v1" / "c1.md").exists()
        assert not (ws / "chapters" / "v1" / "c2.md").exists()


# ---------------------------------------------------------------------------
# 读取授权守卫
# ---------------------------------------------------------------------------


class TestReadScopeGuard:
    def _read_outline_call(self, novel_id: str) -> AIMessage:
        return ai_tool_call(
            "read_file", {"file_path": f"/projects/{novel_id}/outline/大纲.md"}, "cr1"
        )

    async def test_read_outline_without_intent_requires_approval(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        install_fake(model_ref, [self._read_outline_call(novel_id), ai_text("读到了。")])
        run_id = await post_message(client, sid, "今天天气怎么样")
        run = await wait_status(client, run_id, ("awaiting_approval", "completed", "failed"))
        assert run["status"] == "awaiting_approval"
        tree = await _tree(client, sid)
        assert (tree["latest_run"]["pending_interrupts"][0].get("payload") or {}).get("risk") == "read_scope"
        iid = tree["latest_run"]["pending_interrupts"][0]["id"]
        resp = await client.post(
            f"/api/v1/agent/interrupts/{iid}/resolve", json={"decision": "approve_session"}
        )
        assert resp.status_code == 200
        await drain_events(run_id)
        await wait_status(client, run_id, ("completed", "failed"))

    async def test_writing_intent_run_passes_without_approval(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        install_fake(model_ref, [self._read_outline_call(novel_id), ai_text("读到了。")])
        run_id = await post_message(client, sid, "按大纲生成第一章正文")
        await drain_events(run_id)
        run = await wait_status(client, run_id, ("completed", "failed", "awaiting_approval"))
        assert run["status"] != "awaiting_approval"

    async def test_reject_returns_outline_grep_guidance(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        install_fake(model_ref, [self._read_outline_call(novel_id), ai_text("好的。")])
        run_id = await post_message(client, sid, "随便聊聊")
        await wait_status(client, run_id, ("awaiting_approval",))
        tree = await _tree(client, sid)
        iid = tree["latest_run"]["pending_interrupts"][0]["id"]
        await client.post(f"/api/v1/agent/interrupts/{iid}/resolve", json={"decision": "reject"})
        await drain_events(run_id)
        await wait_status(client, run_id, ("completed", "failed"))
        tree = await _tree(client, sid)
        assert any("outline_grep" in t for t in _texts(tree))


# ---------------------------------------------------------------------------
# 消息同步边界
# ---------------------------------------------------------------------------


class TestMessageSyncBoundaries:
    async def test_empty_final_reply_not_persisted_as_bubble_content(self, client, novel_id):
        """模型只回空文本：不落带正文的 assistant 气泡（收尾守护会续跑一次）。"""
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        install_fake(model_ref, [ai_text("")])
        run_id = await post_message(client, sid, "你好")
        await drain_events(run_id)
        await wait_status(client, run_id, ("completed", "failed"))
        tree = await _tree(client, sid)
        assistants = [t for r, t in zip(_roles(tree), _texts(tree)) if r == "assistant"]
        assert all(t.strip() == "" for t in assistants)


# ---------------------------------------------------------------------------
# 流程状态机边界
# ---------------------------------------------------------------------------


class TestWorkflowStateEdges:
    def test_llm_json_tolerant(self):
        assert llm_json("") == {}
        assert llm_json("not json") == {}
        assert llm_json("```json\n{\"a\": 1}\n```") == {"a": 1}
        assert llm_json("前缀 {\"a\": 1} 后缀") == {"a": 1}
        assert llm_json("[1, 2]") == {}
        assert llm_json(None) == {}

    def test_state_from_json_garbage(self):
        assert WorkflowState.from_json(None).stage == "S0"
        assert WorkflowState.from_json("garbage").stage == "S0"
        assert WorkflowState.from_json('{"target": 5}').target is None
        s = WorkflowState.from_json('{"stage": "S3", "postcheck_retry": 2}')
        assert s.stage == "S3" and s.postcheck_retry == 2

    def test_state_roundtrip(self):
        from app.agent.workflows.writing_steps.state import WorkflowTarget

        st = WorkflowState(stage="S1", target=WorkflowTarget("p", "v", "卷一", "c", "第一章", "/x"))
        back = WorkflowState.from_json(st.to_json())
        assert back.target.chapter_id == "c" and back.stage == "S1"

    async def test_unknown_stage_fails(self, session):
        from app.agent.workflows.writing_steps.orchestrator import WorkflowRunner, advance_workflow

        state = WorkflowState(stage="S9")
        runner = WorkflowRunner(db=session, state=state, validator_model=None)
        out = await advance_workflow(runner)
        assert out.stage == "failed"
        assert "未知流程阶段" in out.outcome


# ---------------------------------------------------------------------------
# 意图判定边界
# ---------------------------------------------------------------------------


class TestIntentEdges:
    def test_negative_outline_edit_not_intent(self):
        assert has_workflow_intent("修改第一章简介") is False
        assert has_workflow_intent("更新大纲") is False

    def test_negative_exempt_when_body_mentioned(self):
        assert has_workflow_intent("修改简介并生成本章正文") is True

    def test_plain_chat_not_intent(self):
        assert has_workflow_intent("你好") is False
        assert has_workflow_intent("") is False

    def test_ordinal_variants_resolve(self):
        chapters = [
            {"id": f"c{i}", "title": f"第{i}章标题", "sort_order": i} for i in range(1, 13)
        ]
        assert resolve_mentioned_chapter("参考第十二章", chapters) == "c12"
        assert resolve_mentioned_chapter("参考第12章", chapters) == "c12"
        assert resolve_mentioned_chapter("参考第十章", chapters) == "c10"
        assert resolve_mentioned_chapter("参考第二十章", chapters) is None  # 超出章节数
        assert resolve_mentioned_chapter("", chapters) is None

    def test_explicit_title_wins_over_ordinal(self):
        chapters = [
            {"id": "c1", "title": "第三章 星图", "sort_order": 3},
            {"id": "c2", "title": "第一章 拾荒", "sort_order": 1},
        ]
        # 完整标题优先于「第三章」序数（序数会命中 sort_order=3 的第 1 条）
        assert resolve_mentioned_chapter("写第三章 星图", chapters) == "c1"


# ---------------------------------------------------------------------------
# 子代理回传语义（结论进主上下文，中间过程销毁）
# ---------------------------------------------------------------------------


class TestSubagentConclusionRoundtrip:
    async def test_conclusion_returns_as_tool_message_into_main_context(
        self, client, novel_id, monkeypatch
    ):
        """主 Agent 派发 task → 子代理结论以 ToolMessage 回传主上下文并落库；
        子代理中间过程（任务描述/内部消息）不进主消息树。"""
        monkeypatch.setenv("AGENT_SUBAGENTS_ENABLED", "true")
        from app.core.config import get_settings

        get_settings.cache_clear()
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        conclusion = "回传结论：三处伏笔已在第二卷回收。"
        # 脚本交错：主派发 → 子代理结论 → 主最终回复（同一 fake 实例按序消费）
        install_fake(
            model_ref,
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"description": "回顾伏笔回收情况", "subagent_type": "recaller"},
                            "id": "ctask1",
                        }
                    ],
                ),
                ai_text(conclusion),
                ai_text("已汇总子代理结论。"),
            ],
        )
        run_id = await post_message(client, sid, "让 recaller 回顾伏笔")
        await drain_events(run_id)
        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "completed"
        tree = await _tree(client, sid)
        roles = _roles(tree)
        texts = _texts(tree)
        # 结论以 tool 消息进入主上下文并落库
        assert "tool" in roles
        assert any(conclusion in t for t in texts)
        # 主最终回复在结论之后
        assert texts[-1] == "已汇总子代理结论。"
        # 子代理中间过程不进主树：任务描述只作为 tool 调用参数，不单独成 user 消息
        assert texts.count("回顾伏笔回收情况") == 0
        get_settings.cache_clear()

    async def test_subagent_spec_always_has_model(self, client, novel_id):
        """无 model_ref 的子代理回落主模型（create_sub_agent 要求 spec 必带 model）。"""
        from app.agent.loop.builder import build_subagents_from_specs

        sentinel = object()
        specs = [
            {"name": "recaller", "description": "d", "system_prompt": "p", "mode": "isolated",
             "skills": [], "tools": ["read_file"], "model_ref": None}
        ]
        agents = build_subagents_from_specs(specs, {}, default_model=sentinel)
        assert agents[0]["model"] is sentinel


# ---------------------------------------------------------------------------
# run_manager 单元边界
# ---------------------------------------------------------------------------


class TestRunManagerUnits:
    def test_steer_queue_lifecycle(self):
        run_manager.push_steer("r-x", "引导一")
        run_manager.push_steer("r-x", "引导二")
        assert run_manager.take_steer("r-x") == ["引导一", "引导二"]
        assert run_manager.take_steer("r-x") == []

    def test_hidden_context_lifecycle(self):
        run_manager.set_hidden_context("r-h", "润色提示")
        assert run_manager.hidden_context_of("r-h") == "润色提示"
        run_manager._cleanup("r-h")
        assert run_manager.hidden_context_of("r-h") == ""

    def test_note_user_message_sets_read_intent(self):
        run_manager.note_user_message("r-i", "按大纲生成第二章正文")
        assert run_manager.read_intent_of("r-i") is True
        run_manager.note_user_message("r-i2", "今天天气")
        assert run_manager.read_intent_of("r-i2") is False
        run_manager._cleanup("r-i")
        run_manager._cleanup("r-i2")
