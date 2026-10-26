"""FTS5：两步可用性探测 + 中文二元切分 + `LIKE` 降级（手册「四、检索」）。

实测结论（SQLite 3.53.4 / Python 3.12.14）：FTS5 可用，`unicode61`/`trigram`/`porter`
均可建表，`bm25()` 可用；`trigram` 对 2 字词零命中，故中文主方案是**手工二元切分 + unicode61**。

★查询侧必须用同一个 `bigram_tokenize()`，否则永远查不到。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: `story_fts` 建表语句。`project_id` 必须入表且 UNINDEXED：查询时附加过滤，否则跨项目串数据。
CREATE_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS story_fts USING fts5(
    doc_ref UNINDEXED,
    project_id UNINDEXED,
    kind UNINDEXED,
    title,
    content,
    tokenize='unicode61'
)
"""

#: 非中日韩字符（拉丁词、数字）保持原样，交给 unicode61 分词
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def bigram_tokenize(text: str) -> str:
    """把中文切成空格分隔的二字词；非 CJK 片段原样保留。

    `少年走出山村` → `少年 年走 走出 出山 山村`（实测该方案可命中并有 bm25 区分度）。
    单字 CJK（无相邻汉字）时保留单字，避免查询侧完全落空。
    """
    if not text:
        return ""
    out: list[str] = []
    cjk_run: list[str] = []
    ascii_run: list[str] = []

    def flush_cjk() -> None:
        if not cjk_run:
            return
        if len(cjk_run) == 1:
            out.append(cjk_run[0])
        else:
            out.extend("".join(pair) for pair in zip(cjk_run, cjk_run[1:], strict=False))
        cjk_run.clear()

    def flush_ascii() -> None:
        if not ascii_run:
            return
        token = "".join(ascii_run).strip()
        if token:
            out.append(token)
        ascii_run.clear()

    for ch in text:
        if _CJK.match(ch):
            flush_ascii()
            cjk_run.append(ch)
        elif ch.isspace() or ch in "，。、；：？！「」『』（）《》…—·,.;:?!\"'()[]<>":
            flush_cjk()
            flush_ascii()
        else:
            flush_cjk()
            ascii_run.append(ch)

    flush_cjk()
    flush_ascii()
    return " ".join(out)


@dataclass(frozen=True)
class FtsStatus:
    """FTS5 可用性探测结果（进程内缓存，不重复探测）。"""

    available: bool
    reason: str = ""
    sqlite_version: str = ""


@lru_cache(maxsize=1)
def probe_fts5() -> FtsStatus:
    """两步探测：① `SELECT fts5(NULL)` 判断函数存在；② 建内存表验证 tokenizer 与 `bm25()`。

    任一步抛 `sqlite3.OperationalError` 即永久降级 `LIKE`。
    """
    version = ".".join(str(v) for v in sqlite3.sqlite_version_info)
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(":memory:")
        # ① 函数是否存在（未编译 FTS5 时报 no such function: fts5）
        conn.execute("SELECT fts5(NULL)")
        # ② 建表验证 unicode61 + bm25
        conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x, tokenize='unicode61')")
        conn.execute("INSERT INTO _probe(x) VALUES (?)", (bigram_tokenize("少年走出山村"),))
        rows = conn.execute(
            "SELECT bm25(_probe) FROM _probe WHERE _probe MATCH ?", (bigram_tokenize("少年"),)
        ).fetchall()
        if not rows:
            return FtsStatus(False, "unicode61 建表成功但二元切分未命中", version)
        return FtsStatus(True, "ok", version)
    except sqlite3.Error as exc:  # OperationalError 及其它 sqlite 错误
        return FtsStatus(False, f"{type(exc).__name__}: {exc}", version)
    finally:
        if conn is not None:
            conn.close()


def fts_available() -> bool:
    return probe_fts5().available


async def ensure_fts_schema(conn: Any) -> bool:
    """在业务库中建 `story_fts`（幂等）。返回是否 FTS 可用。

    `conn` 为 aiosqlite / SQLAlchemy async connection 底层的 sqlite3 连接或可执行对象。
    """
    if not fts_available():
        logger.warning("fts5_unavailable", reason=probe_fts5().reason)
        return False
    try:
        await _execute(conn, CREATE_FTS_SQL)
        await _execute(
            conn,
            "CREATE TABLE IF NOT EXISTS story_fts_meta (k TEXT PRIMARY KEY, v TEXT)",
        )
        return True
    except sqlite3.Error as exc:
        logger.error("fts5_schema_failed", error=str(exc))
        return False


async def _execute(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    """兼容 SQLAlchemy AsyncConnection 与裸 aiosqlite 连接。"""
    if hasattr(conn, "execute") and hasattr(conn, "commit"):  # SQLAlchemy AsyncConnection
        from sqlalchemy import text

        return await conn.execute(text(sql), params)
    return await conn.execute(sql, params)  # aiosqlite
