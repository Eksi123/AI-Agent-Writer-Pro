# -*- coding: utf-8 -*-
"""
模块: app.core.system_env
文件名: system_env.py
作者: xionggaoqing
时间: 2026-10-29
说明: 系统级环境变量读写，适配 Windows / Linux / macOS。

    一律使用**用户级**作用域（不需要管理员/root），避免污染整机环境：
    - Windows：注册表 `HKCU\\Environment`（标准库 `winreg` 写 + 广播 WM_SETTINGCHANGE），
      持久、重启后仍生效；不用 `setx`——它会静默截断 >1024 字符的值，且让密钥暴露在命令行；
    - macOS / Linux：密钥事实源 `~/.ai-writer-pro/env.json`（0600），并派生同目录 shell 文件
      `~/.ai-writer-pro/env`（0600）供登录 shell `source`；确保 profile 里有幂等的加载行
      （只追加标记块，**不改写 profile 正文**）；macOS 额外 `launchctl setenv` 让 GUI 会话立即可见。

    读取走 `system_env_get()`：优先当前进程环境，再查持久化存储——因为 Windows 的
    `winreg` 写注册表 / macOS 的 `launchctl setenv` **不会**影响已在运行的本进程。

    测试隔离：`DATA_ROOT` 被显式覆盖（非默认值）时，全部读写重定向到
    `{DATA_ROOT}/system-env.json`，**绝不触碰真实系统环境**（与 `.env` 的隔离策略一致）。
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
from pathlib import Path

from app.core.config import PROJECT_ROOT, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()

#: unix 密钥存储：`env.json` 是 Python 读取的事实源（精确、无转义歧义），
#: `env` 由它派生、供 shell `source` 载入（POSIX 单引号转义，0600 权限）
_UNIX_ENV_DIR = Path.home() / ".ai-writer-pro"
_UNIX_STATE_FILE = _UNIX_ENV_DIR / "env.json"
_UNIX_ENV_FILE = _UNIX_ENV_DIR / "env"
_PROFILE_MARK_BEGIN = "# >>> ai-writer-pro secrets >>>"
_PROFILE_MARK_END = "# <<< ai-writer-pro secrets <<<"
_PROFILE_LINE = (
    f'{_PROFILE_MARK_BEGIN}\n'
    f'[ -f "{_UNIX_ENV_FILE}" ] && . "{_UNIX_ENV_FILE}"\n'
    f"{_PROFILE_MARK_END}\n"
)
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


# ---------------------------------------------------------------------------
# 测试隔离：DATA_ROOT 被覆盖时用 JSON 文件冒充系统环境
# ---------------------------------------------------------------------------


def _is_isolated() -> bool:
    """是否处于测试隔离态（DATA_ROOT 非默认值 → 不碰真实系统环境）。"""
    settings = get_settings()
    default_root = (PROJECT_ROOT / "data").resolve()
    return Path(settings.data_root).resolve() != default_root


def _isolated_file() -> Path:
    return Path(get_settings().data_root) / "system-env.json"


def _isolated_read() -> dict[str, str]:
    path = _isolated_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        logger.warning("system_env_isolated_read_failed", path=str(path))
        return {}


def _isolated_write(store: dict[str, str]) -> None:
    path = _isolated_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Windows：HKCU\Environment
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _win_broadcast_change() -> None:
    """广播 `WM_SETTINGCHANGE`，让新进程/资源管理器立即看到变更（best-effort）。

    注册表写入本身已持久；广播只是免注销的便利，失败不影响结果。
    """
    try:
        import ctypes
        from ctypes import wintypes

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002

        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            wintypes.HWND(HWND_BROADCAST),
            WM_SETTINGCHANGE,
            0,
            ctypes.c_wchar_p("Environment"),
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )
    except Exception as exc:  # pragma: no cover - 广播失败不影响持久化
        logger.debug("system_env_win_broadcast_failed", error=str(exc))


def _win_set(name: str, value: str) -> bool:
    """写 `HKCU\\Environment`（用户级，无需管理员）。失败返回 False。

    用标准库 `winreg` 而非 `setx`：
    - `setx` 会把**超过 1024 字符的值静默截断**（长密钥会被写坏且无报错）；
    - `setx` 让密钥出现在**命令行**上，任何本机进程都能在进程列表里看到明文。
    `winreg` 两者皆无，且写的是同一处注册表键（重启后仍生效）。
    """
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows 平台
        logger.warning("system_env_winreg_unavailable")
        return False

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    except OSError as exc:
        logger.warning("system_env_win_set_failed", name=name, error=str(exc))
        return False

    _win_broadcast_change()
    return True


def _win_get(name: str) -> str | None:
    """读 `HKCU\\Environment`；变量不存在返回 None。"""
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows 平台
        return None

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_QUERY_VALUE
        ) as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None

    if value is None:
        return None
    if isinstance(value, bytes):  # pragma: no cover - 非 REG_SZ 的历史值兜底
        return value.decode("utf-8", errors="replace")
    return str(value)


def _win_delete(name: str) -> bool:
    """删除 `HKCU\\Environment` 下的变量；不存在返回 False。"""
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows 平台
        return False

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        return False

    _win_broadcast_change()
    return True


# ---------------------------------------------------------------------------
# macOS / Linux：~/.ai-writer-pro/env（0600）+ profile 幂等 source 行
# ---------------------------------------------------------------------------


def _unix_read_file() -> dict[str, str]:
    """读取密钥事实源 `env.json`（不存在/损坏时回落到派生的 shell 文件）。"""
    if _UNIX_STATE_FILE.exists():
        try:
            data = json.loads(_UNIX_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (ValueError, OSError) as exc:
            logger.warning("system_env_state_read_failed", error=str(exc))

    if not _UNIX_ENV_FILE.exists():
        return {}
    result: dict[str, str] = {}
    for raw in _UNIX_ENV_FILE.read_text(encoding="utf-8").splitlines():
        m = _LINE_RE.match(raw)
        if m:
            result[m.group(1)] = _shell_unquote(m.group(2))
    return result


def _unix_write_file(store: dict[str, str]) -> None:
    """写事实源 `env.json` + 派生 shell 文件 `env`（两者都 0600）。"""
    _UNIX_ENV_DIR.mkdir(parents=True, exist_ok=True)

    state_tmp = _UNIX_STATE_FILE.with_suffix(".tmp")
    state_tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(state_tmp, _UNIX_STATE_FILE)

    env_tmp = _UNIX_ENV_FILE.with_suffix(".tmp")
    lines = [f"{k}={_shell_escape(v)}" for k, v in store.items()]
    env_tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(env_tmp, _UNIX_ENV_FILE)

    for path in (_UNIX_STATE_FILE, _UNIX_ENV_FILE):
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - 某些文件系统不支持
            logger.debug("system_env_chmod_failed", path=str(path))


def _shell_escape(value: str) -> str:
    """写进 shell 文件时用单引号包裹，内部单引号按 POSIX 规则转义。"""
    return "'" + value.replace("'", "'\\''") + "'"


def _shell_unquote(raw: str) -> str:
    """反解 `_shell_escape`：剥外层单引号并还原 `'\\''` → `'`；非单引号形式按双引号处理。"""
    v = raw.strip()
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1].replace("'\\''", "'")
    return _unquote(v)


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _ensure_profile_source() -> None:
    """确保登录 shell 的 profile 里有（且只有一行）加载标记；不改写其它内容。"""
    home = Path.home()
    candidates = [home / ".profile"]
    if os.path.exists(home / ".zshrc") or platform.system() == "Darwin":
        candidates.insert(0, home / ".zprofile")
    if os.path.exists(home / ".bashrc"):
        candidates.insert(0, home / ".bashrc")

    for profile in candidates:
        try:
            text = profile.read_text(encoding="utf-8") if profile.exists() else ""
            if _PROFILE_MARK_BEGIN in text:
                continue  # 已注入，幂等跳过
            with profile.open("a", encoding="utf-8") as fh:
                if text and not text.endswith("\n"):
                    fh.write("\n")
                fh.write(_PROFILE_LINE)
            return
        except OSError as exc:
            logger.debug("profile_write_failed", path=str(profile), error=str(exc))


def _unix_launchctl_set(name: str, value: str) -> None:
    """macOS：让当前 GUI 会话立即可见（重启后由 profile source 生效）。"""
    if platform.system() != "Darwin":
        return
    ctl = shutil.which("launchctl")
    if not ctl:
        return
    try:
        subprocess.run(  # noqa: S603 - 固定命令 launchctl，参数受控
            [ctl, "setenv", name, value], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("launchctl_set_failed", name=name, error=str(exc))


def _unix_launchctl_unset(name: str) -> None:
    if platform.system() != "Darwin":
        return
    ctl = shutil.which("launchctl")
    if not ctl:
        return
    try:
        subprocess.run(  # noqa: S603 - 固定命令 launchctl，参数受控
            [ctl, "unsetenv", name], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("launchctl_unset_failed", name=name, error=str(exc))


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------


def system_env_set(name: str, value: str) -> bool:
    """把密钥写入**系统级（用户作用域）环境变量**并持久化。返回是否成功。

    Windows 的注册表写入 / macOS 的 launchctl **不会影响本进程**，因此同时写回
    `os.environ`，让应用无需重启即可用新密钥调用大模型。
    """
    with _lock:
        if _is_isolated():
            store = _isolated_read()
            store[name] = value
            _isolated_write(store)
            os.environ[name] = value
            return True

        ok = _win_set(name, value) if _is_windows() else _unix_persist(name, value)
        if ok:
            os.environ[name] = value
        return ok


def _unix_persist(name: str, value: str) -> bool:
    try:
        store = _unix_read_file()
        store[name] = value
        _unix_write_file(store)
        _ensure_profile_source()
        _unix_launchctl_set(name, value)
        return True
    except OSError as exc:
        logger.warning("system_env_unix_set_failed", name=name, error=str(exc))
        return False


def system_env_get(name: str) -> str | None:
    """从系统级环境变量读取（当前进程 → 持久化存储）。"""
    value = os.getenv(name)
    if value:
        return value
    with _lock:
        if _is_isolated():
            return _isolated_read().get(name)
        if _is_windows():
            return _win_get(name)
        return _unix_read_file().get(name)


def system_env_delete(name: str) -> bool:
    """删除系统级环境变量（换密钥 / 删除 Provider 时清理，避免残留）。"""
    with _lock:
        os.environ.pop(name, None)
        if _is_isolated():
            store = _isolated_read()
            existed = name in store
            store.pop(name, None)
            _isolated_write(store)
            return existed

        if _is_windows():
            return _win_delete(name)

        store = _unix_read_file()
        existed = name in store
        if existed:
            store.pop(name, None)
            try:
                _unix_write_file(store)
            except OSError as exc:
                logger.warning("system_env_unix_delete_failed", name=name, error=str(exc))
                return False
        _unix_launchctl_unset(name)
        return existed
