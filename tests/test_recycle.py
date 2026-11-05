# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_recycle.py
作者: xionggaoqing
时间: 2026-10-31
说明: 回收站契约测试。

    覆盖：
    - 删除章节 → 进回收站（按小说归档，可浏览「卷 → 被删章节」）；
    - 回收单个章节 → 还原到原卷（卷删空后被级联删除的，按快照重建）；
    - 彻底删除回收站里的章节 → 归档文件消失；
    - 删除整本小说 → 进回收站（novel_deleted=true）；回收任一章节 → 整本恢复（含大纲）；
    - 回收站只保留最近 20 本。
"""

from __future__ import annotations

import shutil

import pytest
from conftest import data_root_of, workspace_of

from app.core.errors import NotFoundError
from app.db.session import get_sessionmaker
from app.services import recycle


async def _make_novel(client, title: str = "星海拾荒人") -> str:
    resp = await client.post("/api/v1/novels", json={"title": title, "genre": "科幻"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


async def _add_volume_chapter(client, novel_id: str, vol_title: str, ch_title: str) -> tuple[str, str]:
    vol = (
        await client.post(
            f"/api/v1/novels/{novel_id}/volumes",
            json={"title": vol_title, "first_chapter_title": ch_title},
        )
    ).json()["data"]
    return vol["id"], vol["chapters"][0]["id"]


@pytest.mark.asyncio
async def test_delete_chapter_archives_to_recycle(client):
    """删除章节 → 回收站首页出现该小说，二级目录能列出被删章节。"""
    novel_id = await _make_novel(client)
    _vol_id, ch_id = await _add_volume_chapter(client, novel_id, "风起", "拾荒少年")

    resp = await client.delete(f"/api/v1/chapters/{ch_id}")
    assert resp.status_code == 200, resp.text

    items = (await client.get("/api/v1/recycle")).json()["data"]["items"]
    assert any(n["novel_id"] == novel_id for n in items)
    entry = next(n for n in items if n["novel_id"] == novel_id)
    assert entry["novel_deleted"] is False
    assert entry["chapter_count"] == 1

    contents = (await client.get(f"/api/v1/recycle/{novel_id}")).json()["data"]
    assert contents["total_chapters"] == 1
    assert contents["volumes"][0]["chapters"][0]["id"] == ch_id


@pytest.mark.asyncio
async def test_restore_chapter_rebuilds_volume(client):
    """卷只剩这一章时删章会级联删卷；回收该章 → 卷按快照重建、章节还原。"""
    novel_id = await _make_novel(client)
    vol_id, ch_id = await _add_volume_chapter(client, novel_id, "风起", "拾荒少年")

    await client.delete(f"/api/v1/chapters/{ch_id}")
    # 卷已随最后一章被删除
    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    assert vols == []

    resp = await client.post(f"/api/v1/recycle/{novel_id}/chapters/{ch_id}/restore")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["restored"] is True
    assert data["novel_restored"] is False

    # 卷与章节都回来了
    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    assert len(vols) == 1
    assert vols[0]["id"] == vol_id
    assert vols[0]["title"] == "风起"
    assert [c["id"] for c in vols[0]["chapters"]] == [ch_id]

    # 回收站里这本小说已清空 → 归档目录移除
    items = (await client.get("/api/v1/recycle")).json()["data"]["items"]
    assert all(n["novel_id"] != novel_id for n in items)


@pytest.mark.asyncio
async def test_purge_chapter_removes_archive(client):
    """彻底删除回收站里的章节 → 归档文件消失、清单更新。"""
    novel_id = await _make_novel(client)
    _vol_id, ch_id = await _add_volume_chapter(client, novel_id, "风起", "拾荒少年")

    await client.delete(f"/api/v1/chapters/{ch_id}")
    assert (data_root_of() / "recycle" / novel_id / "chapters" / f"c{ch_id}.md").exists()

    resp = await client.delete(f"/api/v1/recycle/{novel_id}/chapters/{ch_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["purged"] is True
    assert not (data_root_of() / "recycle" / novel_id / "chapters" / f"c{ch_id}.md").exists()

    items = (await client.get("/api/v1/recycle")).json()["data"]["items"]
    assert all(n["novel_id"] != novel_id for n in items)


@pytest.mark.asyncio
async def test_delete_whole_novel_then_restore_via_recycle(client):
    """删除整本小说 → 回收站标 novel_deleted；回收任一章节 → 整本恢复（含大纲）。"""
    novel_id = await _make_novel(client, title="玉芬")
    _vol_id, ch_id = await _add_volume_chapter(client, novel_id, "风起", "拾荒少年")
    outline_file = workspace_of(novel_id) / "outline"

    resp = await client.delete(f"/api/v1/novels/{novel_id}")
    assert resp.status_code == 200, resp.text

    # 工作区已移入 trash，左边栏列表不再包含它
    assert not workspace_of(novel_id).exists()
    novels = (await client.get("/api/v1/novels")).json()["data"]
    assert all(n["id"] != novel_id for n in novels)

    items = (await client.get("/api/v1/recycle")).json()["data"]["items"]
    entry = next(n for n in items if n["novel_id"] == novel_id)
    assert entry["novel_deleted"] is True

    contents = (await client.get(f"/api/v1/recycle/{novel_id}")).json()["data"]
    assert contents["novel_deleted"] is True
    assert contents["total_chapters"] >= 1

    # 回收其中一章 → 整本恢复
    resp = await client.post(f"/api/v1/recycle/{novel_id}/chapters/{ch_id}/restore")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["novel_restored"] is True

    # 工作区、大纲、登记行都回来了
    assert workspace_of(novel_id).exists()
    assert outline_file.exists()
    novels = (await client.get("/api/v1/novels")).json()["data"]
    assert any(n["id"] == novel_id and n["title"] == "玉芬" for n in novels)

    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    assert len(vols) == 1
    assert [c["id"] for c in vols[0]["chapters"]] == [ch_id]

    # 回收站里这本小说已离开
    items = (await client.get("/api/v1/recycle")).json()["data"]["items"]
    assert all(n["novel_id"] != novel_id for n in items)


@pytest.mark.asyncio
async def test_purge_last_chapter_of_deleted_novel_removes_novel(client):
    """整本被删的小说，在回收站里把卷章删干净 → 该本小说也从回收站彻底删除
    （trash 里的工作区一并清除）。"""

    novel_id = await _make_novel(client, title="清空测试")
    _vol_id, ch_id = await _add_volume_chapter(client, novel_id, "风起", "拾荒少年")

    resp = await client.delete(f"/api/v1/novels/{novel_id}")
    assert resp.status_code == 200, resp.text

    items = (await client.get("/api/v1/recycle")).json()["data"]["items"]
    entry = next(n for n in items if n["novel_id"] == novel_id)
    assert entry["novel_deleted"] is True
    assert entry["chapter_count"] == 1

    # 彻底删除最后一章 → 整本小说从回收站消失，trash 工作区也没了
    resp = await client.delete(f"/api/v1/recycle/{novel_id}/chapters/{ch_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["purged"] is True

    items = (await client.get("/api/v1/recycle")).json()["data"]["items"]
    assert all(n["novel_id"] != novel_id for n in items)
    trash = data_root_of() / "trash"
    assert not list(trash.glob(f"{novel_id}-*")) if trash.exists() else True


@pytest.mark.asyncio
async def test_novel_with_empty_trash_is_pruned_from_recycle(client):
    """整本被删但 trash 工作区已无章节（如被手工清理）→ 列表时自动清掉条目。"""

    novel_id = await _make_novel(client, title="脏条目")
    _vol_id, ch_id = await _add_volume_chapter(client, novel_id, "风起", "拾荒少年")
    await client.delete(f"/api/v1/novels/{novel_id}")

    # 手工清空 trash 里的工作区，模拟「卷章已被删干净」
    trash = data_root_of() / "trash"
    for d in trash.glob(f"{novel_id}-*"):

        shutil.rmtree(d)

    items = (await client.get("/api/v1/recycle")).json()["data"]["items"]
    assert all(n["novel_id"] != novel_id for n in items)


@pytest.mark.asyncio
async def test_recycle_keeps_only_recent_20_novels(client):
    """回收站只保留最近删除的 20 本（第 21 本进来时最旧的一本被丢弃）。"""

    novel_ids: list[str] = []
    for i in range(21):
        nid = await _make_novel(client, title=f"书{i:02d}")
        _v, c = await _add_volume_chapter(client, nid, "卷", f"章{i:02d}")
        await client.delete(f"/api/v1/chapters/{c}")
        novel_ids.append(nid)

    items = (await client.get("/api/v1/recycle")).json()["data"]["items"]
    assert len(items) == recycle.MAX_NOVELS == 20
    kept = {n["novel_id"] for n in items}
    # 最早删除的那本被挤出回收站
    assert novel_ids[0] not in kept
    assert novel_ids[-1] in kept


@pytest.mark.asyncio
async def test_recycle_rejects_path_traversal_ids(client):
    """路径安全：URL 里的 novel_id / chapter_id 未经校验就拼路径可穿越出 `data/recycle/`
    （回收站不经 WorkspaceGuard）→ 一律按「不存在」拒绝，且不触碰回收站之外的文件。
    """

    novel_id = await _make_novel(client)
    _vol_id, ch_id = await _add_volume_chapter(client, novel_id, "风起", "拾荒少年")
    await client.delete(f"/api/v1/chapters/{ch_id}")

    sentinel = data_root_of() / "outside.md"
    sentinel.write_text("不该被动到", encoding="utf-8")

    # novel_id 是 uuid（含连字符），chapter_id 是 8 位短 ID；下列形态都不得拼进路径
    bad_novels = ["../outside", "..%2F..%2Fetc", "a/b", ".", "", "x" * 80]
    bad_chapters = ["../outside", "../../outside.md", "c:/windows/x", "a\\b", ""]

    async with get_sessionmaker()() as s:
        for bad in bad_novels:
            with pytest.raises(NotFoundError):
                recycle.get_recycled_contents(bad)
            with pytest.raises(NotFoundError):
                await recycle.restore_chapter(s, bad, ch_id)
            with pytest.raises(NotFoundError):
                await recycle.purge_chapter(s, bad, ch_id)

        for bad in bad_chapters:
            with pytest.raises(NotFoundError):
                await recycle.restore_chapter(s, novel_id, bad)
            with pytest.raises(NotFoundError):
                await recycle.purge_chapter(s, novel_id, bad)

    # 合法 ID 仍照常工作（白名单没把正常 uuid / 短 ID 挡掉），归档原封不动
    assert recycle.get_recycled_contents(novel_id)["total_chapters"] == 1
    assert (data_root_of() / "recycle" / novel_id / "chapters" / f"c{ch_id}.md").exists()
    assert sentinel.read_text(encoding="utf-8") == "不该被动到"
