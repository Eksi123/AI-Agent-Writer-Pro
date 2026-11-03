# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_outline_order.py
作者: xionggaoqing
时间: 2026-10-27
说明: 大纲解析 + 卷章顺序派生（手册「四」/「十一 · 大纲解析 / 卷章顺序派生」域）。

    ★核心断言：
    - 严格层级判定：`## 第三章 卷土重来` 不得判为卷节（旧正则的实际 bug）；
    - 附录 H2 不参与章匹配；归一化比较（全角空格/序数词/标点差异视为同名）；
    - `sync_order_from_outline`：调换大纲 H1/H2 先后 → sort_order 回填、**ID/文件名不变**；
    - 顺序回填**不产生修订快照、不递增 version**；未登记卷/章追加末尾并出警告。
"""

from __future__ import annotations

import pytest
from conftest import workspace_of

from app.services.outline import normalize_title, parse_outline

# ---------------------------------------------------------------------------
# 严格层级判定
# ---------------------------------------------------------------------------


def test_strict_heading_levels():
    text = """\
# 第一卷 风起
## 第一章 出山
- 简介：少年离村。
## 第二章 卷土重来
- 简介：少年归来。
# 第二卷 云涌
## 第三章 星海
- 简介：初入星海。
"""
    result = parse_outline(text)
    assert [v.title for v in result.volumes] == ["第一卷 风起", "第二卷 云涌"]
    assert [c.title for c in result.volumes[0].chapters] == ["第一章 出山", "第二章 卷土重来"]
    assert [c.title for c in result.volumes[1].chapters] == ["第三章 星海"]


def test_chapter_with_juan_char_not_misjudged_as_volume():
    """★陷阱用例：`## 第三章 卷土重来` 含「卷」字，但它是 H2 章节，不得判为卷节。"""
    text = "# 第一卷 序\n## 第三章 卷土重来\n- 简介：内容。\n"
    result = parse_outline(text)
    assert len(result.volumes) == 1, "只应有一个卷节"
    assert result.volumes[0].title == "第一卷 序"
    assert len(result.volumes[0].chapters) == 1
    assert result.volumes[0].chapters[0].title == "第三章 卷土重来"


def test_h3_and_deeper_ignored():
    text = "# 卷一 起\n### 三级标题\n## 章一\n- 简介：x\n#### 四级\n"
    result = parse_outline(text)
    assert len(result.volumes) == 1
    assert [c.title for c in result.volumes[0].chapters] == ["章一"]


def test_appendix_h2_not_matched_as_chapter():
    """附录节下的 H2 是附录子条目，不参与章匹配。"""
    text = """\
# 第一卷 起
## 第一章 出山
- 简介：内容。
# 附录B 人物角色
## 主角 李拾荒
- 设定：少年。
## 配角 老陈
"""
    result = parse_outline(text)
    assert len(result.volumes) == 1
    assert len(result.volumes[0].chapters) == 1
    assert len(result.appendices) == 1
    assert result.appendices[0].title == "附录B 人物角色"


def test_intro_and_link_note_parsed():
    text = "# 卷一\n## 章一\n- 简介：少年出山。\n- 衔接要点：承接序章。\n"
    ch = parse_outline(text).volumes[0].chapters[0]
    assert ch.intro == "少年出山。"
    assert ch.link_note == "承接序章。"
    assert not ch.intro_empty


def test_section_exists_but_intro_empty():
    """小节存在但简介为空 —— 与「无对应小节」是两类缺失。"""
    text = "# 卷一\n## 章一\n## 章二\n- 简介：有内容。\n"
    vols = parse_outline(text).volumes
    assert vols[0].chapters[0].intro_empty is True
    assert vols[0].chapters[1].intro_empty is False


def test_code_fence_not_parsed_as_heading():
    text = "# 卷一\n```\n# 这不是标题\n```\n## 章一\n- 简介：x\n"
    result = parse_outline(text)
    assert len(result.volumes) == 1
    assert [c.title for c in result.volumes[0].chapters] == ["章一"]


def test_empty_outline_warns():
    result = parse_outline("")
    assert any("没有任何 H1" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("第一章 出山", "出山"),
        ("第1章　出山", "出山"),  # 全角空格
        ("第三章·卷土重来", "卷土重来"),
        ("第 十 回 大结局", "大结局"),
        ("第一卷：风起云涌", "风起云涌"),
        ("出山！", "出山"),
    ],
)
def test_normalize_title_equivalence(a, b):
    assert normalize_title(a) == normalize_title(b)


def test_normalize_title_distinguishes_different_names():
    assert normalize_title("出山") != normalize_title("归山")


# ---------------------------------------------------------------------------
# outline_order_diff（★v1029：大纲降级为参考资料，只读差异报告，不再回填 sort_order）
# ---------------------------------------------------------------------------


OUTLINE_V1 = """\
# 第一卷 荒原初醒
## 第一章 拾荒少年的清晨
- 简介：少年出山。
## 第二章 废铁镇的钟声
- 简介：进镇。
# 第二卷 星港夜航
## 第三章 第一艘离港的船
- 简介：登船。
"""

#: 把两个卷、卷内两章的顺序都调换
OUTLINE_REORDERED = """\
# 第二卷 星港夜航
## 第三章 第一艘离港的船
- 简介：登船。
# 第一卷 荒原初醒
## 第二章 废铁镇的钟声
- 简介：进镇。
## 第一章 拾荒少年的清晨
- 简介：少年出山。
"""


async def _build_three_chapters(client, novel_id, volume_id):
    """在卷1建两章、再建卷2一章，返回 (vol2_id, ch_ids_by_title)。"""
    ch1 = (await client.post(f"/api/v1/volumes/{volume_id}/chapters", json={"title": "拾荒少年的清晨"})).json()["data"]
    ch2 = (await client.post(f"/api/v1/volumes/{volume_id}/chapters", json={"title": "废铁镇的钟声"})).json()["data"]
    vol2 = (await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": "星港夜航"})).json()["data"]
    ch3 = (await client.post(f"/api/v1/volumes/{vol2['id']}/chapters", json={"title": "第一艘离港的船"})).json()["data"]
    return vol2["id"], {"拾荒少年的清晨": ch1, "废铁镇的钟声": ch2, "第一艘离港的船": ch3}


@pytest.mark.asyncio
async def test_outline_save_reports_diff_without_reordering(client, novel_id, volume_id):
    """★v1029：调换大纲顺序 → 只产出差异报告，卷章 sort_order **不**被改写。"""
    vol2_id, chs = await _build_three_chapters(client, novel_id, volume_id)

    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": OUTLINE_V1})
    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    v1 = next(v for v in vols if v["id"] == volume_id)
    assert [c["sort_order"] for c in v1["chapters"]] == [1, 2]

    # 调换大纲顺序：报告差异，但结构不动
    resp = await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": OUTLINE_REORDERED})
    assert resp.status_code == 200
    sync = resp.json()["data"]["order_sync"]
    assert sync["read_only"] is True
    assert sync["volume_order"] == ["星港夜航", "荒原初醒"], "差异报告按大纲小节先后给出"
    assert sync["volumes_updated"] == 2, "两卷顺序与大纲不同 → 报告 2 处差异"

    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    # ★卷层面顺序保持原样（大纲不再回填）
    assert {v["title"]: v["sort_order"] for v in vols} == {"荒原初醒": 1, "星港夜航": 2}
    # ★卷1 内章顺序也保持原样
    v1 = next(v for v in vols if v["id"] == volume_id)
    order = {c["title"]: c["sort_order"] for c in v1["chapters"]}
    assert order == {"拾荒少年的清晨": 1, "废铁镇的钟声": 2}


@pytest.mark.asyncio
async def test_sync_order_does_not_change_ids_or_filenames(client, novel_id, volume_id):
    """★顺序回填只改 sort_order：ID 与文件名不变。"""

    vol2_id, chs = await _build_three_chapters(client, novel_id, volume_id)
    ws = workspace_of(novel_id)
    files_before = sorted(p.name for p in ws.rglob("c*.md"))
    ids_before = {t: c["id"] for t, c in chs.items()}

    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": OUTLINE_REORDERED})

    files_after = sorted(p.name for p in ws.rglob("c*.md"))
    assert files_before == files_after, "顺序回填不得改变/移动章节文件"

    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    ids_after = {}
    for v in vols:
        for c in v["chapters"]:
            ids_after[c["title"]] = c["id"]
    assert ids_after == ids_before, "顺序回填不得改变章 ID"


@pytest.mark.asyncio
async def test_sync_order_no_snapshot_no_version_bump(client, novel_id, volume_id):
    """★顺序回填不产生修订快照、不递增 version（顺序是元数据非正文）。"""
    vol2_id, chs = await _build_three_chapters(client, novel_id, volume_id)
    ch1 = chs["拾荒少年的清晨"]

    # 写一次正文，确立 version=2 且有 1 个快照
    await client.put(
        f"/api/v1/chapters/{ch1['id']}/content", json={"content": "正文A", "base_version": 1}
    )
    before = (await client.get(f"/api/v1/chapters/{ch1['id']}/content")).json()["data"]
    revs_before = (await client.get(f"/api/v1/chapters/{ch1['id']}/revisions")).json()["data"]

    # 触发顺序回填
    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": OUTLINE_REORDERED})

    after = (await client.get(f"/api/v1/chapters/{ch1['id']}/content")).json()["data"]
    revs_after = (await client.get(f"/api/v1/chapters/{ch1['id']}/revisions")).json()["data"]

    assert after["version"] == before["version"], "顺序回填不得递增 version"
    assert after["content"] == before["content"], "顺序回填不得改正文"
    assert len(revs_after) == len(revs_before), "顺序回填不得产生修订快照"


@pytest.mark.asyncio
async def test_sync_order_unregistered_appended_with_warning(client, novel_id, volume_id):
    """大纲未登记的卷/章追加到末尾并出警告，不阻断保存。"""
    vol2_id, chs = await _build_three_chapters(client, novel_id, volume_id)

    # 大纲只登记卷1的一章，其余全部未登记
    partial = "# 第一卷 荒原初醒\n## 第二章 废铁镇的钟声\n- 简介：只登记这一章。\n"
    resp = await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": partial})
    assert resp.status_code == 200
    sync = resp.json()["data"]["order_sync"]
    assert any("未在大纲中登记" in w for w in sync["warnings"])

    # 未登记的卷「星港夜航」仍在，追加到末尾
    vols = (await client.get(f"/api/v1/novels/{novel_id}/volumes")).json()["data"]
    titles = {v["title"]: v["sort_order"] for v in vols}
    assert titles["荒原初醒"] == 1
    assert titles["星港夜航"] == 2


@pytest.mark.asyncio
async def test_validate_outline_reports_issues(client, novel_id, volume_id):
    """校验：归一化一致性、缺失、序数与顺序冲突、未登记项、错误行号。"""
    await _build_three_chapters(client, novel_id, volume_id)
    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": OUTLINE_V1})

    result = (await client.get(f"/api/v1/novels/{novel_id}/outline/validate")).json()["data"]
    assert result["ok"] is True
    assert result["volume_count"] == 2
    assert result["chapter_count"] == 3


@pytest.mark.asyncio
async def test_validate_detects_missing_chapter_section(client, novel_id, volume_id):
    """大纲缺某章的小节 → missing 清单记录。"""
    await _build_three_chapters(client, novel_id, volume_id)
    partial = "# 第一卷 荒原初醒\n## 第一章 拾荒少年的清晨\n- 简介：只有一章。\n"
    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": partial})

    result = (await client.get(f"/api/v1/novels/{novel_id}/outline/validate")).json()["data"]
    # 章节文件存在但大纲缺小节 → 归为 unregistered（未在大纲登记）
    assert result["unregistered"], "大纲未登记的卷/章应出现在 unregistered"

    unreg_vols = {u["title"] for u in result["unregistered"] if u["kind"] == "volume"}
    unreg_chs = {u.get("title") for u in result["unregistered"] if u["kind"] == "chapter"}

    # 已登记的「拾荒少年的清晨」不应报未登记
    assert "拾荒少年的清晨" not in unreg_chs
    # 卷1 内未登记的章逐个报出
    assert "废铁镇的钟声" in unreg_chs
    # 整个卷2 未在大纲登记 → 报卷级未登记（不再重复报其下的章）
    assert "星港夜航" in unreg_vols


@pytest.mark.asyncio
async def test_validate_empty_intro_flagged(client, novel_id, volume_id):
    await client.post(f"/api/v1/volumes/{volume_id}/chapters", json={"title": "拾荒少年的清晨"})
    text = "# 第一卷 荒原初醒\n## 第一章 拾荒少年的清晨\n"
    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": text})
    result = (await client.get(f"/api/v1/novels/{novel_id}/outline/validate")).json()["data"]
    assert any(m["kind"] == "chapter_intro" for m in result["missing"])


@pytest.mark.asyncio
async def test_outline_read_write_roundtrip(client, novel_id):
    got = (await client.get(f"/api/v1/novels/{novel_id}/outline")).json()["data"]
    assert got["filename"] == "星海拾荒人-大纲.md"
    assert "# 附录A 世界观" in got["content"], "创建时应已写入模板"

    new_content = "# 第一卷 新卷\n## 第一章 新章\n- 简介：新内容。\n"
    resp = await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": new_content})
    assert resp.status_code == 200
    data = resp.json()["data"]
    # ★v1029：大纲是参考资料，保存不再回填卷章顺序，只给只读差异报告
    assert data["order_synced"] is False
    assert data["order_sync"]["read_only"] is True

    got = (await client.get(f"/api/v1/novels/{novel_id}/outline")).json()["data"]
    assert got["content"] == new_content


@pytest.mark.asyncio
async def test_outline_saved_to_correct_file(client, novel_id):
    """大纲写到 outline_file 指向的文件（唯一寻址依据）。"""

    await client.put(
        f"/api/v1/novels/{novel_id}/outline", json={"content": "# 卷一 测试\n## 章一\n- 简介：x\n"}
    )
    ws = workspace_of(novel_id)
    outline = ws / "outline" / "星海拾荒人-大纲.md"
    assert outline.is_file()
    assert "# 卷一 测试" in outline.read_text(encoding="utf-8")
