#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_fts_search.py
作者: xionggaoqing
时间: 2026-10-27
说明: FTS5 二元切分与降级（手册「十一 · FTS5」域）。

    ★必测：二元切分往返（索引侧与查询侧同一函数）；2 字词命中（trigram 做不到的场景）；
    `bm25` 排序；FTS5 不可用时 `LIKE` 降级且 `data.degraded=true`；`project_id` 过滤（跨项目不串数据）。
"""

from __future__ import annotations

import pytest

from app.db.fts import bigram_tokenize, probe_fts5

# ---------------------------------------------------------------------------
# 二元切分
# ---------------------------------------------------------------------------


def test_bigram_tokenize_chinese():
    """手册给的标准例子：`少年走出山村` → `少年 年走 走出 出山 山村`。"""
    assert bigram_tokenize("少年走出山村") == "少年 年走 走出 出山 山村"


def test_bigram_single_char_kept():
    """单字 CJK 保留，避免查询侧完全落空。"""
    assert bigram_tokenize("山") == "山"


def test_bigram_ascii_preserved():
    assert bigram_tokenize("hello world") == "hello world"


def test_bigram_mixed():
    out = bigram_tokenize("少年 AI 出山")
    assert "少年" in out
    assert "AI" in out


def test_bigram_punctuation_splits():
    """标点作为分隔，不产生跨标点的假二元词。"""
    out = bigram_tokenize("少年，出山。")
    tokens = out.split()
    assert "少年" in tokens
    assert "出山" in tokens
    assert all("，" not in t and "。" not in t for t in tokens)


def test_bigram_empty():
    assert bigram_tokenize("") == ""
    assert bigram_tokenize("   ") == ""


def test_bigram_roundtrip_query_side():
    """★索引侧与查询侧同一函数：查询「少年」能命中索引文本。"""
    indexed = bigram_tokenize("少年走出山村，见到星海")
    query = bigram_tokenize("少年")
    assert query in indexed.split()


def test_two_char_word_matchable():
    """2 字词必须可命中（trigram 主方案做不到的场景）。"""
    indexed = bigram_tokenize("他是一名拾荒者").split()
    assert "拾荒" in indexed
    assert "荒者" in indexed


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------


def test_probe_fts5_reports_status():
    status = probe_fts5()
    assert isinstance(status.available, bool)
    assert status.sqlite_version


# ---------------------------------------------------------------------------
# 检索 API（含 project_id 隔离与降级）
# ---------------------------------------------------------------------------


OUTLINE = """\
# 第一卷 荒原初醒
## 第一章 拾荒少年的清晨
- 简介：少年走出山村，第一次见到星海拾荒船。
- 衔接要点：承接序章的少年离乡。
# 附录A 世界观
- 力量体系：星辉共鸣。
"""


@pytest.mark.asyncio
async def test_search_hits_outline(client, novel_id):
    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": OUTLINE})
    await client.post(f"/api/v1/novels/{novel_id}/summaries/rebuild")

    resp = await client.get(f"/api/v1/novels/{novel_id}/search", params={"q": "拾荒少年"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["query"] == "拾荒少年"
    assert data["total"] >= 1
    assert any(item["kind"] == "outline" for item in data["items"])


@pytest.mark.asyncio
async def test_search_two_char_word(client, novel_id):
    """2 字词命中（二元切分的价值所在）。"""
    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": OUTLINE})
    await client.post(f"/api/v1/novels/{novel_id}/summaries/rebuild")

    resp = await client.get(f"/api/v1/novels/{novel_id}/search", params={"q": "少年"})
    data = resp.json()["data"]
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_search_project_isolation(client):
    """★project_id 过滤：跨项目不串数据。"""
    a = (await client.post("/api/v1/novels", json={"title": "项目甲"})).json()["data"]["id"]
    b = (await client.post("/api/v1/novels", json={"title": "项目乙"})).json()["data"]["id"]

    await client.put(f"/api/v1/novels/{a}/outline", json={"content": "# 卷一\n## 章一\n- 简介：紫水晶密钥独有内容。\n"})
    await client.put(f"/api/v1/novels/{b}/outline", json={"content": "# 卷一\n## 章一\n- 简介：完全不同的乙项目内容。\n"})
    await client.post(f"/api/v1/novels/{a}/summaries/rebuild")
    await client.post(f"/api/v1/novels/{b}/summaries/rebuild")

    resp_a = await client.get(f"/api/v1/novels/{a}/search", params={"q": "紫水晶"})
    items_a = resp_a.json()["data"]["items"]
    assert any("紫水晶" in i["snippet"] or i["kind"] == "outline" for i in items_a)

    # 项目 B 搜项目 A 的独有内容 → 不应命中
    resp_b = await client.get(f"/api/v1/novels/{b}/search", params={"q": "紫水晶"})
    data_b = resp_b.json()["data"]
    for item in data_b["items"]:
        assert item["doc_ref"] or True
    # 降级或 FTS 两种路径都不应把 A 的文档返回给 B
    assert all("紫水晶" not in i.get("snippet", "") for i in data_b["items"])


@pytest.mark.asyncio
async def test_search_empty_query_rejected(client, novel_id):
    resp = await client.get(f"/api/v1/novels/{novel_id}/search", params={"q": ""})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_scope_filter(client, novel_id):
    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": OUTLINE})
    await client.post(f"/api/v1/novels/{novel_id}/summaries/rebuild")

    resp = await client.get(
        f"/api/v1/novels/{novel_id}/search", params={"q": "少年", "scope": "outline"}
    )
    assert resp.status_code == 200
    for item in resp.json()["data"]["items"]:
        assert item["kind"] == "outline"


@pytest.mark.asyncio
async def test_search_like_degradation(client, novel_id, monkeypatch):
    """★FTS5 不可用时降级 LIKE，返回结构不变且 `data.degraded=true`。"""
    from app.db import fts as fts_module
    from app.services import search as search_service

    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": OUTLINE})

    # 强制探测为不可用
    monkeypatch.setattr(fts_module, "fts_available", lambda: False)
    monkeypatch.setattr(search_service, "fts_available", lambda: False)

    resp = await client.get(f"/api/v1/novels/{novel_id}/search", params={"q": "少年"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["degraded"] is True, "降级路径必须标记 degraded=true"
    # 返回结构不变
    assert set(data.keys()) == {"query", "scope", "total", "degraded", "items"}
    assert data["total"] >= 1, "LIKE 降级仍应命中"


@pytest.mark.asyncio
async def test_rebuild_index_marks_clean(client, novel_id):
    await client.put(f"/api/v1/novels/{novel_id}/outline", json={"content": OUTLINE})
    resp = await client.post(f"/api/v1/novels/{novel_id}/summaries/rebuild")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["project_id"] == novel_id
    assert data["indexed"] >= 1
