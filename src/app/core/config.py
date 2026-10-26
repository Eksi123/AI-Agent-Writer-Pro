"""应用配置：pydantic-settings 读取 `.env`（见手册「二、(5) 配置模块」）。

密钥类配置只存 `.env`，DB 仅存环境变量名引用。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 仓库根（src/app/core/config.py → 上四级）
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 服务 ----
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    log_level: str = "INFO"

    # ---- 数据根 ----
    data_root: Path = PROJECT_ROOT / "data"

    # ---- 可观测：三项全空则追踪自动降级为本地日志 ----
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ---- SSE 环形缓冲（断线续传）----
    sse_buffer_size: int = 500

    # ---- 业务默认值 ----
    chapter_revision_keep: int = 20
    autosave_interval_sec: int = 30
    pet_idle_minutes: int = 30

    # 配置版本：/config/reload 时 +1，使 Agent 工厂缓存失效
    config_version: int = 0

    @property
    def db_path(self) -> Path:
        return self.data_root / "app.db"

    @property
    def agent_state_path(self) -> Path:
        return self.data_root / "agent_state.db"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例。测试中用 `get_settings.cache_clear()` 重置。"""
    return Settings()


def reload_settings() -> Settings:
    """重读 `.env` 并使缓存失效（`POST /config/reload`）。"""
    get_settings.cache_clear()
    return get_settings()
