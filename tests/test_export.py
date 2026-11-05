# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_export.py
作者: xionggaoqing
时间: 2026-10-27
说明: 导出 zip 与 EPUB 结构（手册「十一 · EPUB / 导出」域）。

    必测：
    - `mimetype` 为 zip **首条目且 STORE 不压缩**；
    - `META-INF/container.xml`、`OEBPS/content.opf`、`nav.xhtml` 存在且 XML 合法；
    - 章节顺序与卷章树（按 `sort_order`）一致；txt 拼接顺序正确；
    - zip 内含正文（按 format）+ 大纲原 md；
    - MOBI/calibre 已移除；PDF 由 reportlab 生成（依赖缺失时任务 failed 并报 50301 文案）。
"""

from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.paths import exports_root
from app.db.models import AuditLog
from app.db.session import get_sessionmaker

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
        # 章节 md 集合（两层目录结构：小说名/卷名/章节.md）
        chapter_files = [n for n in names if n.endswith(".md") and not n.endswith("-大纲.md") and "/" in n and "目录.md" not in n and "简介.md" not in n]
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
    """EPUB：`mimetype` 必须是 zip **首条目**且 **STORE 不压缩**。"""
    novel_id = book["novel_id"]
    path = await _run_export(client, novel_id, "epub")

    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        assert infos[0].filename == "mimetype", "mimetype 必须是首条目"
        assert infos[0].compress_type == zipfile.ZIP_STORED, "mimetype 必须 STORE 不压缩"
        assert zf.read("mimetype") == b"application/epub+zip"


@pytest.mark.asyncio
async def test_export_epub_required_files_valid_xml(client, book):
    """EPUB：container.xml / content.opf / nav.xhtml 存在且 XML 合法。"""
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
    """EPUB spine 顺序与卷章树（按 sort_order）一致。"""
    novel_id = book["novel_id"]
    path = await _run_export(client, novel_id, "epub")

    with zipfile.ZipFile(path) as zf:
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")

    # spine 中章节 itemref 的顺序

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
    """mobi 已移除 → 400 拒绝。"""
    resp = await client.post(f"/api/v1/novels/{book['novel_id']}/export", params={"format": "mobi"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_export_pdf(client, book):
    """PDF 导出——reportlab 可用时 zip 内含 .pdf；缺失时任务 failed 且提示安装依赖。"""
    import importlib.util

    has_reportlab = importlib.util.find_spec("reportlab") is not None
    novel_id = book["novel_id"]
    resp = await client.post(f"/api/v1/novels/{novel_id}/export", params={"format": "pdf"})
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task_id"]
    done = await _wait_task(client, novel_id, task_id)
    if has_reportlab:
        assert done["status"] == "done", done
        with zipfile.ZipFile(exports_root() / done["filename"]) as zf:
            assert [n for n in zf.namelist() if n.endswith(".pdf")], "zip 内应含 PDF 产物"
    else:
        assert done["status"] == "failed"
        assert "reportlab" in (done["error"] or "")


async def _wait_task(client, novel_id: str, task_id: str) -> dict:
    for _ in range(60):
        data = (await client.get(f"/api/v1/novels/{novel_id}/export-tasks/{task_id}")).json()["data"]
        if data["status"] in ("done", "failed"):
            return data
        await asyncio.sleep(0.2)
    raise AssertionError("导出任务超时未完成")


@pytest.mark.asyncio
async def test_export_task_not_found(client, book):
    resp = await client.get(f"/api/v1/novels/{book['novel_id']}/export-tasks/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_writes_audit(client, book):
    """导出动作写审计。"""


    await _run_export(client, book["novel_id"], "txt")

    async with get_sessionmaker()() as s:
        actions = (await s.execute(select(AuditLog.action))).scalars().all()
    assert "novel.export" in actions


@pytest.mark.asyncio
async def test_pdf_detection_is_bool(client):
    """health 接口报告 PDF 依赖（reportlab）可用性（不抛异常）。"""
    data = (await client.get("/api/v1/health")).json()["data"]
    assert isinstance(data["pdf_available"], bool)
    assert data["fts5_available"] is True


# ---------------------------------------------------------------------------
# 导出预检 (from test_workflows.py)
# ---------------------------------------------------------------------------

from app.agent.workflows.novel_export.preflight import run_export_preflight


class TestExportPreflight:
    async def test_no_chapters_blocks(self, client, session, novel_id):
        result = await run_export_preflight(session, novel_id, "epub")
        assert result["passed"] is False
        assert any(c["code"] == "no_chapters" for c in result["checks"])

    async def test_empty_and_draft_warnings(self, client, session, novel_id, chapter_id):
        result = await run_export_preflight(session, novel_id, "markdown")
        assert result["passed"] is True  # 只有警告，无阻断
        codes = {c["code"] for c in result["checks"]}
        # fixture 章节正文很短（<50 字）→ short_chapters；未定稿 → draft_chapters
        assert "short_chapters" in codes or "empty_chapters" in codes

    async def test_pdf_without_reportlab_blocks(self, client, session, novel_id, chapter_id, monkeypatch):
        import app.services.exporters as exporters

        monkeypatch.setattr(exporters, "pdf_available", lambda: False)
        result = await run_export_preflight(session, novel_id, "pdf")
        assert result["passed"] is False
        assert any(c["code"] == "pdf_dependency_missing" for c in result["checks"])

    async def test_pdf_with_reportlab_passes(self, client, session, novel_id, chapter_id, monkeypatch):
        import app.services.exporters as exporters

        monkeypatch.setattr(exporters, "pdf_available", lambda: True)
        result = await run_export_preflight(session, novel_id, "pdf")
        assert not any(c["code"] == "pdf_dependency_missing" for c in result["checks"])

    async def test_api_endpoint(self, client, novel_id):
        resp = await client.get(f"/api/v1/novels/{novel_id}/export-preflight", params={"format": "epub"})
        assert resp.json()["code"] == 0
        assert "passed" in resp.json()["data"]


# ---------------------------------------------------------------------------
# 全书一致性审查 (from test_workflows.py)
# ---------------------------------------------------------------------------

from langchain_core.messages import AIMessage


class TestBookConsistencyAudit:
    async def test_skipped_without_model(self, client, session, novel_id, chapter_id):
        """有章节但未配置模型 → skipped（带 reason，不抛错）。"""
        from app.agent.workflows.book_consistency.audit import run_book_consistency_audit

        result = await run_book_consistency_audit(session, novel_id)
        assert result["status"] == "skipped"
        assert "未配置可用模型" in result["reason"]

    async def test_skipped_without_chapters(self, client, session, novel_id):
        """无章节时 skipped（章节检查先于模型解析，无需配模型）。"""
        from app.agent.workflows.book_consistency.audit import run_book_consistency_audit

        result = await run_book_consistency_audit(session, novel_id)
        assert result["status"] == "skipped"
        assert result["reason"] == "尚无章节"

    async def test_audit_with_fake_model(self, client, session, novel_id, volume_id, chapter_id):
        """假模型返回结构化问题 → 汇总排序 + 报告落盘。"""
        from fake_model import ScriptedFakeChatModel

        from app.agent.loop.llm.factory import register_model_override
        from app.agent.workflows.book_consistency.audit import run_book_consistency_audit
        from app.services.llm import create_model, create_provider

        provider = await create_provider(
            session, name="审计测试厂商2", type="openai_compatible", api_key_env=None
        )
        model = await create_model(
            session, provider_id=provider["id"], model_id="fake-audit2", is_default=True
        )
        fake = ScriptedFakeChatModel(
            responses=[
                AIMessage(
                    content=(
                        '{"issues": [{"level": "suggestion", "chapter": "拾荒少年的清晨",'
                        ' "type": "timeline", "detail": "季节描写前后不一", "advice": "统一为初春"}],'
                        ' "summary": "总体连贯"}'
                    )
                )
            ]
        )
        register_model_override(model["model_ref"], fake)

        result = await run_book_consistency_audit(session, novel_id)
        assert result["status"] == "done"
        assert result["volumes_checked"] >= 1
        assert len(result["issues"]) == 1
        assert result["issues"][0]["type"] == "timeline"

        from app.core.paths import workspace_dir

        report = workspace_dir(novel_id) / "outline" / "consistency-audit.md"
        assert report.exists()
        text = report.read_text(encoding="utf-8")
        assert "一致性审查报告" in text
        assert "季节描写前后不一" in text
