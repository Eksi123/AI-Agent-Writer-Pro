# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_agent_interrupts.py
作者: xionggaoqing
时间: 2026-10-27
说明: 中断恢复语义 + 取消 + 消息树分支测试（手册「十一 · 中断/咨询」）。
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage
from tests.test_agent_e2e import (
    _install_fake,
    _post_message,
    _setup_provider_model,
)

from app.core.paths import chapter_file


async def _new_session(client, novel_id: str) -> str:
    return (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]


async def _wait_status(client, run_id: str, statuses: tuple[str, ...], *, tries: int = 80) -> dict:
    for _ in range(tries):
        run = (await client.get(f"/api/v1/agent/runs/{run_id}")).json()["data"]
        if run["status"] in statuses:
            return run
        await asyncio.sleep(0.1)
    pytest.fail(f"等待 {statuses} 超时，当前 status={run['status']} error={run.get('error')}")


def _delete_call(novel_id: str, call_id: str = "call_del") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "delete", "args": {"file_path": f"/projects/{novel_id}/x.md"}, "id": call_id}],
    )


class TestApprovalDecisions:
    """approval 四种 decision 各自恢复语义正确。"""

    async def test_approve_executes_tool(self, client, novel_id, volume_id, chapter_id):
        """approve → 工具真实执行（文件确实被删）。"""

        _pid, model_ref = await _setup_provider_model(client)
        fake = _install_fake(model_ref, [])

        real_vol = volume_id
        real_ch = chapter_id
        real_path = chapter_file(novel_id, real_vol, real_ch)
        assert real_path.exists()

        fake.responses = [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "delete", "args": {"file_path": f"/projects/{novel_id}/chapters/v{real_vol}/c{real_ch}.md"}, "id": "c1"}
                ],
            ),
            AIMessage(content="已删除。"),
        ]

        sid = await _new_session(client, novel_id)
        run_id = await _post_message(client, sid, f"删除 {real_ch}")
        run = await _wait_status(client, run_id, ("awaiting_approval",))
        iid = run["interrupts"][0]["id"]

        resp = await client.post(f"/api/v1/agent/interrupts/{iid}/resolve", json={"decision": "approve"})
        assert resp.json()["code"] == 0
        await _wait_status(client, run_id, ("completed", "failed"))

        # 工具真的执行了（文件被删）
        assert not real_path.exists()

    async def test_edit_replaces_args(self, client, novel_id, volume_id):
        """edit → 用 edited_args 替换参数后执行。"""
        # 建一章供 edit 后的真实目标使用
        ch_resp = await client.post(
            f"/api/v1/volumes/{volume_id}/chapters",
            json={"title": "待删章节", "content": "旧内容"},
        )
        real_ch = ch_resp.json()["data"]["id"]
        real_vol = volume_id

        _pid, model_ref = await _setup_provider_model(client)
        fake = _install_fake(model_ref, [_delete_call(novel_id), AIMessage(content="完成")])

        # 原调用指向不存在的 y.md；edit 改成真实章节文件
        fake.responses = [
            AIMessage(
                content="",
                tool_calls=[{"name": "delete", "args": {"file_path": f"/projects/{novel_id}/y.md"}, "id": "c1"}],
            ),
            AIMessage(content="完成"),
        ]

        sid = await _new_session(client, novel_id)
        run_id = await _post_message(client, sid, "删除文件")
        run = await _wait_status(client, run_id, ("awaiting_approval",))
        iid = run["interrupts"][0]["id"]

        resp = await client.post(
            f"/api/v1/agent/interrupts/{iid}/resolve",
            json={
                "decision": "edit",
                "edited_args": {"file_path": f"/projects/{novel_id}/chapters/v{real_vol}/c{real_ch}.md"},
            },
        )
        assert resp.json()["code"] == 0
        await _wait_status(client, run_id, ("completed", "failed"))


        assert not chapter_file(novel_id, real_vol, real_ch).exists()

    async def test_edit_without_args_rejected(self, client, novel_id):
        """edit 决策必须带 edited_args → 400。"""
        _pid, model_ref = await _setup_provider_model(client)
        _install_fake(model_ref, [_delete_call(novel_id), AIMessage(content="x")])

        sid = await _new_session(client, novel_id)
        run_id = await _post_message(client, sid, "删除")
        run = await _wait_status(client, run_id, ("awaiting_approval",))
        iid = run["interrupts"][0]["id"]

        resp = await client.post(f"/api/v1/agent/interrupts/{iid}/resolve", json={"decision": "edit"})
        assert resp.json()["code"] == 40001

    async def test_respond_returns_message_to_model(self, client, novel_id):
        """respond → 不执行工具，用户消息作为工具结果回给模型。"""
        _pid, model_ref = await _setup_provider_model(client)
        fake = _install_fake(model_ref, [_delete_call(novel_id), AIMessage(content="明白了")])

        sid = await _new_session(client, novel_id)
        run_id = await _post_message(client, sid, "删除 x.md")
        run = await _wait_status(client, run_id, ("awaiting_approval",))
        iid = run["interrupts"][0]["id"]

        resp = await client.post(
            f"/api/v1/agent/interrupts/{iid}/resolve",
            json={"decision": "respond", "message": "文件内容我自己处理了：已归档"},
        )
        assert resp.json()["code"] == 0
        await _wait_status(client, run_id, ("completed", "failed"))

        # 模型收到了 respond 文本（第 2 次调用的输入里）
        last_call = fake.calls[-1]
        joined = "\n".join(str(getattr(m, "content", "")) for m in last_call)
        assert "已归档" in joined

    async def test_invalid_decision_rejected(self, client, novel_id):
        resp = await client.post(
            "/api/v1/agent/interrupts/whatever/resolve", json={"decision": "bogus"}
        )
        assert resp.status_code == 422 or resp.json()["code"] == 40001


class TestMessageTree:
    async def test_branch_switch_and_regenerate(self, client, novel_id):
        """多轮对话后「重新生成」→ 兄弟分支；切 active_leaf → 查看历史版本。"""
        _pid, model_ref = await _setup_provider_model(client)
        _install_fake(
            model_ref,
            [
                AIMessage(content="回答A"),
                AIMessage(content="回答B"),
                AIMessage(content="回答B改"),
            ],
        )

        sid = await _new_session(client, novel_id)

        # 第 1 轮
        run1 = await _post_message(client, sid, "问题A")
        await _wait_status(client, run1, ("completed", "failed"))
        # 第 2 轮（接在 active_leaf 之后）
        run2 = await _post_message(client, sid, "问题B")
        await _wait_status(client, run2, ("completed", "failed"))

        tree = (await client.get(f"/api/v1/agent/sessions/{sid}/messages")).json()["data"]
        user_b = next(m for m in tree["items"] if m["role"] == "user" and m["content"] == "问题B")
        assert user_b["parent_id"] is not None  # 非 root，可建兄弟分支

        # 重新生成第 2 轮：以「问题B」的父节点为新消息的父 → 兄弟分支
        run3 = await _post_message(client, sid, "问题B改", parent_id=user_b["parent_id"])
        await _wait_status(client, run3, ("completed", "failed"))

        tree = (await client.get(f"/api/v1/agent/sessions/{sid}/messages")).json()["data"]
        siblings = [
            m for m in tree["items"] if m["role"] == "user" and m["parent_id"] == user_b["parent_id"]
        ]
        assert len(siblings) == 2
        assert {m["content"] for m in siblings} == {"问题B", "问题B改"}
        assert siblings[0]["branch_count"] == 2

        # 切回旧分支（问题B）
        resp = await client.post(
            f"/api/v1/agent/sessions/{sid}/branch", json={"message_id": user_b["id"]}
        )
        assert resp.json()["code"] == 0
        assert resp.json()["data"]["active_leaf_id"] == user_b["id"]

        tree = (await client.get(f"/api/v1/agent/sessions/{sid}/messages")).json()["data"]
        active_ids = {m["id"] for m in tree["items"] if m["in_active_path"]}
        assert user_b["id"] in active_ids
        # 新分支（问题B改）不在活跃路径上
        other = next(m for m in siblings if m["content"] == "问题B改")
        assert other["id"] not in active_ids

    async def test_branch_unknown_message(self, client, novel_id):
        sid = await _new_session(client, novel_id)
        resp = await client.post(f"/api/v1/agent/sessions/{sid}/branch", json={"message_id": "nope"})
        assert resp.json()["code"] == 40401


class TestCancel:
    async def test_cancel_unknown_run(self, client):
        resp = await client.post("/api/v1/agent/runs/nope/cancel")
        assert resp.json()["code"] == 40401

    async def test_cancel_finished_run(self, client, novel_id):
        _pid, model_ref = await _setup_provider_model(client)
        _install_fake(model_ref, [AIMessage(content="快答")])
        sid = await _new_session(client, novel_id)
        run_id = await _post_message(client, sid, "hi")
        await _wait_status(client, run_id, ("completed", "failed"))

        resp = await client.post(f"/api/v1/agent/runs/{run_id}/cancel")
        data = resp.json()["data"]
        assert data["cancelled"] is False

    async def test_cancel_slow_run(self, client, novel_id):
        """慢运行（模型 sleep）中途取消 → run 状态 cancelled。"""
        _pid, model_ref = await _setup_provider_model(client)
        slow = _install_fake(model_ref, [AIMessage(content="慢答")])
        slow.sleep = 2.0  # 模型调用耗 2s

        sid = await _new_session(client, novel_id)
        run_id = await _post_message(client, sid, "慢任务")

        await asyncio.sleep(0.5)
        resp = await client.post(f"/api/v1/agent/runs/{run_id}/cancel")
        assert resp.json()["data"]["cancelled"] is True

        run = await _wait_status(client, run_id, ("cancelled", "completed", "failed"))
        assert run["status"] == "cancelled"


class TestConcurrentMessageRejected:
    async def test_second_message_while_running_409(self, client, novel_id):
        _pid, model_ref = await _setup_provider_model(client)
        slow = _install_fake(model_ref, [AIMessage(content="慢答")])
        slow.sleep = 1.5

        sid = await _new_session(client, novel_id)
        await _post_message(client, sid, "第一条")
        await asyncio.sleep(0.4)

        resp = await client.post(
            f"/api/v1/agent/sessions/{sid}/messages", json={"content": "第二条"}
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == 40902
