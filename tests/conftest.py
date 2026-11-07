# -*- coding: utf-8 -*-
"""
模块: tests
文件名: conftest.py
作者: xionggaoqing
时间: 2026-10-27
说明: pytest 公共 fixture：临时数据根 + 内存隔离的 ASGI 客户端。

    手册「十一、测试与验收策略」：API 层用 `httpx.ASGITransport` 直连 FastAPI app（不起真实端口），
    文件类测试用 `tmp_path` 构造临时工作区。
"""

from __future__ import annotations

import copy
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest

from app.agent.loop import checkpointer as cp
from app.agent.loop.llm.factory import clear_model_overrides
from app.agent.tracing.sse.events import sse_bus
from app.core.config import get_settings
from app.core.paths import data_root, workspace_dir
from app.db.bootstrap import init_database
from app.db.fts import probe_fts5
from app.db.session import get_sessionmaker, reset_engine
from app.main import app


@pytest.fixture(autouse=True)
def _isolate_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每个测试用独立的 data 根，避免互相污染。

    必须在 `app.core.config` 首次导入前生效，故同时重置 lru_cache 与 FTS 探测缓存。
    Agent 侧全局单例（checkpointer / SSE 总线 / 模型缓存）也一并重置。
    同时快照并还原 `os.environ`：`set_env_key` 会直接写进程环境（密钥类测试会污染）。
    """

    env_snapshot = copy.deepcopy(os.environ)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_ROOT", str(data_dir))
    monkeypatch.setenv("APP_PORT", "8000")
    # 关闭 Langfuse，避免测试触网
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    # 测试默认关闭子代理（聚焦主循环；子代理装配有专项测试）
    monkeypatch.setenv("AGENT_SUBAGENTS_ENABLED", "false")


    get_settings.cache_clear()
    probe_fts5.cache_clear()
    yield

    # 全局单飞：收尾取消残留的活 run，避免挡住下一个测试发消息。
    # 同步 fixture 不能 await——只 cancel；被取消任务在下次 await 点自行退出，
    # 其收尾异常已被 runner 的 finally 兜住（CancelledError 直接终止任务）。
    from app.agent.loop.runner import run_manager

    for rid in list(run_manager.running_ids()):
        task = run_manager._tasks.get(rid)
        if task is not None and not task.done():
            task.cancel()

    # 同步 teardown：直接释放全局态（连接对象交由 GC；测试用临时目录）
    clear_model_overrides()
    cp._saver = None
    cp._conn = None
    cp._saver_path = None
    sse_bus._streams.clear()
    get_settings.cache_clear()

    # 还原进程环境（清除 set_env_key 写入的密钥等）
    os.environ.clear()
    os.environ.update(env_snapshot)


@pytest.fixture
async def client(_isolate_data_root: None) -> AsyncIterator:
    """httpx AsyncClient 直连 ASGI app（含 lifespan 启动/关闭）。"""

    # 重新导入以拿到隔离后的 settings（engine 单例需重置）

    await reset_engine()


    transport = httpx.ASGITransport(app=app)
    # 单层 with：退出顺序仍为 client → lifespan，语义与嵌套写法一致
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        yield ac

    await reset_engine()


@pytest.fixture
async def session(_isolate_data_root: None) -> AsyncIterator:
    """裸 AsyncSession（服务层单测用）。"""

    await reset_engine()
    await init_database()
    async with get_sessionmaker()() as s:
        yield s
    await reset_engine()


@pytest.fixture
async def novel_id(client) -> str:
    """预建一个合法命名的小说项目。"""
    resp = await client.post(
        "/api/v1/novels",
        json={"title": "星海拾荒人", "author_name": "测试作者", "genre": "科幻"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    return body["data"]["id"]


@pytest.fixture
async def volume_id(client, novel_id: str) -> str:
    """预建一卷。"""
    resp = await client.post(
        f"/api/v1/novels/{novel_id}/volumes", json={"title": "荒原初醒", "summary": "第一卷简介"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


@pytest.fixture
async def chapter_id(client, volume_id: str) -> str:
    """预建一章。"""
    resp = await client.post(
        f"/api/v1/volumes/{volume_id}/chapters",
        json={"title": "拾荒少年的清晨", "content": "少年走出山村，第一次见到星海。"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def workspace_of(project_id: str) -> Path:
    """返回当前测试的临时工作区路径。"""

    return workspace_dir(project_id)


def data_root_of() -> Path:

    return data_root()


def env_var(name: str) -> str | None:
    return os.getenv(name)
