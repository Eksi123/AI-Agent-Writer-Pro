#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_export.py
作者: xionggaoqing
时间: 2026-10-27
说明: 导出 zip 与 EPUB 结构（手册「十一 · EPUB / 导出」域）。

    ★必测：
    - `mimetype` 为 zip **首条目且 STORE 不压缩**；
    - `META-INF/container.xml`、`OEBPS/content.opf`、`nav.xhtml` 存在且 XML 合法；
    - 章节顺序与卷章树（按 `sort_order`）一致；txt 拼接顺序正确；
    - zip 内含正文（按 format）+ 大纲原 md；
    - calibre 未安装时 mobi 返回 503 / `50301`。
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

OUTLINE = """\
# 第一卷 荒原初醒
## 第一章 拾荒少年的清晨
- 简介：少年出山。
## 第二章 废铁镇的钟声
- 简介：进镇。
# 第二卷 星港夜航
## 第三章 第一艘离港的船
- 简介：登船。
"""


@pytest.fixture
async def book(client, novel_id):
    """构造一本 2 卷 3 章的书，并把顺序设为与大纲一致。"""
    v1 = (await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": "荒原初醒"})).json()["data"]
    c1 = (await client.post(f"/api/v1/volumes/{v1['id']}/chapters", json={"title": "拾荒少年的清晨", "content": "少年走出山村。"})).json()["data"]
    c2 = (await client.post(f"/api/v1/volumes/{v1['id']}/chapters", json={"title": "废铁镇的钟声", "content": "钟声响起在废铁镇。"})).json()["data"]
    v2 = (await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": "星港夜航"})).json()["data"]
    c3 = (await client.post(f"/api/v1/volumes/{v2['id']}/chapters", json={"title": "第一艘离港的船", "content": "船离开星港。"})).json()["data"]

    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": OUTLINE})
    return {"novel_id": novel_id, "v1": v1["id"], "v2": v2["id"], "chapters": [c1, c2, c3]}


async def _run_export(client, novel_id: str, fmt: str) -> Path:
    """触发导出并等待任务完成，返回 zip 路径。"""
    import asyncio

    resp = await client.post(f"/api/v1/novels/{novel_id}/export", params={"format": fmt})
    assert resp.status_code == 200, resp.text
    task = resp.json()["data"]
    task_id = task["task_id"]

    # BackgroundTasks 在 ASGITransport 下同步执行完毕；轮询确认
    for _ in range(50):
        status = (await client.get(f"/api/v1/novels/{novel_id}/export-tasks/{task_id}")).json()["data"]
        if status["status"] in ("done", "failed"):
            break
        await asyncio.sleep(0.05)
    assert status["status"] == "done", f"导出失败：{status}"

    from app.core.paths import exports_root

    path = exports_root() / status["filename"]
    assert path.is_file(), f"导出产物不存在：{path}"
    return path


@pytest.mark.asyncio
async def test_export_markdown_zip_contents(client, book):
    novel_id = book["novel_id"]
    path = await _run_export(client, novel_id, "markdown")

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        # 大纲原件
        assert any(n.endswith("-大纲.md") for n in names), "zip 内必须含大纲原 md"
        # manifest
        assert "export-manifest.json" in names
        # 章节 md 集合
        chapter_files = [n for n in names if n.startswith("正文/") and n.endswith(".md")]
        assert len(chapter_files) >= 3

        manifest = json.loads(zf.read("export-manifest.json"))
        assert manifest["format"] == "markdown"
        assert manifest["chapter_count"] == 3
        assert manifest["volume_count"] == 2
        assert manifest["includes_outline"] is True


@pytest.mark.asyncio
async def test_export_txt_concatenation_order(client, book):
    """txt：卷章标题 + 正文按顺序连接。"""
    novel_id = book["novel_id"]
    path = await _run_export(client, novel_id, "txt")

    with zipfile.ZipFile(path) as zf:
        txt_name = next(n for n in zf.namelist() if n.endswith(".txt"))
        text = zf.read(txt_name).decode("utf-8")

    assert "星海拾荒人" in text
    # 顺序：卷1 → 章1 → 章2 → 卷2 → 章3
    i_v1 = text.index("荒原初醒")
    i_c1 = text.index("拾荒少年的清晨")
    i_c2 = text.index("废铁镇的钟声")
    i_v2 = text.index("星港夜航")
    i_c3 = text.index("第一艘离港的船")
    assert i_v1 < i_c1 < i_c2 < i_v2 < i_c3, "txt 拼接顺序必须与卷章树一致"
    assert "少年走出山村。" in text


@pytest.mark.asyncio
async def test_export_epub_mimetype_first_and_stored(client, book):
    """★EPUB：`mimetype` 必须是 zip **首条目**且 **STORE 不压缩**。"""
    novel_id = book["novel_id"]
    path = await _run_export(client, novel_id, "epub")

    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        assert infos[0].filename == "mimetype", "mimetype 必须是首条目"
        assert infos[0].compress_type == zipfile.ZIP_STORED, "mimetype 必须 STORE 不压缩"
        assert zf.read("mimetype") == b"application/epub+zip"


@pytest.mark.asyncio
async def test_export_epub_required_files_valid_xml(client, book):
    """★EPUB：container.xml / content.opf / nav.xhtml 存在且 XML 合法。"""
    novel_id = book["novel_id"]
    path = await _run_export(client, novel_id, "epub")

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        for required in ("META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml"):
            assert required in names, f"缺少 EPUB 必需文件：{required}"
            # XML 合法性
            try:
                ET.fromstring(zf.read(required))
            except ET.ParseError as exc:
                pytest.fail(f"{required} 不是合法 XML：{exc}")

        # container.xml 指向 content.opf
        container = zf.read("META-INF/container.xml").decode("utf-8")
        assert "OEBPS/content.opf" in container

        # content.opf 声明 version 3.0 且 manifest/spine 非空
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert 'version="3.0"' in opf
        assert "<manifest>" in opf and "<spine" in opf
        assert opf.count("<item ") >= 3
        assert opf.count("<itemref ") >= 3


@pytest.mark.asyncio
async def test_export_epub_chapter_order_matches_tree(client, book):
    """★EPUB spine 顺序与卷章树（按 sort_order）一致。"""
    novel_id = book["novel_id"]
    path = await _run_export(client, novel_id, "epub")

    with zipfile.ZipFile(path) as zf:
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")

    # spine 中章节 itemref 的顺序
    import re

    spine_ids = re.findall(r'<itemref idref="(ch\d+)"/>', opf)
    assert len(spine_ids) == 3

    # 从 manifest 取 id → href，读出各章标题在 nav 中的顺序
    nav_titles = re.findall(r"<a href=\"ch\d+\.xhtml\">([^<]+)</a>", nav)
    assert nav_titles == ["拾荒少年的清晨", "废铁镇的钟声", "第一艘离港的船"], (
        f"EPUB 目录顺序应与卷章树一致，实际 {nav_titles}"
    )


@pytest.mark.asyncio
async def test_export_epub_chapter_content_present(client, book):
    novel_id = book["novel_id"]
    path = await _run_export(client, novel_id, "epub")

    with zipfile.ZipFile(path) as zf:
        chapter_files = [n for n in zf.namelist() if n.startswith("OEBPS/ch") and n.endswith(".xhtml")]
        assert len(chapter_files) == 3
        bodies = "".join(zf.read(n).decode("utf-8") for n in chapter_files)
        assert "少年走出山村。" in bodies
        assert "钟声响起在废铁镇。" in bodies


@pytest.mark.asyncio
async def test_export_includes_outline_original(client, book):
    """所有格式都附带大纲原 md（不转换格式）。"""
    novel_id = book["novel_id"]
    for fmt in ("markdown", "txt", "epub"):
        path = await _run_export(client, novel_id, fmt)
        with zipfile.ZipFile(path) as zf:
            outline_names = [n for n in zf.namelist() if n.endswith("-大纲.md")]
            assert outline_names, f"{fmt} 导出缺少大纲原件"
            content = zf.read(outline_names[0]).decode("utf-8")
            assert content == OUTLINE, "大纲应为原 markdown，不做转换"


@pytest.mark.asyncio
async def test_export_invalid_format_rejected(client, book):
    resp = await client.post(f"/api/v1/novels/{book['novel_id']}/export", params={"format": "pdf"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_export_mobi_without_calibre_503(client, book, monkeypatch):
    """★calibre 未安装 → 受理阶段即返回 503 / `50301`（前端据此置灰该选项）。"""
    from app.api import novels as novels_api

    monkeypatch.setattr(novels_api, "calibre_available", lambda: False)

    resp = await client.post(f"/api/v1/novels/{book['novel_id']}/export", params={"format": "mobi"})
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["code"] == 50301
    assert "calibre" in body["message"].lower()


@pytest.mark.asyncio
async def test_export_task_not_found(client, book):
    resp = await client.get(f"/api/v1/novels/{book['novel_id']}/export-tasks/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_writes_audit(client, book):
    """导出动作写审计。"""
    from sqlalchemy import select

    from app.db.models import AuditLog
    from app.db.session import get_sessionmaker

    await _run_export(client, book["novel_id"], "txt")

    async with get_sessionmaker()() as s:
        actions = (await s.execute(select(AuditLog.action))).scalars().all()
    assert "novel.export" in actions


@pytest.mark.asyncio
async def test_calibre_detection_is_bool(client):
    """health 接口报告 calibre 可用性（不抛异常）。"""
    data = (await client.get("/api/v1/health")).json()["data"]
    assert isinstance(data["calibre_available"], bool)
    assert data["fts5_available"] is True
