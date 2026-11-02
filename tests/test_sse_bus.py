# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_sse_bus.py
作者: xionggaoqing
时间: 2026-10-27
说明: SSE 事件总线契约测试（手册「十一 · SSE」）。

    - `seq` 每 run 内单调递增；
    - `Last-Event-ID` 续传补齐；
    - 缓冲溢出 → buffer_lost（API 层发 41001）；
    - 缓冲上限来自 settings.sse_buffer_size。
"""

from __future__ import annotations

import asyncio
import json
from collections import deque

import pytest

from app.agent.tracing.sse.events import RunStream, SseBus, SseEvent
from app.core.config import get_settings


@pytest.fixture
def bus() -> SseBus:
    return SseBus()


class TestSeqMonotonic:
    async def test_seq_increases_per_run(self, bus):
        await bus.open("r1", "s1")
        seqs = []
        for i in range(5):
            evt = await bus.publish("r1", "s1", "message.delta", {"delta": str(i)})
            seqs.append(evt.seq)
        assert seqs == [1, 2, 3, 4, 5]

    async def test_seq_independent_between_runs(self, bus):
        await bus.publish("r1", "s1", "run.start", {})
        await bus.publish("r1", "s1", "run.end", {})
        evt = await bus.publish("r2", "s2", "run.start", {})
        assert evt.seq == 1

    async def test_envelope_fields(self, bus):
        evt = await bus.publish("r1", "s1", "tool.start", {"tool_name": "ls"})
        d = evt.to_dict()
        assert d["event"] == "tool.start"
        assert d["run_id"] == "r1"
        assert d["session_id"] == "s1"
        assert d["seq"] == 1
        assert d["ts"].endswith("Z")
        assert d["data"] == {"tool_name": "ls"}
        # 可 JSON 序列化

        json.loads(evt.to_json())


class TestResume:
    async def test_backlog_from_last_event_id(self, bus):
        await bus.open("r1", "s1")
        for i in range(5):
            await bus.publish("r1", "s1", "message.delta", {"delta": str(i)})

        queue, backlog, lost, finished = await bus.subscribe("r1", last_event_id=2)
        assert lost is False
        assert finished is False
        assert [e.seq for e in backlog] == [3, 4, 5]
        await bus.unsubscribe("r1", queue)

    async def test_full_backlog_without_last_event_id(self, bus):
        await bus.open("r1", "s1")
        for i in range(3):
            await bus.publish("r1", "s1", "message.delta", {"delta": str(i)})
        queue, backlog, lost, finished = await bus.subscribe("r1")
        assert [e.seq for e in backlog] == [1, 2, 3]
        assert lost is False
        await bus.unsubscribe("r1", queue)

    async def test_buffer_lost_when_seq_evicted(self, bus):
        """缓冲溢出后，请求已被挤出的 seq → buffer_lost=True（API 层发 41001）。"""

        stream = await bus.open("r1", "s1")
        stream.buffer = deque(maxlen=3)  # 模拟小缓冲
        for i in range(10):
            await bus.publish("r1", "s1", "message.delta", {"delta": str(i)})

        queue, backlog, lost, finished = await bus.subscribe("r1", last_event_id=2)
        assert lost is True
        assert backlog == []
        await bus.unsubscribe("r1", queue)

    async def test_unknown_run_reports_lost(self, bus):
        queue, backlog, lost, finished = await bus.subscribe("no-such-run", last_event_id=1)
        assert lost is True and finished is True
        assert backlog == []


class TestSubscribeLive:
    async def test_subscriber_receives_new_events(self, bus):
        await bus.open("r1", "s1")
        queue, backlog, lost, finished = await bus.subscribe("r1")
        assert backlog == []

        await bus.publish("r1", "s1", "message.delta", {"delta": "x"})
        evt = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert evt.event == "message.delta"
        assert evt.data == {"delta": "x"}
        await bus.unsubscribe("r1", queue)

    async def test_close_wakes_subscribers(self, bus):
        await bus.open("r1", "s1")
        queue, *_ = await bus.subscribe("r1")
        await bus.close("r1")
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        # 关闭哨兵（非 SseEvent）

        assert not isinstance(item, SseEvent)

    async def test_reopen_preserves_seq_and_buffer(self, bus):
        """中断恢复重开流：seq 续增、缓冲保留（客户端无缝续传）。"""
        await bus.open("r1", "s1")
        await bus.publish("r1", "s1", "run.start", {})
        await bus.publish("r1", "s1", "run.end", {})
        await bus.close("r1")

        stream = await bus.reopen("r1", "s1")
        assert stream.finished is False
        assert stream.seq == 2
        evt = await bus.publish("r1", "s1", "run.start", {"resumed": True})
        assert evt.seq == 3

    async def test_run_end_marks_finished(self, bus):
        await bus.open("r1", "s1")
        await bus.publish("r1", "s1", "run.end", {"status": "completed"})
        _q, backlog, lost, finished = await bus.subscribe("r1")
        assert finished is True
        assert backlog[-1].event == "run.end"


class TestRingBuffer:
    async def test_buffer_respects_settings_size(self, bus, monkeypatch):

        get_settings.cache_clear()
        stream = RunStream(run_id="r", session_id="s")
        assert stream.buffer.maxlen == max(10, get_settings().sse_buffer_size)

    async def test_eviction_removes_finished_streams_only(self, bus):
        bus._max_streams = 2
        await bus.open("r1", "s1")
        await bus.publish("r1", "s1", "run.end", {})  # finished
        await bus.open("r2", "s2")  # running
        await bus.open("r3", "s3")  # 触发淘汰
        assert "r1" not in bus._streams  # 已结束的被淘汰
        assert "r2" in bus._streams  # 进行中的保留
        assert "r3" in bus._streams
