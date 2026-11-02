# -*- coding: utf-8 -*-
"""
模块: app.agent.tools.builtin.web_search
文件名: web_search.py
作者: xionggaoqing
时间: 2026-10-27
说明: `web_search` 内置工具实现（手册「二、(3) 联网类」）。

    - 免 Key 免费源：**Wikipedia REST 搜索 API**（主）+ **DuckDuckGo lite HTML**（备）；
    - 源不可用 / 无结果 → 返回**明确错误文案**，绝不静默返回空；
    - 只读网络请求，带超时；工具装配见上级 `tools/registry.py`。
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import unquote

import httpx
from langchain_core.tools import tool

from app.core.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = 12.0

_WIKI_SEARCH = "https://zh.wikipedia.org/w/api.php"
_DDG_LITE = "https://lite.duckduckgo.com/lite/"
_UA = "Mozilla/5.0 (compatible; AIWriterPro/0.1; +local-novel-writing-tool)"

_TAG = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return html.unescape(_TAG.sub(" ", text or "")).strip()


async def _search_wikipedia(client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
    """Wikipedia 搜索（中文站），返回标题 + 摘要 + 链接。"""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "format": "json",
        "utf8": 1,
    }
    resp = await client.get(_WIKI_SEARCH, params=params)
    resp.raise_for_status()
    payload = resp.json()
    items = []
    for hit in (payload.get("query") or {}).get("search") or []:
        title = hit.get("title") or ""
        items.append(
            {
                "title": title,
                "snippet": _clean(hit.get("snippet") or "")[:600],
                "url": f"https://zh.wikipedia.org/wiki/{title.replace(' ', '_')}",
                "source": "wikipedia",
            }
        )
    return items


async def _search_duckduckgo(client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
    """DuckDuckGo lite（HTML 解析，无 Key）。"""
    resp = await client.post(_DDG_LITE, data={"q": query})
    resp.raise_for_status()
    text = resp.text
    links = re.findall(r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.S)
    snippets = re.findall(r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>', text, re.S)
    items: list[dict[str, Any]] = []
    for idx, (url, title) in enumerate(links[:limit]):
        clean_url = html.unescape(url)
        m = re.search(r"uddg=([^&]+)", clean_url)
        if m:
            clean_url = unquote(m.group(1))
        snippet = _clean(snippets[idx]) if idx < len(snippets) else ""
        items.append(
            {
                "title": _clean(title)[:200],
                "snippet": snippet[:600],
                "url": clean_url,
                "source": "duckduckgo",
            }
        )
    return items


async def run_search(query: str, limit: int = 5) -> dict[str, Any]:
    """依次尝试各源；全部失败抛 RuntimeError（带每个源的具体原因）。"""
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True) as client:
        for name, fn in (("wikipedia", _search_wikipedia), ("duckduckgo", _search_duckduckgo)):
            try:
                items = await fn(client, query, limit)
                if items:
                    return {"query": query, "source": name, "items": items, "count": len(items)}
                errors.append(f"{name}: 无匹配结果")
            except httpx.HTTPError as exc:
                errors.append(f"{name}: {type(exc).__name__}")
                logger.warning("web_search_source_failed", source=name, error=str(exc))
            except (ValueError, KeyError) as exc:
                errors.append(f"{name}: 响应解析失败")
                logger.warning("web_search_parse_failed", source=name, error=str(exc))
    raise RuntimeError("联网搜索不可用：" + "；".join(errors))


@tool("web_search", parse_docstring=True)
async def web_search(query: str, limit: int = 5) -> str:
    """搜索互联网获取外部资料（免 Key 免费源：Wikipedia / DuckDuckGo）。

    适用于查证真实世界的知识、术语、历史背景、地理与文化细节。
    不要用它搜索本项目的小说内容——项目内容请用 grep / read_file / glob。

    Args:
        query: 搜索关键词（中文或英文均可，建议具体一些）。
        limit: 返回结果条数上限（1-10，默认 5）。

    Returns:
        JSON 字符串 `{query, source, count, items:[{title, snippet, url, source}]}`；
        所有搜索源不可用时返回 `{error: "..."}` 并说明原因。
    """
    limit = max(1, min(int(limit or 5), 10))
    query = (query or "").strip()
    if not query:
        return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)
    try:
        data = await run_search(query, limit)
        return json.dumps(data, ensure_ascii=False)
    except Exception as exc:  # 兜底：任何异常都给明确错误，绝不静默空结果
        logger.error("web_search_failed", query=query, error=str(exc))
        message = str(exc) if isinstance(exc, RuntimeError) else f"联网搜索失败：{type(exc).__name__}"
        return json.dumps({"error": message}, ensure_ascii=False)
