# -*- coding: utf-8 -*-
"""
模块: app.db.models.novel
文件名: novel.py
作者: xionggaoqing
时间: 2026-10-27
说明: 内容域 ORM：novels / chapter_revisions / reading_progress / project_index
    （手册「五、数据模型」M1）。

    项目登记表只做登记 + 外键锚点，正文以工作区文件为事实源，可全量重建；
    `project_index` 是派生索引（字数/章数/脏标记），不是事实源。
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_id, utcnow


class Novel(Base, TimestampMixin):
    """项目登记表：只做登记 + 外键锚点，内容以文件为事实源，可从工作区全量重建。"""

    __tablename__ = "novels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 工作区目录，相对 data/ 根（如 `workspaces/{project_id}`）
    workspace_dir: Mapped[str] = mapped_column(String(300), nullable=False)
    #: 大纲相对路径，重命名后同步更新，是大纲唯一寻址依据
    outline_file: Mapped[str] = mapped_column(String(300), nullable=False)
    author_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    genre: Mapped[str | None] = mapped_column(String(50), nullable=True)
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: active | deleted（软删除）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class ChapterRevision(Base):
    """章节修订快照。自动保存不快照（防版本爆炸）。"""

    __tablename__ = "chapter_revisions"
    __table_args__ = (Index("ix_chapter_revisions_chapter_id_version", "chapter_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    novel_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: manual | agent
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class ReadingProgress(Base):
    """阅读进度：单用户单行/项目。"""

    __tablename__ = "reading_progress"

    novel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chapter_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scroll_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class ProjectIndex(Base):
    """项目统计缓存 + 脏标记（dirty=1 表示派生索引待重建）。"""

    __tablename__ = "project_index"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chapter_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_words: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_updated: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dirty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
