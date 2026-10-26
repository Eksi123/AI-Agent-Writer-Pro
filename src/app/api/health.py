"""健康检查与运行期自检（FTS5 探测、calibre 探测）。"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.core.config import get_settings
from app.core.paths import data_root
from app.core.response import ok
from app.db.fts import probe_fts5
from app.services.exporters import calibre_available

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """服务自检。FTS5 不可用不视为故障（自动降级 LIKE）。"""
    fts = probe_fts5()
    return ok(
        {
            "status": "ok",
            "version": __version__,
            "fts5_available": fts.available,
            "fts5_reason": fts.reason,
            "sqlite_version": fts.sqlite_version,
            "calibre_available": calibre_available(),
            "langfuse_enabled": get_settings().langfuse_enabled,
            "data_root": str(data_root()),
        }
    )
