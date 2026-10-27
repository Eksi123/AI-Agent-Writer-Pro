#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_volumes_chapters.py
作者: xionggaoqing
时间: 2026-10-27
说明: 卷/章 CRUD 与顺序契约（手册「一」铁律 10 / 「三、(3)【卷章】」）。

    ★核心断言：
    - 新建一律追加到末尾（sort_order = max+1）；
    - **无任何重排接口**（reorder / move 路由不存在，返回 404/405）；
    - 章不可跨卷移动；
    - 删除卷级联删除章节文件。
"""

from __future__ import annotations

import pytest
from tests.conftest import workspace_of

# ---------------------------------------------------------------------------
# 卷
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_volume_appends_to_end(client, novel_id):
    """新建卷追加到末尾，顺序只增不改。"""
    ids = []
    for i, title in enumerate(["荒原初醒", "星港夜航", "深空回响"], start=1):
        resp = await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": title})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["sort_order"] == i, f"第 {i} 卷 sort_order 应为 {i}，实际 {data['sort_order']}"
        ids.append(data["id"])

    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    assert [v["title"] for v in vols] == ["荒原初醒", "星港夜航", "深空回响"]
    assert [v["sort_order"] for v in vols] == [1, 2, 3]


@pytest.mark.asyncio
async def test_create_volume_rejects_default_name(client, novel_id):
    for bad in ("第 1 卷", "未命名卷", ""):
        resp = await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": bad})
        assert resp.status_code == 422, f"{bad!r} -> {resp.status_code}"
        assert resp.json()["code"] == 42201


@pytest.mark.asyncio
async def test_volume_ids_are_short_and_stable(client, novel_id, volume_id):
    """短 ID = uuid 前 8 位；改名后 ID 不变（铁律 10）。"""
    assert len(volume_id) == 8
    assert volume_id.isalnum()

    await client.patch(f"/api/v1/volumes/{volume_id}", json={"title": "荒原初醒（修订版）"})
    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    assert vols[0]["id"] == volume_id, "改名不得改变卷 ID"
    assert vols[0]["title"] == "荒原初醒（修订版）"

    ws = workspace_of(novel_id)
    assert (ws / "chapters" / f"v{volume_id}").is_dir(), "目录名含 ID，不得随改名变化"


@pytest.mark.asyncio
async def test_rename_volume_does_not_touch_sort_order(client, novel_id, volume_id):
    await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": "星港夜航"})
    before = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]

    await client.patch(f"/api/v1/volumes/{volume_id}", json={"summary": "改了简介"})
    after = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]

    assert [v["sort_order"] for v in after] == [v["sort_order"] for v in before]


@pytest.mark.asyncio
async def test_delete_volume_cascades_chapter_files(client, novel_id, volume_id, chapter_id):
    """删除卷级联删除其下章节文件。"""
    ws = workspace_of(novel_id)
    ch_file = ws / "chapters" / f"v{volume_id}" / f"c{chapter_id}.md"
    assert ch_file.is_file()

    resp = await client.delete(f"/api/v1/volumes/{volume_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["chapters_deleted"] == 1

    assert not ch_file.exists(), "章节文件应已级联删除"
    assert not (ws / "chapters" / f"v{volume_id}").exists(), "卷目录应已删除"

    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    assert vols == []


@pytest.mark.asyncio
async def test_missing_volume_404(client):
    assert (await client.patch("/api/v1/volumes/deadbeef", json={"title": "新名字"})).status_code == 404
    assert (await client.delete("/api/v1/volumes/deadbeef")).status_code == 404


# ---------------------------------------------------------------------------
# 章
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_chapter_appends_to_volume_end(client, novel_id, volume_id):
    for i, title in enumerate(["拾荒少年的清晨", "废铁镇的钟声", "第一艘离港的船"], start=1):
        resp = await client.post(
            f"/api/v1/volumes/{volume_id}/chapters", json={"title": title}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["sort_order"] == i

    chapters = (await client.get(f"/api/v1/volumes/{volume_id}/chapters")).json()["data"]
    assert [c["title"] for c in chapters] == [
        "拾荒少年的清晨",
        "废铁镇的钟声",
        "第一艘离港的船",
    ]
    assert all(c["version"] == 1 for c in chapters)
    assert all(c["status"] == "draft" for c in chapters)


@pytest.mark.asyncio
async def test_create_chapter_rejects_default_name(client, volume_id):
    for bad in ("第 1 章", "未命名章节", "12"):
        resp = await client.post(f"/api/v1/volumes/{volume_id}/chapters", json={"title": bad})
        assert resp.status_code == 422, f"{bad!r} -> {resp.status_code}"
        assert resp.json()["code"] == 42201


@pytest.mark.asyncio
async def test_chapter_file_layout_contains_ids(client, novel_id, volume_id, chapter_id):
    """章节文件名 `c{chapter_id}.md` 位于 `chapters/v{vol_id}/`（ID 进路径，故 ID 必须稳定）。"""
    ws = workspace_of(novel_id)
    path = ws / "chapters" / f"v{volume_id}" / f"c{chapter_id}.md"
    assert path.is_file()

    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), "章节文件应以 YAML frontmatter 开头"
    assert "title: 拾荒少年的清晨" in text
    assert "version: 1" in text
    assert "少年走出山村" in text


@pytest.mark.asyncio
async def test_rename_chapter_keeps_id_and_filename(client, novel_id, volume_id, chapter_id):
    ws = workspace_of(novel_id)
    old = ws / "chapters" / f"v{volume_id}" / f"c{chapter_id}.md"

    resp = await client.patch(f"/api/v1/chapters/{chapter_id}", json={"title": "拾荒少年的黎明"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["id"] == chapter_id

    assert old.is_file(), "改名不得改变文件名（文件名含 ID）"
    md_files = list((ws / "chapters" / f"v{volume_id}").glob("c*.md"))
    assert md_files == [old], "改名后该卷下仍只有这一个章文件"

    import frontmatter

    meta = frontmatter.load(str(old)).metadata
    assert meta["title"] == "拾荒少年的黎明"


@pytest.mark.asyncio
async def test_delete_chapter(client, novel_id, volume_id, chapter_id):
    ws = workspace_of(novel_id)
    resp = await client.delete(f"/api/v1/chapters/{chapter_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["deleted"] is True
    assert not (ws / "chapters" / f"v{volume_id}" / f"c{chapter_id}.md").exists()

    chapters = (await client.get(f"/api/v1/volumes/{volume_id}/chapters")).json()["data"]
    assert chapters == []


@pytest.mark.asyncio
async def test_missing_chapter_404(client):
    assert (await client.get("/api/v1/chapters/deadbeef/content")).status_code == 404
    assert (await client.delete("/api/v1/chapters/deadbeef")).status_code == 404


# ---------------------------------------------------------------------------
# ★铁律 10：无手工重排 / 无跨卷移动
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/v1/novels/{novel_id}/volumes/reorder"),
        ("post", "/api/v1/volumes/{volume_id}/chapters/reorder"),
        ("post", "/api/v1/volumes/reorder"),
        ("post", "/api/v1/chapters/reorder"),
        ("post", "/api/v1/chapters/{chapter_id}/move"),
        ("patch", "/api/v1/chapters/{chapter_id}/move"),
        ("post", "/api/v1/volumes/{volume_id}/move"),
        ("put", "/api/v1/novels/{novel_id}/volumes/order"),
    ],
)
async def test_no_reorder_or_move_routes(client, novel_id, volume_id, chapter_id, method, path):
    """★断言路由不存在：卷章顺序只能由大纲派生，不提供任何手工重排入口。"""
    url = path.format(novel_id=novel_id, volume_id=volume_id, chapter_id=chapter_id)
    resp = await getattr(client, method)(url, json={"ids": [chapter_id], "sort_order": 1})
    assert resp.status_code in (404, 405), f"{method.upper()} {url} 不应存在，实际 {resp.status_code}"


@pytest.mark.asyncio
async def test_openapi_has_no_reorder_paths(client):
    """从 OpenAPI schema 层面再断言一次（防止将来误加）。"""
    from app.main import app

    paths = app.openapi()["paths"]
    offenders = [p for p in paths if "reorder" in p.lower() or "/move" in p.lower()]
    assert offenders == [], f"违反铁律 10，出现了重排/移动路由：{offenders}"


def test_service_module_exports_no_reorder_functions():
    """服务层不得存在重排/移动函数（设计守卫）。"""
    from app.services import volumes as volumes_service

    forbidden = {"reorder_volumes", "reorder_chapters", "move_chapter", "move_volume"}
    leaked = forbidden & set(dir(volumes_service))
    assert not leaked, f"违反铁律 10：服务层出现手工重排函数 {leaked}"

    volumes_service.assert_no_manual_reorder()


# ---------------------------------------------------------------------------
# 项目统计
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_index_reflects_chapters(client, novel_id, volume_id):
    for title in ("第一章 拾荒", "第二章 星港", "第三章 深空"):
        await client.post(f"/api/v1/volumes/{volume_id}/chapters", json={"title": title, "content": "正文内容一二三四五"})

    detail = (await client.get(f"/api/v1/novels/{novel_id}")).json()["data"]
    assert detail["chapter_count"] == 3
    assert detail["total_words"] > 0

    listed = (await client.get("/api/v1/novels")).json()["data"]
    assert listed[0]["chapter_count"] == 3


@pytest.mark.asyncio
async def test_index_rebuild_endpoint(client, novel_id, volume_id, chapter_id):
    resp = await client.post(f"/api/v1/novels/{novel_id}/index/rebuild")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["project_id"] == novel_id
    assert data["dirty"] == 0
    assert data["chapter_count"] == 1
