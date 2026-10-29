# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_novels_api.py
作者: xionggaoqing
时间: 2026-10-27
说明: 项目 CRUD 与软删除（手册「十一」· 导出/软删除域 + M1 验收口径）。
"""

from __future__ import annotations

import pytest
from tests.conftest import workspace_of


@pytest.mark.asyncio
async def test_create_novel_initializes_workspace(client):
    """创建后：登记行 + 工作区 + 大纲模板文件（三件套）。"""
    resp = await client.post("/api/v1/novels", json={"title": "星海拾荒人"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    novel = body["data"]
    pid = novel["id"]

    assert novel["title"] == "星海拾荒人"
    assert novel["status"] == "active"
    assert novel["outline_file"] == "outline/星海拾荒人-大纲.md"
    assert novel["chapter_count"] == 0

    ws = workspace_of(pid)
    assert (ws / "novel.yaml").is_file()
    assert (ws / "volumes.yaml").is_file()
    assert (ws / "chapters").is_dir()
    assert (ws / "summaries").is_dir()
    outline = ws / "outline" / "星海拾荒人-大纲.md"
    assert outline.is_file(), "创建时必须写入大纲模板文件"
    text = outline.read_text(encoding="utf-8")
    assert "# 附录A 世界观" in text
    assert "- 简介：" in text


@pytest.mark.asyncio
async def test_create_novel_rejects_invalid_title(client):
    """v1028 书名校验：空 / 全阿拉伯数字 / 含 #$% 等特殊字符 → 422 / 42201。"""
    for bad in ("", "   ", "12", "123456", "#星海", "星海$拾荒", "星海%拾荒"):
        resp = await client.post("/api/v1/novels", json={"title": bad})
        assert resp.status_code == 422, f"{bad!r} 应被拒绝，实际 {resp.status_code}"
        assert resp.json()["code"] == 42201


@pytest.mark.asyncio
async def test_create_novel_allows_punctuation_title(client):
    """v1028：书名允许出现标点符号（如冒号、间隔号）。"""
    resp = await client.post("/api/v1/novels", json={"title": "星海拾荒人：黎明"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["title"] == "星海拾荒人：黎明"


@pytest.mark.asyncio
async def test_list_novels_excludes_deleted(client, novel_id):
    resp = await client.get("/api/v1/novels")
    assert [n["id"] for n in resp.json()["data"]] == [novel_id]

    await client.delete(f"/api/v1/novels/{novel_id}")
    resp = await client.get("/api/v1/novels")
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_get_missing_novel_404(client):
    resp = await client.get("/api/v1/novels/nonexistent-id")
    assert resp.status_code == 404
    assert resp.json()["code"] == 40401


@pytest.mark.asyncio
async def test_rename_novel_renames_outline_file(client, novel_id):
    """重命名事务内同步重命名大纲文件，并更新 novel.yaml 与 DB。"""
    ws = workspace_of(novel_id)
    old = ws / "outline" / "星海拾荒人-大纲.md"
    assert old.is_file()

    resp = await client.patch(f"/api/v1/novels/{novel_id}", json={"title": "银河拾荒记"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["outline_file"] == "outline/银河拾荒记-大纲.md"

    assert not old.exists(), "旧大纲文件应已被重命名"
    new = ws / "outline" / "银河拾荒记-大纲.md"
    assert new.is_file()

    # novel.yaml 与 DB 均同步
    import yaml

    meta = yaml.safe_load((ws / "novel.yaml").read_text(encoding="utf-8"))
    assert meta["title"] == "银河拾荒记"
    assert meta["outline_file"] == "outline/银河拾荒记-大纲.md"

    detail = (await client.get(f"/api/v1/novels/{novel_id}")).json()["data"]
    assert detail["title"] == "银河拾荒记"


@pytest.mark.asyncio
async def test_rename_to_invalid_title_rejected(client, novel_id):
    """v1028：重命名为全阿拉伯数字 / 含特殊字符仍被拒。"""
    resp = await client.patch(f"/api/v1/novels/{novel_id}", json={"title": "123456"})
    assert resp.status_code == 422
    assert resp.json()["code"] == 42201


@pytest.mark.asyncio
async def test_soft_delete_moves_workspace_to_trash(client, novel_id):
    """软删除：工作区移入 data/trash/ + status=deleted，列表不可见。"""
    from app.core.paths import trash_root

    ws = workspace_of(novel_id)
    assert ws.exists()

    resp = await client.delete(f"/api/v1/novels/{novel_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "deleted"

    assert not ws.exists(), "工作区应已移出 workspaces/"
    moved = list(trash_root().glob(f"{novel_id}-*"))
    assert len(moved) == 1
    assert (moved[0] / "novel.yaml").is_file()

    # 删除后再取详情 → 404
    assert (await client.get(f"/api/v1/novels/{novel_id}")).status_code == 404


@pytest.mark.asyncio
async def test_duplicate_novel(client, novel_id, volume_id, chapter_id):
    """复制项目：副本有独立 ID，正文与卷章齐备。"""
    resp = await client.post(
        f"/api/v1/novels/{novel_id}/duplicate", json={"title": "星海拾荒人（副本）"}
    )
    assert resp.status_code == 200, resp.text
    copy = resp.json()["data"]
    assert copy["id"] != novel_id
    assert copy["chapter_count"] == 1

    vols = (await client.get(f"/api/v1/novels/{copy['id']}/volumes")).json()["data"]
    assert len(vols) == 1
    assert vols[0]["title"] == "荒原初醒"
    assert vols[0]["chapters"][0]["title"] == "拾荒少年的清晨"


@pytest.mark.asyncio
async def test_reading_progress_roundtrip(client, novel_id, chapter_id):
    """进度记忆：novel_id 主键单行，写入后可读回。"""
    got = (await client.get(f"/api/v1/novels/{novel_id}/reading-progress")).json()["data"]
    assert got["chapter_id"] is None
    assert got["scroll_ratio"] == 0.0

    resp = await client.put(
        f"/api/v1/novels/{novel_id}/reading-progress",
        json={"chapter_id": chapter_id, "scroll_ratio": 0.42},
    )
    assert resp.status_code == 200, resp.text

    got = (await client.get(f"/api/v1/novels/{novel_id}/reading-progress")).json()["data"]
    assert got["chapter_id"] == chapter_id
    assert got["scroll_ratio"] == 0.42

    # 单行覆盖，不新增
    await client.put(
        f"/api/v1/novels/{novel_id}/reading-progress",
        json={"chapter_id": chapter_id, "scroll_ratio": 0.9},
    )
    got = (await client.get(f"/api/v1/novels/{novel_id}/reading-progress")).json()["data"]
    assert got["scroll_ratio"] == 0.9


@pytest.mark.asyncio
async def test_reading_progress_ratio_bounds(client, novel_id):
    resp = await client.put(
        f"/api/v1/novels/{novel_id}/reading-progress", json={"scroll_ratio": 1.5}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 40001


@pytest.mark.asyncio
async def test_settings_roundtrip(client):
    got = (await client.get("/api/v1/settings")).json()["data"]
    assert got["autosave_interval_sec"] == 30
    assert got["city"] == "北京"

    resp = await client.put(
        "/api/v1/settings",
        json={"theme": "dark", "font_size": 22, "city": "杭州", "autosave_interval_sec": 60},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert (data["theme"], data["font_size"], data["city"]) == ("dark", 22, "杭州")
    assert data["autosave_interval_sec"] == 60

    again = (await client.get("/api/v1/settings")).json()["data"]
    assert again["theme"] == "dark"


@pytest.mark.asyncio
async def test_settings_font_size_bounds(client):
    resp = await client.put("/api/v1/settings", json={"font_size": 200})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_audit_logs_written(client, novel_id):
    """关键动作落审计：SQLite 表 + JSONL 备份双写。"""
    from sqlalchemy import select

    from app.db.models import AuditLog
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        actions = (
            await s.execute(select(AuditLog.action).order_by(AuditLog.created_at))
        ).scalars().all()

    assert "novel.create" in actions

    from app.core.paths import logs_root

    audit_files = list(logs_root().glob("audit-*.jsonl"))
    assert audit_files, "审计必须有 JSONL 备份"
    assert "novel.create" in audit_files[0].read_text(encoding="utf-8")
