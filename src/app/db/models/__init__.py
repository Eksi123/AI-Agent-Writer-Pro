"""ORM 模型（手册「五、数据模型」）。

M1 落地：novels / chapter_revisions / reading_progress / user_settings / project_index / audit_logs。
Agent 相关表（sessions / messages / runs / interrupts 等）在 M3 引入。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
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


class AuditLog(Base):
    """审计日志：数据表 + 只读查询接口，不做后台管理 UI；同时 JSONL 备份。"""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_entity", "entity_type", "entity_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: JSON 文本（detail_json）
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class ProjectIndex(Base):
    """项目统计缓存 + 脏标记（dirty=1 表示派生索引待重建）。"""

    __tablename__ = "project_index"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chapter_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_words: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_updated: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dirty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class UserSettings(Base):
    """通用设置：单行表（id 固定 'default'）。"""

    __tablename__ = "user_settings"

    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    #: light | dark | system
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    primary_color: Mapped[str] = mapped_column(String(20), nullable=False, default="#409EFF")
    font_size: Mapped[int] = mapped_column(Integer, nullable=False, default=18)
    font_family: Mapped[str] = mapped_column(String(50), nullable=False, default="serif")
    reading_width: Mapped[int] = mapped_column(Integer, nullable=False, default=42)
    autosave_interval_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    #: ★回落城市（浏览器定位失败/被拒时使用），不是定位结果
    city: Mapped[str] = mapped_column(String(60), nullable=False, default="北京")
    widget_enabled_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pet_idle_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class LlmProvider(Base, TimestampMixin):
    """LLM Provider。`api_key_env` 仅存环境变量名，密钥本身只在 `.env`。"""

    __tablename__ = "llm_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: openai | openai_compatible | anthropic | google | azure_openai | openrouter
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    api_key_env: Mapped[str | None] = mapped_column(String(80), nullable=True)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class LlmModel(Base, TimestampMixin):
    __tablename__ = "llm_models"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    max_context: Mapped[int] = mapped_column(Integer, nullable=False, default=32768)
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: 有序 model_ref 列表，供 ModelFallbackMiddleware 使用（M3）
    fallback_chain_json: Mapped[str | None] = mapped_column(Text, nullable=True)


#: 供 Alembic / `create_all` 发现所有表
ALL_MODELS = [
    Novel,
    ChapterRevision,
    ReadingProgress,
    AuditLog,
    ProjectIndex,
    UserSettings,
    LlmProvider,
    LlmModel,
]
