# -*- coding: utf-8 -*-
"""
模块: app.core.config
文件名: config.py
作者: xionggaoqing
时间: 2026-10-27
说明: 应用配置：pydantic-settings 读取 `.env`（见手册「二、(5) 配置模块」）。

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

    # ---- Agent 装配 ----
    #: 子代理开关（测试可关闭以聚焦主循环）
    agent_subagents_enabled: bool = True
    #: 单次 run 的 LLM 调用软上限
    agent_model_call_limit: int = 40
    #: LangGraph 硬上限（recursion_limit）
    agent_recursion_limit: int = 60
    #: 门禁 G2 单章生成目标字数
    agent_word_target: int = 3000

    # ---- 业务默认值 ----
    chapter_revision_keep: int = 20
    autosave_interval_sec: int = 30
    pet_idle_minutes: int = 30

    # 配置版本：/config/reload 时 +1，使 Agent 工厂缓存失效
    # ★注意：这是进程级单调计数器（见 current_config_version），不随 Settings 重载归零；
    #   字段仅用于序列化快照，运行期一律用 current_config_version()。
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


# ---- 配置版本（进程级单调计数器，跨 .env 重载不归零）----
_config_version = 0


def current_config_version() -> int:
    """当前配置版本（Agent 工厂 / 模型缓存以此为键的一部分）。"""
    return _config_version


def bump_config_version() -> int:
    """`POST /config/reload` 调用：版本 +1，返回新值。"""
    global _config_version
    _config_version += 1
    return _config_version


def reload_settings() -> Settings:
    """重读 `.env` 并使缓存失效（`POST /config/reload`）。

    同时 `config_version += 1`，使 Agent 工厂与模型缓存失效。
    """
    get_settings.cache_clear()
    bump_config_version()
    return get_settings()
