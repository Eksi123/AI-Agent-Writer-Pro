# -*- coding: utf-8 -*-
"""
模块: tests
文件名: helpers.py
作者: xionggaoqing
时间: 2026-10-27
说明: 跨测试文件共享的辅助函数。

    原先散落在 test_agent_e2e.py 中，重构后提取到此处供所有测试文件引用。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import AIMessage
from fake_model import ScriptedFakeChatModel

from app.agent.loop.llm.factory import register_model_override
from app.agent.tracing.sse.events import sse_bus


async def setup_provider_model(client, *, model_id: str = "fake-model") -> tuple[str, str]:
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
    assert "test-fake" not in provider["api_key_masked"]

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


def install_fake(model_ref: str, responses: list[Any]) -> ScriptedFakeChatModel:
    """注册假模型到工厂，返回 fake 实例。"""
    fake = ScriptedFakeChatModel(responses=responses)
    register_model_override(model_ref, fake)
    return fake


async def drain_events(run_id: str, *, timeout: float = 8.0) -> list[dict[str, Any]]:
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


async def post_message(client, session_id: str, content: str, **kwargs) -> str:
    """发消息到会话，返回 run_id。"""
    resp = await client.post(
        f"/api/v1/agent/sessions/{session_id}/messages",
        json={"content": content, **kwargs},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]["run_id"]


async def new_session(client, novel_id: str) -> str:
    """创建一个 Agent 会话，返回 session_id。"""
    return (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]


async def wait_status(client, run_id: str, statuses: tuple[str, ...], *, tries: int = 80) -> dict:
    """轮询等待 run 进入指定状态。"""
    import pytest

    for _ in range(tries):
        run = (await client.get(f"/api/v1/agent/runs/{run_id}")).json()["data"]
        if run["status"] in statuses:
            return run
        await asyncio.sleep(0.1)
    pytest.fail(f"等待 {statuses} 超时，当前 status={run['status']} error={run.get('error')}")


def delete_call(novel_id: str, call_id: str = "call_del") -> AIMessage:
    """构造 delete 工具调用的 AIMessage。"""
    return AIMessage(
        content="",
        tool_calls=[{"name": "delete", "args": {"file_path": f"/projects/{novel_id}/x.md"}, "id": call_id}],
    )
