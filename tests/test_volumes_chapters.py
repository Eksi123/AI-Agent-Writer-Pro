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

import frontmatter
import pytest
from tests.conftest import workspace_of

from app.main import app
from app.services import volumes as volumes_service

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
async def test_create_volume_rejects_invalid_name(client, novel_id):
    """v1029：卷名与书名同规则 —— 只拒绝空名 / 全阿拉伯数字 / 含特殊字符。"""
    for bad in ("", "   ", "12", "#星海", "星海$拾荒"):
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
async def test_create_chapter_rejects_invalid_name(client, volume_id):
    """v1029：章名与书名同规则 —— 只拒绝空名 / 全阿拉伯数字 / 含特殊字符。"""
    for bad in ("", "   ", "12", "#星海", "星海%拾荒"):
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


    meta = frontmatter.load(str(old)).metadata
    assert meta["title"] == "拾荒少年的黎明"


@pytest.mark.asyncio
async def test_delete_chapter(client, novel_id, volume_id, chapter_id):
    ws = workspace_of(novel_id)
    resp = await client.delete(f"/api/v1/chapters/{chapter_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["deleted"] is True
    assert not (ws / "chapters" / f"v{volume_id}" / f"c{chapter_id}.md").exists()

    # ★v1029：该卷下所有章节删完 → 卷一并删除
    assert resp.json()["data"]["volume_deleted"] is True
    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    assert vols == []


@pytest.mark.asyncio
async def test_delete_chapter_keeps_volume_with_siblings(client, novel_id, volume_id, chapter_id):
    """卷下仍有其它章时，删一章不删卷，且剩余章重新连续编号。"""
    await client.post(f"/api/v1/volumes/{volume_id}/chapters", json={"title": "废铁镇的钟声"})
    await client.post(f"/api/v1/volumes/{volume_id}/chapters", json={"title": "第一艘离港的船"})
    before = (await client.get(f"/api/v1/volumes/{volume_id}/chapters")).json()["data"]
    assert [c["sort_order"] for c in before] == [1, 2, 3]

    middle = next(c for c in before if c["sort_order"] == 2)
    resp = await client.delete(f"/api/v1/chapters/{middle['id']}")
    assert resp.json()["data"]["volume_deleted"] is False

    after = (await client.get(f"/api/v1/volumes/{volume_id}/chapters")).json()["data"]
    assert [c["title"] for c in after] == ["拾荒少年的清晨", "第一艘离港的船"]
    assert [c["sort_order"] for c in after] == [1, 2], "删章后卷内重新连续编号"


@pytest.mark.asyncio
async def test_delete_last_chapter_removes_volume_and_renumbers(client, novel_id, volume_id, chapter_id):
    """v1029：删空某卷 → 卷消失，其余卷重新连续编号（1..N）。"""
    await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": "星港夜航"})
    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    second = next(v for v in vols if v["title"] == "星港夜航")
    await client.post(f"/api/v1/volumes/{second['id']}/chapters", json={"title": "登船"})

    first_chapters = (await client.get(f"/api/v1/volumes/{volume_id}/chapters")).json()["data"]
    for ch in first_chapters:
        await client.delete(f"/api/v1/chapters/{ch['id']}")

    remaining = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    assert [v["title"] for v in remaining] == ["星港夜航"]
    assert remaining[0]["sort_order"] == 1, "删空卷后其余卷重新连续编号"


@pytest.mark.asyncio
async def test_create_volume_with_first_chapter_title(client, novel_id):
    """v1030续：插入卷时带上第一章章节名 → 卷与它的第一章一并创建（★不允许空卷）。"""
    resp = await client.post(
        f"/api/v1/novels/{novel_id}/volumes",
        json={"title": "风起", "ordinal": 1, "first_chapter_title": "第一章 拾荒"},
    )
    assert resp.status_code == 200, resp.text
    vol = resp.json()["data"]
    assert vol["sort_order"] == 1
    assert vol["chapter_count"] == 1
    assert vol["chapters"][0]["title"] == "第一章 拾荒"

    listed = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    target = next(v for v in listed if v["title"] == "风起")
    assert target["chapter_count"] == 1


@pytest.mark.asyncio
async def test_create_volume_with_ordinal_inserts_and_shifts(client, novel_id, volume_id):
    """v1029：带 ordinal 建卷 = 往前插入，其后各卷顺延 +1。"""
    await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": "第二站"})
    await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": "第三站"})

    resp = await client.post(
        f"/api/v1/novels/{novel_id}/volumes", json={"title": "插在最前", "ordinal": 1}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["sort_order"] == 1

    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    assert [v["title"] for v in vols] == ["插在最前", "荒原初醒", "第二站", "第三站"]
    assert [v["sort_order"] for v in vols] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_create_chapter_with_ordinal_inserts_and_shifts(client, novel_id, volume_id, chapter_id):
    """v1029：带 ordinal 建章 = 往前插入章节，其后各章顺延 +1。"""
    await client.post(f"/api/v1/volumes/{volume_id}/chapters", json={"title": "第二章 钟声"})

    resp = await client.post(
        f"/api/v1/volumes/{volume_id}/chapters",
        json={"title": "插入的第二章", "ordinal": 2},
    )
    assert resp.status_code == 200, resp.text

    chapters = (await client.get(f"/api/v1/volumes/{volume_id}/chapters")).json()["data"]
    assert [c["sort_order"] for c in chapters] == [1, 2, 3]
    assert chapters[1]["title"] == "插入的第二章"
    assert chapters[2]["title"] == "第二章 钟声"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, 99])
async def test_create_with_bad_ordinal_rejected(client, novel_id, volume_id, bad):
    """v1029：序号必须是正整数且不超出 1..N+1。"""
    resp = await client.post(
        f"/api/v1/novels/{novel_id}/volumes", json={"title": "非法序号卷", "ordinal": bad}
    )
    assert resp.status_code in (400, 422), f"{bad} -> {resp.status_code}"

    resp2 = await client.post(
        f"/api/v1/volumes/{volume_id}/chapters", json={"title": "非法序号章", "ordinal": bad}
    )
    assert resp2.status_code in (400, 422), f"{bad} -> {resp2.status_code}"


@pytest.mark.asyncio
async def test_rename_chapter_with_volume_title(client, novel_id, volume_id, chapter_id):
    """v1029：重命名弹窗同时改章名与所属卷名（章仍留在原卷）。"""
    resp = await client.patch(
        f"/api/v1/chapters/{chapter_id}",
        json={"title": "拾荒少年的黎明", "volume_title": "荒原初醒（修订）"},
    )
    assert resp.status_code == 200, resp.text

    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    assert vols[0]["id"] == volume_id, "改卷名不改卷 ID"
    assert vols[0]["title"] == "荒原初醒（修订）"
    assert vols[0]["chapters"][0]["title"] == "拾荒少年的黎明"


@pytest.mark.asyncio
async def test_rename_chapter_keeps_volume_when_unchanged(client, novel_id, volume_id, chapter_id):
    """v1029：卷名默认填当前卷名（不修改）→ 卷名保持原样。"""
    before = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"][0]

    resp = await client.patch(
        f"/api/v1/chapters/{chapter_id}",
        json={"title": "新章名", "volume_title": before["title"]},
    )
    assert resp.status_code == 200, resp.text

    after = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"][0]
    assert after["title"] == before["title"]
    assert after["sort_order"] == before["sort_order"]


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

    paths = app.openapi()["paths"]
    offenders = [p for p in paths if "reorder" in p.lower() or "/move" in p.lower()]
    assert offenders == [], f"违反铁律 10，出现了重排/移动路由：{offenders}"


def test_service_module_exports_no_reorder_functions():
    """服务层不得存在重排/移动函数（设计守卫）。"""

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
