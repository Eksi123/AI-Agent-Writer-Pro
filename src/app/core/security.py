# -*- coding: utf-8 -*-
"""
模块: app.core.security
文件名: security.py
作者: xionggaoqing
时间: 2026-10-27
说明: 密钥读写：API Key 存**系统级环境变量**、从环境变量读取，**不入数据库**（手册「二、(1) 密钥安全」）。

    DB 的 `llm_providers.api_key_env` 仅存环境变量名引用。

    ★v1029：
    - 密钥改存系统级环境变量（见 `core/system_env.py`，用户作用域），`.env` 仅作历史密钥的兼容回落；
    - 密钥环境变量名**没有名称要求限制，可以任意命名**。原「必须以 `_API_KEY`/`_KEY`/`_TOKEN`
      结尾」的白名单已取消；改为只要求是合法的环境变量名，并**拒绝应用自身的配置键**
      （`Settings` 字段名），避免把密钥写坏配置。

    ★测试隔离：`DATA_ROOT` 被显式覆盖（非默认值）时，`.env` 读写重定向到
    `{DATA_ROOT}/.env`（首次读取时从项目根 `.env` 播种一份），防止测试污染真实 `.env`。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.core.config import PROJECT_ROOT, get_settings

ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"

#: v1029：合法的环境变量名（任意命名，只要 shell 可接受）
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: 应用自身的配置键：由 `Settings` 字段名派生（★避免与常量清单漂移）
_RESERVED_CONFIG_KEYS: frozenset[str] = frozenset()


def _reserved_config_keys() -> frozenset[str]:
    """应用配置键集合（大写）。密钥不得占用，防止把 `APP_PORT` 等写成密钥值。"""
    global _RESERVED_CONFIG_KEYS
    if not _RESERVED_CONFIG_KEYS:
        from app.core.config import Settings

        _RESERVED_CONFIG_KEYS = frozenset(f.upper() for f in Settings.model_fields)
    return _RESERVED_CONFIG_KEYS


_KV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def env_file_path() -> Path:
    """当前生效的 `.env` 路径。

    生产：项目根 `.env`（pydantic-settings 读取的同一份）；
    测试（DATA_ROOT 被覆盖）：`{DATA_ROOT}/.env`，首次访问时从项目根播种。
    """
    settings = get_settings()
    default_root = (PROJECT_ROOT / "data").resolve()
    data_root = Path(settings.data_root).resolve()
    if data_root != default_root:
        local = data_root / ".env"
        real = PROJECT_ROOT / ".env"
        if not local.exists() and real.exists():
            data_root.mkdir(parents=True, exist_ok=True)
            local.write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
        return local
    return PROJECT_ROOT / ".env"


def is_valid_secret_key_name(env_name: str) -> bool:
    """v1029：密钥环境变量名是否可用。

    规则（宽松，满足「没有名称要求限制，可以任意命名」）：
    ① 是合法的环境变量名（字母/下划线开头，仅含字母数字下划线）；
    ② 不与应用自身的配置键（`Settings` 字段名）冲突——否则会写坏 `.env` 里的配置行。
    """
    name = (env_name or "").strip()
    if not name or not _ENV_NAME_RE.match(name):
        return False
    return name.upper() not in _reserved_config_keys()


def is_whitelisted_key(env_name: str) -> bool:
    """兼容别名（★v1029 语义已变：不再要求 `_API_KEY`/`_KEY`/`_TOKEN` 后缀）。"""
    return is_valid_secret_key_name(env_name)


def mask(key: str | None) -> str:
    """掩码脱敏：`sk-abcdefgh1234` → `sk-****1234`。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:3]}****{key[-4:]}"


def get_api_key(env_name: str) -> tuple[str | None, bool]:
    """读取密钥。返回 `(value, missing)`。

    v1029：密钥存在**系统级环境变量**，查找顺序为
    ① 当前进程环境 → ② 系统级持久化存储（注册表 / `~/.ai-writer-pro/env.json`）
    → ③ 项目 `.env`（★仅作历史密钥的兼容回落，新密钥不再写这里）。
    """
    value = os.getenv(env_name)
    if value:
        return value, False

    from app.core.system_env import system_env_get

    value = system_env_get(env_name)
    if value:
        os.environ[env_name] = value  # 回填进程环境，避免重复查注册表/读盘
        return value, False

    parsed = read_env_file()
    value = parsed.get(env_name)
    if value:
        os.environ[env_name] = value
        return value, False
    return None, True


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """解析 `.env` 为 dict（保留原顺序）。忽略注释与空行。"""
    target = path or env_file_path()
    if not target.exists():
        return {}
    result: dict[str, str] = {}
    for raw in target.read_text(encoding="utf-8").splitlines():
        m = _KV_LINE.match(raw)
        if not m:
            continue
        result[m.group(1)] = _unquote(m.group(2))
    return result


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def ensure_env_example() -> None:
    """若缺 `.env.example` 则按手册「十二」的键清单生成（M0 交付项）。"""
    if ENV_EXAMPLE_PATH.exists():
        return
    ENV_EXAMPLE_PATH.write_text(ENV_EXAMPLE_TEMPLATE, encoding="utf-8")


ENV_EXAMPLE_TEMPLATE = """\
APP_HOST=127.0.0.1
APP_PORT=8000
DATA_ROOT=./data
LOG_LEVEL=INFO

# LLM（密钥只进 .env，DB 只存变量名）
DASHSCOPE_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
AZURE_OPENAI_API_KEY=
OPENROUTER_API_KEY=

# Langfuse（三项全空则追踪自动降级为本地日志）
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
"""


def configured_provider_keys() -> dict[str, bool]:
    """启动时探测各 Provider 密钥是否已配置（供前端「未配置密钥」警示）。"""
    names = [
        "DASHSCOPE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    ]
    parsed = read_env_file()
    return {n: bool(os.getenv(n) or parsed.get(n)) for n in names}
