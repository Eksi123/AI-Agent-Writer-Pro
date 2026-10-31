# -*- coding: utf-8 -*-
"""
模块: app.core.config
文件名: config.py
作者: xionggaoqing
时间: 2026-10-27
说明: 应用配置：pydantic-settings（环境变量优先；`.env` 仅作历史兼容，可不存在）。

    ★v1031 续：`.env` / `.env.example` 已移除——应用配置全部有默认值，
    Langfuse 三项改读根目录 `config.ini` 的 `[langfuse]` 段（见 `_read_config_ini`）；
    大模型密钥在系统级环境变量（`core/system_env.py`），DB 仅存变量名引用。
"""

from __future__ import annotations

import configparser
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 仓库根（src/app/core/config.py → 上四级）
PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: ★v1031 续：Langfuse 配置文件（根目录 config.ini 的 [langfuse] 段）
CONFIG_INI_PATH = PROJECT_ROOT / "config.ini"


def _read_config_ini_langfuse() -> dict[str, str]:
    """读 `config.ini` 的 `[langfuse]` 段；文件缺失/损坏一律返回空（降级为本地日志）。"""
    out: dict[str, str] = {}
    if not CONFIG_INI_PATH.exists():
        return out
    parser = configparser.ConfigParser()
    try:
        parser.read(CONFIG_INI_PATH, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return out
    if parser.has_section("langfuse"):
        for key in ("public_key", "secret_key", "host"):
            val = parser.get("langfuse", key, fallback="").strip()
            if val:
                out[key] = val
    return out


_LANGFUSE_INI = _read_config_ini_langfuse()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),  # ★历史兼容：文件可不存在
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
    # ★v1031 续：默认值取自根目录 config.ini 的 [langfuse] 段（default_factory 保证
    #   reload_settings 重读后生效）；环境变量仍可覆盖
    langfuse_public_key: str = Field(default_factory=lambda: _LANGFUSE_INI.get("public_key", ""))
    langfuse_secret_key: str = Field(default_factory=lambda: _LANGFUSE_INI.get("secret_key", ""))
    langfuse_host: str = Field(
        default_factory=lambda: _LANGFUSE_INI.get("host", "https://cloud.langfuse.com")
    )

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
    """重读配置并使缓存失效（`POST /config/reload`）。

    ★v1031 续：同时重读 `config.ini` 的 `[langfuse]` 段；`config_version += 1`，
    使 Agent 工厂与模型缓存失效。
    """
    global _LANGFUSE_INI
    _LANGFUSE_INI = _read_config_ini_langfuse()
    get_settings.cache_clear()
    bump_config_version()
    return get_settings()
