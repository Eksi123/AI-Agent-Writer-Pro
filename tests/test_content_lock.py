# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_content_lock.py
作者: xionggaoqing
时间: 2026-10-27
说明: 正文读写乐观锁 + 修订快照（手册「六 · 正文写入乐观锁」/「十一 · 乐观锁/修订快照」域）。

    ★必测：
    - 带过期 `base_version` → 409 / `40901` 且回传最新内容；带最新值 → 成功且 `version+1`；
    - 手动保存 / Agent 写入两种触发各产生快照；自动保存不产生；
    - 超过保留上限时清理最旧快照；restore 回滚后内容与 `version` 正确。
    ★v1101：AI 编辑锁——锁定期内手动/自动保存均 409 / `40903`；释放后恢复；
      `GET /chapters/locks` 返回当前锁列表。
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.services import volumes as volumes_service
from app.services.content_lock import content_locks


@pytest.mark.asyncio
async def test_read_content_returns_version(client, chapter_id):
    data = (await client.get(f"/api/v1/chapters/{chapter_id}/content")).json()["data"]
    assert data["version"] == 1
    assert data["content"] == "少年走出山村，第一次见到星海。"
    assert data["status"] == "draft"
    assert data["last_writer"] == "manual"


@pytest.mark.asyncio
async def test_put_content_requires_base_version(client, chapter_id):
    """`base_version` 缺失 → 400（参数格式错误）。"""
    resp = await client.put(f"/api/v1/chapters/{chapter_id}/content", json={"content": "新正文"})
    assert resp.status_code == 400
    assert resp.json()["code"] == 40001


@pytest.mark.asyncio
async def test_put_content_success_increments_version(client, chapter_id):
    resp = await client.put(
        f"/api/v1/chapters/{chapter_id}/content",
        json={"content": "少年走出山村，回望了一眼炊烟。", "base_version": 1},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["version"] == 2
    assert data["snapshotted"] is True
    assert data["word_count"] > 0

    again = (await client.get(f"/api/v1/chapters/{chapter_id}/content")).json()["data"]
    assert again["version"] == 2
    assert again["content"] == "少年走出山村，回望了一眼炊烟。"


@pytest.mark.asyncio
async def test_stale_base_version_returns_409_40901(client, chapter_id):
    """★核心：过期 base_version → 409 / 40901，且 data 回传最新内容与版本号。"""
    # 先写一次，把 version 推到 2
    await client.put(
        f"/api/v1/chapters/{chapter_id}/content",
        json={"content": "AI 或其它端已修改的内容", "base_version": 1},
    )

    # 用过期的 base_version=1 再写 → 冲突
    resp = await client.put(
        f"/api/v1/chapters/{chapter_id}/content",
        json={"content": "自动保存迟到的内容", "base_version": 1},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == 40901
    assert body["data"]["version"] == 2, "必须回传服务端最新版本号"
    assert body["data"]["content"] == "AI 或其它端已修改的内容", "必须回传最新内容供前端合并"
    assert body["data"]["base_version"] == 1


@pytest.mark.asyncio
async def test_retry_with_latest_version_succeeds(client, chapter_id):
    """强制覆盖需再次带最新 base_version（手册「六」流程）。"""
    await client.put(
        f"/api/v1/chapters/{chapter_id}/content", json={"content": "v2", "base_version": 1}
    )
    conflict = await client.put(
        f"/api/v1/chapters/{chapter_id}/content", json={"content": "v3", "base_version": 1}
    )
    assert conflict.status_code == 409
    latest = conflict.json()["data"]["version"]

    ok = await client.put(
        f"/api/v1/chapters/{chapter_id}/content",
        json={"content": "覆盖后的正文", "base_version": latest},
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["version"] == latest + 1


@pytest.mark.asyncio
async def test_manual_save_creates_snapshot(client, chapter_id):
    """手动保存产生 `source="manual"` 快照，内容为**旧**正文。"""
    await client.put(
        f"/api/v1/chapters/{chapter_id}/content",
        json={"content": "第二版正文", "base_version": 1},
    )
    revs = (await client.get(f"/api/v1/chapters/{chapter_id}/revisions")).json()["data"]
    assert len(revs) == 1
    assert revs[0]["source"] == "manual"
    assert revs[0]["version"] == 1, "快照记录的是被覆盖前的版本"
    assert "少年走出山村" in revs[0]["preview"]


@pytest.mark.asyncio
async def test_autosave_does_not_snapshot(client, chapter_id):
    """★自动保存不产生快照（防版本爆炸），但 version 仍自增。"""
    for i in range(1, 6):
        resp = await client.put(
            f"/api/v1/chapters/{chapter_id}/content",
            json={"content": f"自动保存第 {i} 次", "base_version": i, "autosave": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["snapshotted"] is False

    cur = (await client.get(f"/api/v1/chapters/{chapter_id}/content")).json()["data"]
    assert cur["version"] == 6

    revs = (await client.get(f"/api/v1/chapters/{chapter_id}/revisions")).json()["data"]
    assert revs == [], "自动保存不得产生任何快照"


@pytest.mark.asyncio
async def test_invalid_status_rejected(client, chapter_id):
    resp = await client.patch(f"/api/v1/chapters/{chapter_id}", json={"status": "published"})
    assert resp.status_code == 400
    assert resp.json()["code"] == 40001


@pytest.mark.asyncio
async def test_snapshot_pruning_keeps_newest(client, chapter_id, monkeypatch):
    """超过保留上限时清理最旧的非定稿快照。"""

    monkeypatch.setattr(get_settings(), "chapter_revision_keep", 3, raising=False)

    for i in range(1, 9):
        resp = await client.put(
            f"/api/v1/chapters/{chapter_id}/content",
            json={"content": f"第 {i} 版正文", "base_version": i},
        )
        assert resp.status_code == 200, resp.text

    revs = (await client.get(f"/api/v1/chapters/{chapter_id}/revisions")).json()["data"]
    assert len(revs) <= 3, f"应被裁剪到上限 3，实际 {len(revs)}"
    # 保留的是最新的（version 最大）
    versions = [r["version"] for r in revs]
    assert versions == sorted(versions, reverse=True)
    assert max(versions) == 8


@pytest.mark.asyncio
async def test_restore_revision(client, chapter_id):
    """回滚到指定快照：内容还原、version 继续自增、回滚前也快照当前内容。"""
    # v1 -> v2
    await client.put(
        f"/api/v1/chapters/{chapter_id}/content",
        json={"content": "第二版正文", "base_version": 1},
    )
    revs = (await client.get(f"/api/v1/chapters/{chapter_id}/revisions")).json()["data"]
    assert len(revs) == 1
    first_rev = revs[0]["id"]

    # v2 -> v3
    await client.put(
        f"/api/v1/chapters/{chapter_id}/content",
        json={"content": "第三版正文", "base_version": 2},
    )
    assert len((await client.get(f"/api/v1/chapters/{chapter_id}/revisions")).json()["data"]) == 2

    # 回滚到 v1 的快照
    resp = await client.post(f"/api/v1/chapters/{chapter_id}/revisions/{first_rev}/restore")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "少年走出山村" in data["content"], "内容应还原为快照内容"
    assert data["version"] == 4, "回滚不回退版本号，继续自增"

    cur = (await client.get(f"/api/v1/chapters/{chapter_id}/content")).json()["data"]
    assert "少年走出山村" in cur["content"]
    assert cur["version"] == 4

    # 回滚前也快照了当前内容 → 共 3 条
    revs = (await client.get(f"/api/v1/chapters/{chapter_id}/revisions")).json()["data"]
    assert len(revs) == 3


@pytest.mark.asyncio
async def test_restore_missing_revision_404(client, chapter_id):
    resp = await client.post(f"/api/v1/chapters/{chapter_id}/revisions/nonexistent/restore")
    assert resp.status_code == 404
    assert resp.json()["code"] == 40401


@pytest.mark.asyncio
async def test_word_count_updates(client, chapter_id):
    resp = await client.put(
        f"/api/v1/chapters/{chapter_id}/content",
        json={"content": "这是一段共十四个字的正文内容啊", "base_version": 1},
    )
    assert resp.status_code == 200
    wc = resp.json()["data"]["word_count"]
    assert wc > 0

    chapters = None
    vols = (await client.get("/api/v1/novels")).json()["data"]
    novel_id = vols[0]["id"]
    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    chapters = vols[0]["chapters"]
    assert chapters[0]["word_count"] == wc


@pytest.mark.asyncio
async def test_agent_write_source_recorded(client, chapter_id):
    """Agent 写入路径（source="agent"）产生 agent 快照并记 last_writer。"""

    async with get_sessionmaker()() as s:
        result = await volumes_service.write_content(
            s, chapter_id, content="AI 生成的正文", base_version=1, source="agent"
        )
        await s.commit()

    assert result["version"] == 2
    assert result["last_writer"] == "agent"
    assert result["snapshotted"] is True

    # 快照的 source 记录**触发写入的路径**（手册「二、(6) S2」：Agent 写入前快照 source="agent"）
    revs = (await client.get(f"/api/v1/chapters/{chapter_id}/revisions")).json()["data"]
    assert len(revs) == 1
    assert revs[0]["source"] == "agent"
    assert revs[0]["version"] == 1, "快照记录被覆盖前的版本号"
    assert "少年走出山村" in revs[0]["preview"], "快照内容是被覆盖前的旧正文"

    cur = (await client.get(f"/api/v1/chapters/{chapter_id}/content")).json()["data"]
    assert cur["last_writer"] == "agent"
    assert cur["content"] == "AI 生成的正文"


# ---------------------------------------------------------------------------
# ★v1101：AI 编辑锁（章节被 AI 修改期间用户保存被拒）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_chapter_rejects_manual_and_autosave(client, chapter_id):
    """锁定期内：手动保存与自动保存均 409 / 40903，且内容不变。"""
    assert await content_locks.acquire(chapter_id, run_id="run-x", session_id="s", project_id="p")

    resp = await client.put(
        f"/api/v1/chapters/{chapter_id}/content",
        json={"content": "用户偷偷保存的内容", "base_version": 1},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == 40903

    resp = await client.put(
        f"/api/v1/chapters/{chapter_id}/content",
        json={"content": "自动保存的内容", "base_version": 1, "autosave": True},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == 40903

    cur = (await client.get(f"/api/v1/chapters/{chapter_id}/content")).json()["data"]
    assert cur["content"] == "少年走出山村，第一次见到星海。"
    assert cur["version"] == 1

    # 锁列表可见
    locks = (await client.get("/api/v1/chapters/locks")).json()["data"]
    assert any(lk["chapter_id"] == chapter_id and lk["run_id"] == "run-x" for lk in locks)

    # 释放后恢复可写
    assert await content_locks.release(chapter_id, run_id="run-x")
    resp = await client.put(
        f"/api/v1/chapters/{chapter_id}/content",
        json={"content": "释放后可以保存", "base_version": 1},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_lock_release_only_by_holder(client, chapter_id):
    """非持有者释放无效（防误删他人锁）；release_for_run 按 run 清理。"""
    await content_locks.acquire(chapter_id, run_id="run-a", session_id="s", project_id="p")
    assert not await content_locks.release(chapter_id, run_id="run-b")
    assert content_locks.is_locked(chapter_id)

    freed = await content_locks.release_for_run("run-a")
    assert freed == [chapter_id]
    assert not content_locks.is_locked(chapter_id)
    assert (await client.get("/api/v1/chapters/locks")).json()["data"] == []
