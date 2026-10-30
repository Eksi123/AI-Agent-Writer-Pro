# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_system_env.py
作者: xionggaoqing
时间: 2026-10-29
说明: v1029 密钥存储：系统级环境变量（用户作用域）+ 任意命名的变量名校验。

    ★测试隔离：`DATA_ROOT` 被覆盖时 `system_env` 重定向到 `{DATA_ROOT}/system-env.json`，
    **绝不触碰真实系统环境**（注册表 / HOME 目录），故这些断言可在任何机器上跑。
"""

from __future__ import annotations

import os

import pytest

from app.core import system_env
from app.core.security import get_api_key, is_valid_secret_key_name


class TestSecretNameValidation:
    """v1029：密钥环境变量名没有名称要求限制，可以任意命名。"""

    @pytest.mark.parametrize(
        "name",
        ["MY_RANDOM_VAR", "RANDOM_VAR", "sk_secret_1", "deepseek", "ANYTHING_123", "_leading", "a"],
    )
    def test_allowed_names(self, name: str) -> None:
        assert is_valid_secret_key_name(name) is True

    @pytest.mark.parametrize("name", ["APP_PORT", "DATA_ROOT", "data_root", "LOG_LEVEL", "App_Port"])
    def test_app_config_keys_rejected(self, name: str) -> None:
        """不得占用应用自身配置键，否则会把 `APP_PORT` 等写成密钥值。"""
        assert is_valid_secret_key_name(name) is False

    @pytest.mark.parametrize("name", ["BAD-NAME", "1BAD", "HAS SPACE", "", "   ", "含中文", "A.B"])
    def test_illegal_names_rejected(self, name: str) -> None:
        assert is_valid_secret_key_name(name) is False


class TestSystemEnvStore:
    """系统级环境变量的写入 / 读取 / 删除（隔离态下走 JSON 文件）。"""

    def test_isolated_mode_active(self) -> None:
        """前置条件：测试里必须是隔离态，否则会误改真实系统环境。"""
        assert system_env._is_isolated() is True

    def test_set_get_delete_roundtrip(self) -> None:
        name = "V1029_ROUNDTRIP_KEY"
        assert system_env.system_env_set(name, "sk-abc123") is True

        # 写入后本进程即可读到（无需重启应用）
        assert os.environ[name] == "sk-abc123"
        assert system_env.system_env_get(name) == "sk-abc123"

        assert system_env.system_env_delete(name) is True
        assert name not in os.environ
        assert system_env.system_env_get(name) is None

    def test_persisted_in_store_not_only_process_env(self) -> None:
        """★持久化到存储：清掉进程环境后仍能读回（模拟重启应用）。"""
        name = "V1029_PERSIST_KEY"
        system_env.system_env_set(name, "sk-persist")

        os.environ.pop(name, None)
        assert system_env.system_env_get(name) == "sk-persist"

        system_env.system_env_delete(name)

    def test_value_with_special_chars_roundtrips(self) -> None:
        """密钥含引号/反斜杠/空格等非 shell 友好字符时不得损坏。"""
        name = "V1029_SPECIAL_KEY"
        value = "sk-'quote\"back\\slash space$"
        system_env.system_env_set(name, value)

        os.environ.pop(name, None)
        assert system_env.system_env_get(name) == value

        system_env.system_env_delete(name)

    def test_delete_missing_returns_false(self) -> None:
        assert system_env.system_env_delete("V1029_NEVER_SET_KEY") is False

    def test_overwrite_existing(self) -> None:
        name = "V1029_OVERWRITE_KEY"
        system_env.system_env_set(name, "old-value")
        system_env.system_env_set(name, "new-value")

        os.environ.pop(name, None)
        assert system_env.system_env_get(name) == "new-value"

        system_env.system_env_delete(name)


class TestApiKeyLookupOrder:
    """`get_api_key` 查找顺序：进程环境 → 系统级存储 → `.env`（历史回落）。"""

    def test_reads_from_system_env_store(self) -> None:
        name = "V1029_LOOKUP_KEY"
        system_env.system_env_set(name, "sk-from-system")

        # 即使清掉进程环境，也应从系统级存储读到
        os.environ.pop(name, None)
        value, missing = get_api_key(name)
        assert missing is False
        assert value == "sk-from-system"

        system_env.system_env_delete(name)

    def test_missing_key(self) -> None:
        value, missing = get_api_key("V1029_NO_SUCH_KEY_AT_ALL")
        assert missing is True
        assert value is None


class TestProviderSecretLifecycle:
    """Provider 生命周期与系统级密钥的联动（共用保护 + 删除清理）。"""

    async def test_create_provider_writes_system_env(self, client) -> None:
        resp = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "系统密钥厂商",
                "type": "openai_compatible",
                "api_key_env": "V1029_PROVIDER_KEY",
                "api_key_value": "sk-provider-secret",
            },
        )
        assert resp.json()["code"] == 0, resp.text
        assert resp.json()["data"]["api_key_configured"] is True

        # 密钥落在系统级存储（隔离态为 {DATA_ROOT}/system-env.json）
        os.environ.pop("V1029_PROVIDER_KEY", None)
        assert system_env.system_env_get("V1029_PROVIDER_KEY") == "sk-provider-secret"

        pid = resp.json()["data"]["id"]
        await client.delete(f"/api/v1/llm/providers/{pid}")

    async def test_delete_provider_removes_system_env(self, client) -> None:
        """删除 Provider → 其系统级密钥一并清除（不残留）。"""
        resp = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "待删除厂商",
                "type": "openai_compatible",
                "api_key_env": "V1029_DELETE_ME_KEY",
                "api_key_value": "sk-to-be-removed",
            },
        )
        pid = resp.json()["data"]["id"]
        assert system_env.system_env_get("V1029_DELETE_ME_KEY") == "sk-to-be-removed"

        await client.delete(f"/api/v1/llm/providers/{pid}")

        os.environ.pop("V1029_DELETE_ME_KEY", None)
        assert system_env.system_env_get("V1029_DELETE_ME_KEY") is None

    async def test_shared_env_name_not_deleted(self, client) -> None:
        """★两家 Provider 共用同一变量名时，删掉一家不得误删另一家的密钥。"""
        shared = "V1029_SHARED_KEY"
        r1 = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "共用厂商甲",
                "type": "openai_compatible",
                "api_key_env": shared,
                "api_key_value": "sk-shared",
            },
        )
        r2 = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "共用厂商乙",
                "type": "openai_compatible",
                "api_key_env": shared,
                "api_key_value": "sk-shared",
            },
        )
        pid1, pid2 = r1.json()["data"]["id"], r2.json()["data"]["id"]

        await client.delete(f"/api/v1/llm/providers/{pid1}")
        # 乙仍在用 → 密钥必须保留
        os.environ.pop(shared, None)
        assert system_env.system_env_get(shared) == "sk-shared"

        await client.delete(f"/api/v1/llm/providers/{pid2}")
        # 两家都删了 → 现在才清理
        os.environ.pop(shared, None)
        assert system_env.system_env_get(shared) is None

    async def test_clear_api_key_removes_system_env(self, client) -> None:
        resp = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "清除密钥厂商",
                "type": "openai_compatible",
                "api_key_env": "V1029_CLEAR_KEY",
                "api_key_value": "sk-clear-me",
            },
        )
        pid = resp.json()["data"]["id"]

        resp = await client.patch(
            f"/api/v1/llm/providers/{pid}", json={"clear_api_key": True}
        )
        assert resp.json()["data"]["api_key_env"] is None

        os.environ.pop("V1029_CLEAR_KEY", None)
        assert system_env.system_env_get("V1029_CLEAR_KEY") is None

        await client.delete(f"/api/v1/llm/providers/{pid}")

    async def test_rename_env_leaves_no_orphan(self, client) -> None:
        """换环境变量名并写入新密钥后，旧变量名成为孤儿 → 清理。"""
        r = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "改名厂商",
                "type": "openai_compatible",
                "api_key_env": "V1029_OLD_NAME_KEY",
                "api_key_value": "sk-old",
            },
        )
        pid = r.json()["data"]["id"]

        r = await client.patch(
            f"/api/v1/llm/providers/{pid}",
            json={"api_key_env": "V1029_NEW_NAME_KEY", "api_key_value": "sk-new"},
        )
        assert r.json()["data"]["api_key_env"] == "V1029_NEW_NAME_KEY"

        os.environ.pop("V1029_OLD_NAME_KEY", None)
        os.environ.pop("V1029_NEW_NAME_KEY", None)
        assert system_env.system_env_get("V1029_OLD_NAME_KEY") is None
        assert system_env.system_env_get("V1029_NEW_NAME_KEY") == "sk-new"

        await client.delete(f"/api/v1/llm/providers/{pid}")
