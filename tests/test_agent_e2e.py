# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_agent_e2e.py
作者: xionggaoqing
时间: 2026-10-27
说明: Agent 端到端测试（假模型驱动，手册「十一 · 每里程碑验收口径 M3」）。

    验收链路：配 Provider → 发消息 → SSE 流式 → 审批 → 取消。
    全部用 `ScriptedFakeChatModel`，不打真实 LLM。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from tests.fake_model import ScriptedFakeChatModel

from app.agent.loop.llm.factory import register_model_override
from app.agent.tracing.sse.events import sse_bus


async def _setup_provider_model(client, *, model_id: str = "fake-model") -> tuple[str, str]:
    """建一个 Provider + 默认 Model，返回 (provider_id, model_ref)。密钥用假值（假模型不联网）。"""
    resp = await client.post(
        "/api/v1/llm/providers",
        json={
            "name": "测试厂商",
            "type": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "api_key_env": "OPENAI_API_KEY",
            "api_key_value": "sk-test-fake-key-0000",
        },
    )
    assert resp.status_code == 200, resp.text
    provider = resp.json()["data"]
    provider_id = provider["id"]
    assert provider["api_key_configured"] is True
    assert provider["api_key_masked"].startswith("sk-")
    assert "test-fake" not in provider["api_key_masked"]  # 不回显明文

    resp = await client.post(
        "/api/v1/llm/models",
        json={
            "provider_id": provider_id,
            "model_id": model_id,
            "display_name": "假模型",
            "is_default": True,
        },
    )
    assert resp.status_code == 200, resp.text
    model = resp.json()["data"]
    return provider_id, model["model_ref"]


def _install_fake(model_ref: str, responses: list[Any]) -> ScriptedFakeChatModel:
    fake = ScriptedFakeChatModel(responses=responses)
    register_model_override(model_ref, fake)
    return fake


async def _drain_events(run_id: str, *, timeout: float = 8.0) -> list[dict[str, Any]]:
    """直接从 sse_bus 订阅并收集事件直到 run.end / error。"""
    queue, backlog, _lost, finished = await sse_bus.subscribe(run_id)
    events: list[dict[str, Any]] = [e.to_dict() for e in backlog]
    if finished or any(e["event"] in ("run.end", "error") for e in events):
        await sse_bus.unsubscribe(run_id, queue)
        return events

    try:
        while True:
            item = await asyncio.wait_for(queue.get(), timeout=timeout)
            if item is None:
                break
            events.append(item.to_dict())
            if item.event in ("run.end", "error"):
                break
    finally:
        await sse_bus.unsubscribe(run_id, queue)
    return events


async def _post_message(client, session_id: str, content: str, **kwargs) -> str:
    resp = await client.post(
        f"/api/v1/agent/sessions/{session_id}/messages",
        json={"content": content, **kwargs},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]["run_id"]


# ---------------------------------------------------------------------------
# Provider / Model 管理
# ---------------------------------------------------------------------------


class TestLlmProviderApi:
    async def test_provider_crud_and_masking(self, client):
        provider_id, model_ref = await _setup_provider_model(client)

        resp = await client.get("/api/v1/llm/providers")
        providers = resp.json()["data"]
        assert len(providers) == 1
        assert providers[0]["id"] == provider_id
        assert providers[0]["api_key_configured"] is True
        # 掩码不含明文
        assert "test-fake-key" not in json.dumps(providers[0])

        # 改优先级
        resp = await client.patch(f"/api/v1/llm/providers/{provider_id}", json={"priority": 5})
        assert resp.json()["data"]["priority"] == 5

        # 删除级联删模型
        resp = await client.delete(f"/api/v1/llm/providers/{provider_id}")
        assert resp.json()["data"]["deleted"] is True
        resp = await client.get("/api/v1/llm/models")
        assert resp.json()["data"] == []

    async def test_duplicate_provider_name_rejected(self, client):
        await _setup_provider_model(client)
        resp = await client.post(
            "/api/v1/llm/providers",
            json={"name": "测试厂商", "type": "openai"},
        )
        assert resp.json()["code"] == 40001

    async def test_invalid_provider_type_rejected(self, client):
        # 项目约定：pydantic 校验错误统一转 400 + code 40001（见 main._validation_error）
        resp = await client.post("/api/v1/llm/providers", json={"name": "x", "type": "bogus"})
        assert resp.status_code == 400
        assert resp.json()["code"] == 40001

    async def test_api_key_env_allows_arbitrary_name(self, client):
        """★v1029：密钥环境变量名可**任意命名**（不再要求 _API_KEY/_KEY/_TOKEN 后缀）。"""
        resp = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "任意命名厂商",
                "type": "openai",
                "api_key_env": "MY_RANDOM_VAR",
                "api_key_value": "x",
            },
        )
        assert resp.json()["code"] == 0, resp.text
        assert resp.json()["data"]["api_key_env"] == "MY_RANDOM_VAR"

    @pytest.mark.parametrize(
        "bad_name",
        ["APP_PORT", "data_root", "1BAD", "BAD-NAME", "HAS SPACE", "含中文"],
    )
    async def test_api_key_env_rejects_invalid_names(self, client, bad_name):
        """v1029：任意命名仍要挡住①非法字符 ②与应用配置键重名（否则写坏 .env）。"""
        resp = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "非法变量名厂商",
                "type": "openai",
                "api_key_env": bad_name,
                "api_key_value": "x",
            },
        )
        assert resp.json()["code"] == 40001, f"{bad_name!r} 应被拒绝，实际 {resp.text}"

    async def test_default_model_and_overview(self, client):
        _pid, model_ref = await _setup_provider_model(client)
        resp = await client.get("/api/v1/llm/overview")
        data = resp.json()["data"]
        assert data["default_model_ref"] == model_ref
        assert any(m["model_ref"] == model_ref for m in data["models"])

    async def test_config_reload_bumps_version(self, client):
        resp = await client.post("/api/v1/config/reload")
        v1 = resp.json()["data"]["config_version"]
        resp = await client.post("/api/v1/config/reload")
        v2 = resp.json()["data"]["config_version"]
        assert v2 > v1

    async def test_test_connection_reports_missing_key(self, client):
        resp = await client.post(
            "/api/v1/llm/providers",
            json={"name": "无密钥", "type": "openai", "api_key_env": "NOTSET_TEST_API_KEY"},
        )
        pid = resp.json()["data"]["id"]
        resp = await client.post(f"/api/v1/llm/providers/{pid}/test", json={})
        data = resp.json()["data"]
        assert data["ok"] is False
        assert "未配置密钥" in data["message"]


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


class TestAgentSessions:
    async def test_create_session_isolated_by_project(self, client, novel_id):
        resp = await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        assert resp.status_code == 200, resp.text
        sess = resp.json()["data"]
        assert sess["project_id"] == novel_id
        assert sess["status"] == "idle"

        # 列表按项目过滤
        resp = await client.get("/api/v1/agent/sessions", params={"novel_id": novel_id})
        assert len(resp.json()["data"]) == 1
        resp = await client.get("/api/v1/agent/sessions", params={"novel_id": "other"})
        assert resp.json()["data"] == []

    async def test_create_session_unknown_project(self, client):
        resp = await client.post("/api/v1/agent/sessions", json={"novel_id": "nope"})
        assert resp.json()["code"] == 40401

    async def test_rename_and_delete_session(self, client, novel_id):
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
        resp = await client.patch(f"/api/v1/agent/sessions/{sid}", json={"title": "大纲会话"})
        assert resp.json()["data"]["title"] == "大纲会话"

        resp = await client.delete(f"/api/v1/agent/sessions/{sid}")
        assert resp.json()["data"]["deleted"] is True
        resp = await client.get(f"/api/v1/agent/sessions/{sid}/messages")
        assert resp.json()["code"] == 40401

    async def test_update_context(self, client, novel_id):
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
        resp = await client.patch(
            f"/api/v1/agent/sessions/{sid}/context",
            json={"subagent_ids": ["a"], "skill_ids": ["b"], "context_chapter_ids": ["c1"]},
        )
        data = resp.json()["data"]
        assert data["subagent_ids"] == ["a"]
        assert data["skill_ids"] == ["b"]
        assert data["context_chapter_ids"] == ["c1"]


# ---------------------------------------------------------------------------
# 端到端运行
# ---------------------------------------------------------------------------


class TestAgentRunE2E:
    async def test_simple_message_flow(self, client, novel_id):
        """发消息 → run.start → message.delta/complete → run.end(completed)。"""
        _pid, model_ref = await _setup_provider_model(client)
        _install_fake(model_ref, [AIMessage(content="你好，我是写作助理。")])

        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
        run_id = await _post_message(client, sid, "你好")

        events = await _drain_events(run_id)
        names = [e["event"] for e in events]
        assert "run.start" in names
        assert "message.delta" in names
        assert "run.end" in names

        # seq 单调递增
        seqs = [e["seq"] for e in events if e["seq"] > 0]
        assert seqs == sorted(seqs)

        end = next(e for e in events if e["event"] == "run.end")
        assert end["data"]["status"] == "completed"

        # run 详情
        resp = await client.get(f"/api/v1/agent/runs/{run_id}")
        run = resp.json()["data"]
        assert run["status"] == "completed"
        assert run["ended_at"] is not None

        # 消息树：用户 + 助手
        resp = await client.get(f"/api/v1/agent/sessions/{sid}/messages")
        tree = resp.json()["data"]
        roles = [m["role"] for m in tree["items"]]
        assert "user" in roles and "assistant" in roles

    async def test_delete_triggers_approval_then_reject(self, client, novel_id):
        """delete 工具触发审批中断 → reject → run 继续完成。"""
        _pid, model_ref = await _setup_provider_model(client)
        _install_fake(
            model_ref,
            [
                AIMessage(content="", tool_calls=[{"name": "delete", "args": {"file_path": f"/projects/{novel_id}/x.md"}, "id": "call_del"}]),
                AIMessage(content="好的，已取消删除。"),
            ],
        )

        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
        run_id = await _post_message(client, sid, "删除 x.md")

        # 等待进入审批中断
        for _ in range(60):
            resp = await client.get(f"/api/v1/agent/runs/{run_id}")
            run = resp.json()["data"]
            if run["status"] == "awaiting_approval":
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail(f"未进入审批状态，当前 status={run['status']} error={run.get('error')}")

        interrupts = run["interrupts"]
        assert len(interrupts) == 1
        interrupt_id = interrupts[0]["id"]
        assert interrupts[0]["type"] == "approval"
        assert interrupts[0]["tool_name"] == "delete"

        # 拒绝
        resp = await client.post(
            f"/api/v1/agent/interrupts/{interrupt_id}/resolve",
            json={"decision": "reject", "message": "不要删"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["resumed"] is True

        # 等 run 完成
        for _ in range(60):
            run = (await client.get(f"/api/v1/agent/runs/{run_id}")).json()["data"]
            if run["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)
        assert run["status"] == "completed", run.get("error")

        # 重复 resolve → 409/40902
        resp = await client.post(
            f"/api/v1/agent/interrupts/{interrupt_id}/resolve", json={"decision": "approve"}
        )
        assert resp.json()["code"] == 40902

    async def test_resolve_unknown_interrupt(self, client):
        resp = await client.post(
            "/api/v1/agent/interrupts/nope/resolve", json={"decision": "approve"}
        )
        assert resp.json()["code"] == 40401

    async def test_run_not_found(self, client):
        resp = await client.get("/api/v1/agent/runs/nope")
        assert resp.json()["code"] == 40401

    async def test_message_persisted_in_tree(self, client, novel_id):
        """消息树含用户与助手消息，active_leaf 指向最后一条。"""
        _pid, model_ref = await _setup_provider_model(client)
        _install_fake(model_ref, [AIMessage(content="回复内容")])

        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
        run_id = await _post_message(client, sid, "提问")
        await _drain_events(run_id)

        tree = (await client.get(f"/api/v1/agent/sessions/{sid}/messages")).json()["data"]
        assert tree["total"] >= 2
        assert tree["active_leaf_id"] is not None
        user_msgs = [m for m in tree["items"] if m["role"] == "user"]
        assert user_msgs and user_msgs[0]["content"] == "提问"


# ---------------------------------------------------------------------------
# SSE 流 API（经 HTTP）
# ---------------------------------------------------------------------------


class TestSseStreamHttp:
    async def test_stream_unknown_run_404(self, client):
        resp = await client.get("/api/v1/agent/runs/nope/stream")
        assert resp.status_code == 404

    async def test_stream_replays_buffered_events(self, client, novel_id):
        """run 完成后连流，仍能从环形缓冲补齐事件。"""
        _pid, model_ref = await _setup_provider_model(client)
        _install_fake(model_ref, [AIMessage(content="完成")])

        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
        run_id = await _post_message(client, sid, "hi")
        await _drain_events(run_id)  # 等 run 结束

        # 之后再连 SSE，应补发缓冲并以 run.end 收尾
        events: list[str] = []
        async with client.stream("GET", f"/api/v1/agent/runs/{run_id}/stream") as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                if "run.end" in line:
                    break
        assert "run.start" in events
        assert "run.end" in events
