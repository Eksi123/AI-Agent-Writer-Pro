# -*- coding: utf-8 -*-
"""
模块: app.schemas.novel
文件名: novel.py
作者: xionggaoqing
时间: 2026-10-27
说明: 项目 / 卷章 / 大纲 DTO（手册「六 → 路由树 · 项目/卷章/大纲」）。

    ★卷章无 reorder / move 的 DTO：v1029 起位置编辑走「插入」（`ordinal`），仍无拖拽重排。
    ★`ContentIn` 必带 `base_version`（乐观锁，不符 409/40901）。

    响应统一由 `core.response.ok()` 包成 `{code,message,data}`，此处只定义 `data` 部分。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# 项目（novels）
# ---------------------------------------------------------------------------


class NovelCreate(BaseModel):
    """新建小说：书名必填（显式命名，违规 42201）。"""

    title: str = Field(max_length=200, description="书名，必填；空名/默认名/纯序号名由服务层返回 42201")
    author_name: str | None = Field(default=None, max_length=100)
    #: v1030续：题材支持自定义标签（最多 3 个以「、」连接，自定义名 ≤5 字），50 足够
    genre: str | None = Field(default=None, max_length=50)
    synopsis: str | None = None


class NovelUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    author_name: str | None = Field(default=None, max_length=100)
    #: v1030续：同 NovelCreate，容纳自定义题材
    genre: str | None = Field(default=None, max_length=50)
    synopsis: str | None = None


class NovelDuplicate(BaseModel):
    title: str = Field(max_length=200, description="副本的新书名（显式命名）")


class NovelOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    author_name: str | None = None
    genre: str | None = None
    synopsis: str | None = None
    status: str = "active"
    outline_file: str
    chapter_count: int = 0
    total_words: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class ReadingProgressIn(BaseModel):
    chapter_id: str | None = None
    scroll_ratio: float = Field(default=0.0, ge=0.0, le=1.0)


class ReadingProgressOut(BaseModel):
    novel_id: str
    chapter_id: str | None = None
    scroll_ratio: float = 0.0
    updated_at: str | None = None


# ---------------------------------------------------------------------------
# 卷 / 章
# ---------------------------------------------------------------------------


class VolumeCreate(BaseModel):
    title: str = Field(max_length=200)
    summary: str = ""
    #: v1029：插入位置（1..N+1，正整数）；不传则追加到末尾
    ordinal: int | None = Field(default=None, ge=1, description="插入位置序号；缺省追加末尾")
    #: v1030续：插入卷时同时创建它的第一章（★不允许产生空卷）
    first_chapter_title: str | None = Field(
        default=None, max_length=200, description="第一章章节名；给出时随卷一并创建（避免空卷）"
    )


class VolumeUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = None


class VolumeOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    summary: str = ""
    sort_order: int = 0
    chapters: list[ChapterOut] = Field(default_factory=list)
    chapter_count: int = 0
    word_count: int = 0


class ChapterCreate(BaseModel):
    title: str = Field(max_length=200)
    content: str = ""
    #: v1029：插入位置（1..N+1，正整数）；不传则追加到卷末尾
    ordinal: int | None = Field(default=None, ge=1, description="插入位置序号；缺省追加末尾")


class ChapterUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    status: Literal["draft", "finalized"] | None = None
    #: v1029：重命名弹窗同时给出卷名（默认不变）；变更时重命名该章所属的卷
    volume_title: str | None = Field(default=None, max_length=200)


class ChapterOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    volume_id: str
    title: str
    sort_order: int = 0
    word_count: int = 0
    status: str = "draft"
    version: int = 1
    updated_at: str | None = None


class ContentIn(BaseModel):
    """正文写入请求体。

    `base_version` 为**必填**（乐观锁）：读取时拿到的 `version`，不符返回 409/40901。
    `autosave=true` 时不产生修订快照（防版本爆炸）。
    """

    content: str = ""
    base_version: int = Field(ge=1, description="读取时拿到的版本号")
    autosave: bool = False


class ContentOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    volume_id: str
    title: str | None = None
    content: str = ""
    word_count: int = 0
    status: str = "draft"
    version: int = 1
    updated_at: str | None = None
    last_writer: str = "manual"


class ContentWriteResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    volume_id: str
    version: int
    word_count: int
    updated_at: str | None = None
    last_writer: str = "manual"
    snapshotted: bool = True


class RevisionOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    chapter_id: str
    project_id: str
    volume_id: str
    version: int
    word_count: int
    source: str
    run_id: str | None = None
    created_at: str | None = None
    preview: str = ""


# ---------------------------------------------------------------------------
# 大纲
# ---------------------------------------------------------------------------


class OutlineIn(BaseModel):
    content: str = Field(description="大纲 markdown 全文")


class OutlineOut(BaseModel):
    novel_id: str
    path: str
    filename: str
    content: str
    word_count: int = 0
    #: v1029：只读差异报告（大纲不再回填 sort_order）；read_only=True 标记
    order_synced: bool = False


class OutlineIssue(BaseModel):
    level: Literal["error", "warning"] = "warning"
    line: int | None = None
    message: str


class OutlineValidateOut(BaseModel):
    novel_id: str
    ok: bool = True
    volume_count: int = 0
    chapter_count: int = 0
    appendix_count: int = 0
    issues: list[OutlineIssue] = Field(default_factory=list)
    missing: list[dict[str, Any]] = Field(default_factory=list)
    #: 归一化后与 volumes.yaml / 章节 title 不一致的清单项
    mismatches: list[dict[str, Any]] = Field(default_factory=list)
    #: 大纲中未登记的卷/章（保持原 sort_order 追加末尾）
    unregistered: list[dict[str, Any]] = Field(default_factory=list)


VolumeOut.model_rebuild()
