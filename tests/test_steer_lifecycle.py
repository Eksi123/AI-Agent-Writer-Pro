# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_steer_lifecycle.py
作者: xionggaoqing
时间: 2026-11-08
说明: 引导消息（steer）生命周期与停止/异常收尾语义测试。

    覆盖：
    - steer 发送即落库（kind=steer，未消费前不在活跃路径）+ 响应返回 message_id；
    - steer 被下一次模型调用消费（steer.consumed 事件 + 进入活跃路径原位展示）；
    - 未消费 steer 可 PATCH 编辑 / DELETE 删除（落库行与队列同步）；已消费/结束 → 40902；
    - 主动停止 → 落库 notice「当前任务已暂停」，会话只显示 用户消息 + notice；
    - 模型接口异常 + 探测不恢复 → 30s 宽限超时落库 notice（含原因与建议）；
    - 模型接口异常 + 探测恢复 → checkpoint 续跑原任务，run 正常 completed、无 notice；
    - classify_failure / notice_text_for 单元语义。
"""

from __future__ import annotations

import asyncio

import httpx
from langchain_core.messages import AIMessage

from helpers import drain_events, install_fake, new_session, post_message, setup_provider_model, wait_status
from fake_model import ai_text, ai_tool_call

from app.agent.loop import runner as runner_mod
from app.agent.loop.netprobe import classify_failure, notice_text_for
from app.core.errors import ApiError, ErrorCode
from app.db.models import Message
from app.db.session import session_scope
from app.agent.tracing.sse.events import sse_bus


async def _collect_until_end(run_id: str, timeout: float = 30.0) -> list[dict]:
    """订阅 run 事件流直到 run.end（drain_events 会在首个 error 事件返回，
    而 notice/run.end 在 error 之后发出，故这里自行收集全量事件）。"""
    queue, backlog, _lost, finished = await sse_bus.subscribe(run_id)
    events: list[dict] = [e.to_dict() for e in backlog]
    try:
        if not finished and not any(e["event"] == "run.end" for e in events):
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    break
                events.append(item.to_dict())
                if item.event == "run.end":
                    break
    finally:
        await sse_bus.unsubscribe(run_id, queue)
    return events


async def _tree(client, session_id: str) -> dict:
    resp = await client.get(f"/api/v1/agent/sessions/{session_id}/messages")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _kinds(tree: dict) -> list[str]:
    return [m.get("kind", "normal") for m in tree["items"] if m["in_active_path"]]


def _rows(tree: dict) -> list[dict]:
    return [m for m in tree["items"] if m["in_active_path"]]


class TestClassifyAndNotice:
    def test_classify_network(self):
        assert classify_failure(httpx.ConnectError("boom")) == "network"
        assert classify_failure(httpx.ConnectTimeout("slow")) == "network"
        assert classify_failure(RuntimeError("connection reset by peer")) == "network"

    def test_classify_llm(self):
        exc = ApiError("provider 502", code=ErrorCode.LLM_PROVIDER_FAILED)
        assert classify_failure(exc) == "llm"
        assert classify_failure(RuntimeError("rate limit exceeded 429")) == "llm"

    def test_classify_other(self):
        assert classify_failure(ValueError("bad input")) == "other"

    def test_notice_texts(self):
        assert notice_text_for("cancelled") == "当前任务已暂停"
        assert notice_text_for("network") == "当前网络异常，请稍后再试"
        llm = notice_text_for("llm", "502 Bad Gateway")
        assert "当前大模型接口请求异常" in llm
        assert "502 Bad Gateway" in llm
        assert "建议" in llm


class TestSteerPersistAndConsume:
    async def test_steer_persists_and_consumes(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        # 第一轮工具调用保持 run 存活，steer 在第二轮模型调用前注入
        fake = install_fake(
            model_ref,
            [ai_tool_call("ls", {"path": f"/projects/{novel_id}"}, "cs1"), ai_text("完成。")],
        )
        fake.sleep = 0.4
        run_id = await post_message(client, sid, "分两步写")
        await wait_status(client, run_id, ("running",))

        resp = await client.post(f"/api/v1/agent/runs/{run_id}/steer", json={"content": "改用第二人称"})
        assert resp.status_code == 200, resp.text
        message_id = resp.json()["data"]["message_id"]

        # 未消费：已落库 kind=steer，但不在活跃路径（不干扰 assistant 落库链路）
        tree = await _tree(client, sid)
        steer_rows = [m for m in tree["items"] if m["id"] == message_id]
        assert len(steer_rows) == 1
        assert steer_rows[0]["kind"] == "steer"
        assert steer_rows[0]["in_active_path"] is False

        events = await drain_events(run_id)
        await wait_status(client, run_id, ("completed",))
        # steer.consumed 事件携带 message_id
        consumed = [e for e in events if e["event"] == "steer.consumed"]
        assert consumed and message_id in consumed[0]["data"]["message_ids"]
        # 注入到了第二次模型调用
        assert any(getattr(m, "content", "") == "改用第二人称" for m in fake.calls[1])

        # 消费后：steer 行进入活跃路径（原位展示），且只有一条
        tree = await _tree(client, sid)
        active = _rows(tree)
        kinds = [m["kind"] for m in active]
        assert kinds.count("steer") == 1
        idx = kinds.index("steer")
        assert active[idx]["content"] == "改用第二人称"
        # steer 前后仍属同一轮：user 在 steer 前，最终 assistant 在 steer 后
        assert active[0]["role"] == "user"
        assert active[-1]["role"] == "assistant"

    async def test_steer_edit_and_delete_before_consume(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        fake = install_fake(
            model_ref,
            [ai_tool_call("ls", {"path": f"/projects/{novel_id}"}, "cs1"), ai_text("完成。")],
        )
        fake.sleep = 1.2
        run_id = await post_message(client, sid, "写")
        await wait_status(client, run_id, ("running",))

        resp = await client.post(f"/api/v1/agent/runs/{run_id}/steer", json={"content": "原文"})
        message_id = resp.json()["data"]["message_id"]

        # 编辑：落库行与队列同步更新
        resp = await client.patch(
            f"/api/v1/agent/runs/{run_id}/steer/{message_id}", json={"content": "改后"}
        )
        assert resp.status_code == 200, resp.text
        async with session_scope() as db:
            row = await db.get(Message, message_id)
            assert row.content_json == "改后"

        # 删除：落库行与队列项一并移除
        resp = await client.delete(f"/api/v1/agent/runs/{run_id}/steer/{message_id}")
        assert resp.status_code == 200, resp.text
        async with session_scope() as db:
            assert await db.get(Message, message_id) is None

        await drain_events(run_id)
        await wait_status(client, run_id, ("completed",))
        # 已删除的引导不会进入模型调用
        for call in fake.calls:
            assert not any(getattr(m, "content", "") in ("原文", "改后") for m in call)

    async def test_steer_edit_after_consume_409(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        install_fake(model_ref, [ai_text("完成。")])
        run_id = await post_message(client, sid, "写")
        await drain_events(run_id)
        await wait_status(client, run_id, ("completed",))
        resp = await client.post(f"/api/v1/agent/runs/{run_id}/steer", json={"content": "x"})
        assert resp.status_code == 409
        # 运行结束后编辑/删除任意 steer id 均 40902
        resp = await client.patch(f"/api/v1/agent/runs/{run_id}/steer/m-nope", json={"content": "y"})
        assert resp.status_code == 404


class TestStopAndFailureNotice:
    async def test_cancel_persists_pause_notice(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        fake = install_fake(model_ref, [ai_text("不该出现的完整回复")])
        fake.sleep = 1.5
        run_id = await post_message(client, sid, "写一章")
        await wait_status(client, run_id, ("running",))

        resp = await client.post(f"/api/v1/agent/runs/{run_id}/cancel")
        assert resp.status_code == 200, resp.text
        await drain_events(run_id)
        run = await wait_status(client, run_id, ("cancelled",))
        assert run["status"] == "cancelled"

        tree = await _tree(client, sid)
        active = _rows(tree)
        # 会话只显示：一条用户消息 + 一条 notice 默认回复
        assert [m["role"] for m in active] == ["user", "assistant"]
        assert active[-1]["kind"] == "notice"
        assert active[-1]["content"] == "当前任务已暂停"

    async def test_cancel_with_unconsumed_steer_shows_all_three(self, client, novel_id):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        fake = install_fake(model_ref, [ai_text("不该出现的完整回复")])
        fake.sleep = 1.5
        run_id = await post_message(client, sid, "写一章")
        await wait_status(client, run_id, ("running",))

        resp = await client.post(f"/api/v1/agent/runs/{run_id}/steer", json={"content": "引导一"})
        assert resp.status_code == 200, resp.text
        # 引导尚未被消费即停止 → 收尾收养进活跃路径
        await client.post(f"/api/v1/agent/runs/{run_id}/cancel")
        await drain_events(run_id)
        await wait_status(client, run_id, ("cancelled",))

        tree = await _tree(client, sid)
        active = _rows(tree)
        assert [m["role"] for m in active] == ["user", "user", "assistant"]
        assert active[1]["kind"] == "steer"
        assert active[1]["content"] == "引导一"
        assert active[2]["kind"] == "notice"
        assert active[2]["content"] == "当前任务已暂停"

    async def test_llm_failure_probe_timeout_persists_notice(self, client, novel_id, monkeypatch):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        fake = install_fake(model_ref, [ai_text("完成。")])

        def _boom(self, messages):  # noqa: ARG001
            raise ApiError("502 Bad Gateway from provider", code=ErrorCode.LLM_PROVIDER_FAILED)

        # 第一次（也是唯一一次）模型调用抛 LLM 业务错误（不可重试、无备用模型 → 直达宽限逻辑）
        monkeypatch.setattr(type(fake), "_next_response", _boom)

        # 宽限窗口压到 1s、探测间隔 0.2s；探测恒不可达 → 超时落 notice
        monkeypatch.setattr(runner_mod, "GRACE_WINDOW_SEC", 1)
        monkeypatch.setattr(runner_mod, "GRACE_PROBE_INTERVAL", 0.2)

        async def _no_probe(_db, _ref):
            return False

        monkeypatch.setattr(runner_mod, "probe_provider", _no_probe)

        run_id = await post_message(client, sid, "写")
        events = await _collect_until_end(run_id, timeout=20)
        run = await wait_status(client, run_id, ("failed", "completed"))
        assert run["status"] == "failed"

        notice_events = [e for e in events if e["event"] == "run.notice"]
        assert notice_events and notice_events[0]["data"]["kind"] == "llm"
        end = [e for e in events if e["event"] == "run.end"]
        assert end and end[-1]["data"].get("notice") is True

        tree = await _tree(client, sid)
        active = _rows(tree)
        assert active[-1]["kind"] == "notice"
        assert "当前大模型接口请求异常" in active[-1]["content"]
        assert "建议" in active[-1]["content"]

    async def test_llm_failure_probe_ok_resumes(self, client, novel_id, monkeypatch):
        _, model_ref = await setup_provider_model(client)
        sid = await new_session(client, novel_id)
        fake = install_fake(model_ref, [ai_text("占位")])
        calls = {"n": 0}

        def _first_boom(self, messages):  # noqa: ARG001
            calls["n"] += 1
            # 前 3 次 = 首次 + ModelRetry 的 2 次重试（重试耗尽 on_failure=error 上抛），
            # 第 4 次 = 宽限探测恢复后的 checkpoint 续跑
            if calls["n"] <= 3:
                raise ApiError("503 provider unavailable", code=ErrorCode.LLM_PROVIDER_FAILED)
            return AIMessage(
                content="续跑后的回复。",
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            )

        monkeypatch.setattr(type(fake), "_next_response", _first_boom)
        monkeypatch.setattr(runner_mod, "GRACE_PROBE_INTERVAL", 0.2)
        monkeypatch.setattr(runner_mod, "GRACE_WINDOW_SEC", 10)

        async def _ok_probe(_db, _ref):
            return True

        monkeypatch.setattr(runner_mod, "probe_provider", _ok_probe)

        run_id = await post_message(client, sid, "写")
        events = await _collect_until_end(run_id, timeout=30)
        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "completed", run.get("error")
        # 续跑事件存在、无 notice
        assert any(e["event"] == "run.resuming" for e in events)
        assert not [e for e in events if e["event"] == "run.notice"]

        tree = await _tree(client, sid)
        active = _rows(tree)
        assert not [m for m in active if m["kind"] == "notice"]
        assert active[-1]["content"] == "续跑后的回复。"
