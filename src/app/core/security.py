#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.core.security
文件名: security.py
作者: xionggaoqing
时间: 2026-10-27
说明: 密钥读写：API Key **只存 `.env`、从环境变量读取，不入数据库**（手册「二、(1) 密钥安全」）。

    DB 的 `llm_providers.api_key_env` 仅存环境变量名引用。写回 `.env` 时：
    只允许白名单键 → 读全文 → 替换/追加 → 临时文件 + `os.replace` 原子写 → 备份 `.env.bak`，
    全程加进程内互斥锁（风险表「UI 保存密钥需写回 .env」）。

    ★测试隔离：`DATA_ROOT` 被显式覆盖（非默认值）时，`.env` 读写重定向到
    `{DATA_ROOT}/.env`（首次读取时从项目根 `.env` 播种一份），防止测试污染真实 `.env`。
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from app.core.config import PROJECT_ROOT, get_settings

ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"

#: 允许由 UI 写回 `.env` 的键（白名单，防止破坏任意配置行）
_KEY_WHITELIST = re.compile(r"^[A-Z][A-Z0-9_]*(_API_KEY|_KEY|_TOKEN)$|^LANGFUSE_[A-Z_]+$")

_write_lock = threading.Lock()

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


def is_whitelisted_key(env_name: str) -> bool:
    """该环境变量名是否允许由 UI 写回。"""
    return bool(_KEY_WHITELIST.match(env_name.strip()))


def mask(key: str | None) -> str:
    """掩码脱敏：`sk-abcdefgh1234` → `sk-****1234`。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:3]}****{key[-4:]}"


def get_api_key(env_name: str) -> tuple[str | None, bool]:
    """读取密钥。返回 `(value, missing)`；先查进程环境，再回落解析 `.env` 文件。"""
    value = os.getenv(env_name)
    if value:
        return value, False
    parsed = read_env_file()
    value = parsed.get(env_name)
    if value:
        os.environ[env_name] = value  # 回填进程环境，避免重复读盘
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


def set_env_key(env_name: str, value: str, *, path: Path | None = None) -> None:
    """把密钥写回 `.env`（原子写 + 备份）。

    Raises:
        ValueError: 键不在白名单内。
    """
    name = env_name.strip()
    if not is_whitelisted_key(name):
        raise ValueError(f"环境变量 {name} 不在允许写回的白名单内")

    target = path or env_file_path()
    with _write_lock:
        lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
        replaced = False
        for i, raw in enumerate(lines):
            m = _KV_LINE.match(raw)
            if m and m.group(1) == name:
                lines[i] = f"{name}={value}"
                replaced = True
                break
        if not replaced:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"{name}={value}")

        # 写前备份，再临时文件 + os.replace 原子替换
        if target.exists():
            backup = target.with_suffix(target.suffix + ".bak")
            backup.write_bytes(target.read_bytes())
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, target)

    os.environ[name] = value


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
