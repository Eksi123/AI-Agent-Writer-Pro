# -*- coding: utf-8 -*-
"""
模块: app.agent.tracing.sse.events
文件名: events.py
作者: xionggaoqing
时间: 2026-10-27
说明: SSE 事件总线：每 run 内存环形缓冲 + `seq` 单调递增 + 断线续传（手册「六 · SSE 契约」）。

    事件信封：`{event, run_id, session_id, seq, ts, data}`。

    - 客户端断线重连带 `Last-Event-ID: {seq}`，服务端从缓冲补发；
    - 缓冲溢出或进程重启后不可续传 → 发 `error`（code=41001）并关流，前端回落重拉消息树；
    - `POST messages` 返回 202 后，连接前产生的事件由环形缓冲补齐（先建流/先发消息两种顺序都支持）。
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.core.errors import ErrorCode
from app.core.logging import get_logger
from app.db.base import utcnow

logger = get_logger(__name__)

#: 订阅队列的关闭标记（与事件对象区分）
_CLOSE_SENTINEL = object()


@dataclass(slots=True)
class SseEvent:
    """一条已定序的事件。"""

    event: str
    run_id: str
    session_id: str
    seq: int
    ts: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "seq": self.seq,
            "ts": self.ts,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


@dataclass
class RunStream:
    """单个 run 的环形缓冲 + 订阅者集合。"""

    run_id: str
    session_id: str
    buffer: deque[SseEvent] = field(default_factory=deque)
    seq: int = 0
    #: 已结束（run.end / error 已发）；订阅者读到结尾即退出
    finished: bool = False
    subscribers: set[asyncio.Queue] = field(default_factory=set)

    def __post_init__(self) -> None:
        size = max(10, int(get_settings().sse_buffer_size))
        self.buffer = deque(maxlen=size)


class SseBus:
    """进程内事件总线（单用户本地应用，无需跨进程）。"""

    def __init__(self) -> None:
        self._streams: dict[str, RunStream] = {}
        self._lock = asyncio.Lock()
        #: 已完成 run 的保留上限（超出按插入序淘汰，避免长期运行内存增长）
        self._max_streams = 64

    # ---- 生命周期 ----

    async def open(self, run_id: str, session_id: str) -> RunStream:
        """为 run 建立流（发消息时调用，早于/晚于建流都支持）。"""
        async with self._lock:
            stream = self._streams.get(run_id)
            if stream is None:
                stream = RunStream(run_id=run_id, session_id=session_id)
                self._streams[run_id] = stream
                self._evict_locked()
            return stream

    async def close(self, run_id: str) -> None:
        """标记流结束并唤醒订阅者（缓冲保留，供晚到的重连补齐）。"""
        async with self._lock:
            stream = self._streams.get(run_id)
            if stream is None:
                return
            stream.finished = True
            for queue in list(stream.subscribers):
                await queue.put(_CLOSE_SENTINEL)

    async def reopen(self, run_id: str, session_id: str) -> RunStream:
        """中断恢复时重开流：`finished=False`，**seq 与缓冲保留**（客户端可无缝续传）。"""
        async with self._lock:
            stream = self._streams.get(run_id)
            if stream is None:
                stream = RunStream(run_id=run_id, session_id=session_id)
                self._streams[run_id] = stream
                self._evict_locked()
            else:
                stream.finished = False
                stream.session_id = session_id
            return stream

    def forget(self, run_id: str) -> None:
        self._streams.pop(run_id, None)

    # ---- 发布 ----

    async def publish(
        self, run_id: str, session_id: str, event: str, data: dict[str, Any] | None = None
    ) -> SseEvent:
        """追加一条事件并广播给订阅者。"""
        async with self._lock:
            stream = self._streams.get(run_id)
            if stream is None:
                stream = RunStream(run_id=run_id, session_id=session_id)
                self._streams[run_id] = stream
                self._evict_locked()
            stream.seq += 1
            sse = SseEvent(
                event=event,
                run_id=run_id,
                session_id=session_id,
                seq=stream.seq,
                ts=utcnow(),
                data=data or {},
            )
            stream.buffer.append(sse)
            if event in ("run.end", "error"):
                stream.finished = True
            queues = list(stream.subscribers)

        for queue in queues:
            await queue.put(sse)
        return sse

    def schedule(
        self, run_id: str, session_id: str, event: str, data: dict[str, Any] | None = None
    ) -> asyncio.Future | None:
        """在已有事件循环内调度一次发布（供同步上下文使用）。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("sse_publish_without_loop", run_id=run_id, event=event)
            return None
        return asyncio.ensure_future(self.publish(run_id, session_id, event, data))

    # ---- 订阅 ----

    async def subscribe(
        self, run_id: str, *, last_event_id: int | None = None
    ) -> tuple[asyncio.Queue, list[SseEvent], bool, bool]:
        """订阅一个 run。

        Returns:
            (queue, backlog, buffer_lost, finished)
            - `backlog`：需要先补发的历史事件（按 Last-Event-ID 过滤）
            - `buffer_lost`：请求的 seq 已被挤出缓冲 → 调用方应发 41001 并关流
            - `finished`：run 已结束（补发完 backlog 即可关流，无需再等）
        """
        async with self._lock:
            stream = self._streams.get(run_id)
            if stream is None:
                return asyncio.Queue(), [], True, True
            queue: asyncio.Queue = asyncio.Queue()
            stream.subscribers.add(queue)

            backlog: list[SseEvent] = []
            buffer_lost = False
            if last_event_id is not None and last_event_id > 0:
                buffered = list(stream.buffer)
                oldest = buffered[0].seq if buffered else stream.seq + 1
                if last_event_id < oldest - 1:
                    # 请求的位点已被挤出缓冲（溢出或重启）
                    buffer_lost = True
                else:
                    backlog = [e for e in buffered if e.seq > last_event_id]
            else:
                backlog = list(stream.buffer)
            finished = stream.finished

        return queue, backlog, buffer_lost, finished

    async def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            stream = self._streams.get(run_id)
            if stream is not None:
                stream.subscribers.discard(queue)

    # ---- 内部 ----

    def _evict_locked(self) -> None:
        """超出上限时淘汰最早**已结束**的流（进行中 run 的流绝不淘汰）。"""
        if len(self._streams) <= self._max_streams:
            return
        for run_id, stream in list(self._streams.items()):
            if len(self._streams) <= self._max_streams:
                break
            if stream.finished and not stream.subscribers:
                self._streams.pop(run_id, None)

    def snapshot(self, run_id: str) -> list[dict[str, Any]]:
        """缓冲内容快照（调试 / 断线兜底）。"""
        stream = self._streams.get(run_id)
        if stream is None:
            return []
        return [e.to_dict() for e in stream.buffer]


#: 进程内单例
sse_bus = SseBus()


def buffer_lost_event(run_id: str, session_id: str) -> SseEvent:
    """构造 41001（stream buffer lost）事件。"""
    return SseEvent(
        event="error",
        run_id=run_id,
        session_id=session_id,
        seq=0,
        ts=utcnow(),
        data={"code": ErrorCode.STREAM_BUFFER_LOST, "message": "stream buffer lost"},
    )
