# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_llm.py
作者: xionggaoqing
时间: 2026-10-27
说明: LLM 配置与密钥管理测试。

    包含：
    - LLM Provider/Model CRUD 与 API Key 掩码
    - 密钥存储到 .env 文件（非系统环境变量）
    - Provider 类型与密钥验证
"""

from __future__ import annotations

import json

import pytest


async def _setup_provider_model(client, *, model_id: str = "fake-model") -> tuple[str, str]:
    """建一个 Provider + 默认 Model，返回 (provider_id, model_ref)。密钥用假值（假模型不联网）。"""
    resp = await client.post(
        "/api/v1/llm/providers",
        json={
            "name": "测试厂商",
            "type": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "api_key_env": "OPENAI_API_KEY",
            "api_key_value": "sk-test-fake-key-0000",
        },
    )
    assert resp.status_code == 200, resp.text
    provider = resp.json()["data"]
    provider_id = provider["id"]
    assert provider["api_key_configured"] is True
    assert provider["api_key_masked"].startswith("sk-")
    assert "test-fake" not in provider["api_key_masked"]  # 不回显明文

    resp = await client.post(
        "/api/v1/llm/models",
        json={
            "provider_id": provider_id,
            "model_id": model_id,
            "display_name": "假模型",
            "is_default": True,
        },
    )
    assert resp.status_code == 200, resp.text
    model = resp.json()["data"]
    return provider_id, model["model_ref"]


class TestLlmProviderApi:
    """LLM Provider/Model 配置 API"""

    async def test_provider_crud_and_masking(self, client):
        provider_id, model_ref = await _setup_provider_model(client)

        resp = await client.get("/api/v1/llm/providers")
        providers = resp.json()["data"]
        assert len(providers) == 1
        assert providers[0]["id"] == provider_id
        assert providers[0]["api_key_configured"] is True
        # 掩码不含明文
        assert "test-fake-key" not in json.dumps(providers[0])

        # 改优先级
        resp = await client.patch(f"/api/v1/llm/providers/{provider_id}", json={"priority": 5})
        assert resp.json()["data"]["priority"] == 5

        # 删除级联删模型
        resp = await client.delete(f"/api/v1/llm/providers/{provider_id}")
        assert resp.json()["data"]["deleted"] is True
        resp = await client.get("/api/v1/llm/models")
        assert resp.json()["data"] == []

    async def test_duplicate_provider_name_rejected(self, client):
        await _setup_provider_model(client)
        resp = await client.post(
            "/api/v1/llm/providers",
            json={"name": "测试厂商", "type": "openai"},
        )
        assert resp.json()["code"] == 40001

    async def test_invalid_provider_type_rejected(self, client):
        # 项目约定：pydantic 校验错误统一转 400 + code 40001（见 main._validation_error）
        resp = await client.post("/api/v1/llm/providers", json={"name": "x", "type": "bogus"})
        assert resp.status_code == 400
        assert resp.json()["code"] == 40001

    async def test_api_key_env_allows_arbitrary_name(self, client):
        """密钥环境变量名可**任意命名**（不再要求 _API_KEY/_KEY/_TOKEN 后缀）。"""
        resp = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "任意命名厂商",
                "type": "openai",
                "api_key_env": "MY_RANDOM_VAR",
                "api_key_value": "x",
            },
        )
        assert resp.json()["code"] == 0, resp.text
        assert resp.json()["data"]["api_key_env"] == "MY_RANDOM_VAR"

    @pytest.mark.parametrize(
        "bad_name",
        ["APP_PORT", "data_root", "1BAD", "BAD-NAME", "HAS SPACE", "含中文"],
    )
    async def test_api_key_env_rejects_invalid_names(self, client, bad_name):
        """任意命名仍要挡住①非法字符 ②与应用配置键重名（否则写坏 .env）。"""
        resp = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "非法变量名厂商",
                "type": "openai",
                "api_key_env": bad_name,
                "api_key_value": "x",
            },
        )
        assert resp.json()["code"] == 40001, f"{bad_name!r} 应被拒绝，实际 {resp.text}"

    async def test_default_model_and_overview(self, client):
        _pid, model_ref = await _setup_provider_model(client)
        resp = await client.get("/api/v1/llm/overview")
        data = resp.json()["data"]
        assert data["default_model_ref"] == model_ref
        assert any(m["model_ref"] == model_ref for m in data["models"])

    async def test_config_reload_bumps_version(self, client):
        resp = await client.post("/api/v1/config/reload")
        v1 = resp.json()["data"]["config_version"]
        resp = await client.post("/api/v1/config/reload")
        v2 = resp.json()["data"]["config_version"]
        assert v2 > v1

    async def test_test_connection_reports_missing_key(self, client):
        resp = await client.post(
            "/api/v1/llm/providers",
            json={"name": "无密钥", "type": "openai", "api_key_env": "NOTSET_TEST_API_KEY"},
        )
        pid = resp.json()["data"]["id"]
        resp = await client.post(f"/api/v1/llm/providers/{pid}/test", json={})
        data = resp.json()["data"]
        assert data["ok"] is False
        assert "未配置密钥" in data["message"]


class TestSecretNameValidation:
    """密钥环境变量名验证规则"""

    @pytest.mark.parametrize(
        "name",
        [
            "OPENAI_API_KEY",
            "MY_CUSTOM_SECRET",
            "vendor_key",
            "SOME_TOKEN",
            "X123",
        ],
    )
    def test_allowed_names(self, name: str) -> None:
        from app.core.security import is_valid_secret_key_name

        assert is_valid_secret_key_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "APP_PORT",
            "DATA_ROOT",
            "LOG_LEVEL",
            "CORS_ORIGINS",
        ],
    )
    def test_app_config_keys_rejected(self, name: str) -> None:
        """应用配置键不得用作密钥名（防止覆盖 .env 里的核心配置）。"""
        from app.core.security import is_valid_secret_key_name

        assert is_valid_secret_key_name(name) is False

    @pytest.mark.parametrize(
        "name",
        [
            "1INVALID",
            "has-dash",
            "has space",
            "中文",
            "",
            "a" * 100,
        ],
    )
    def test_illegal_names_rejected(self, name: str) -> None:
        from app.core.security import is_valid_secret_key_name

        assert is_valid_secret_key_name(name) is False


class TestSystemEnvStore:
    """环境变量存储（隔离模式 + 持久化）"""

    def test_isolated_mode_active(self) -> None:
        """测试环境应始终处于隔离模式（不污染进程 env）。"""
        from app.core.system_env import _is_isolated

        assert _is_isolated() is True

    def test_set_get_delete_roundtrip(self) -> None:
        """set → get → delete 完整生命周期。"""
        from app.core.system_env import system_env_set, system_env_get, system_env_delete

        key = "_TEST_ROUNDTRIP_KEY"
        value = "roundtrip_value_123"

        system_env_set(key, value)
        assert system_env_get(key) == value

        deleted = system_env_delete(key)
        assert deleted is True
        assert system_env_get(key) is None

    def test_persisted_in_store_not_only_process_env(self) -> None:
        """密钥存到 .env 文件，不仅仅是进程环境变量。"""
        import os

        from app.core.system_env import system_env_set, system_env_get, system_env_delete

        key = "_TEST_PERSISTED_KEY"
        value = "persisted_value"

        system_env_set(key, value)

        # 即使进程 env 没有，store 仍能返回
        os.environ.pop(key, None)
        assert system_env_get(key) == value

        system_env_delete(key)

    def test_value_with_special_chars_roundtrips(self) -> None:
        """含特殊字符的值能正确保存和读取。"""
        from app.core.system_env import system_env_set, system_env_get, system_env_delete

        key = "_TEST_SPECIAL_CHARS"
        value = 'value with "quotes" and\nnewline and $dollar'

        system_env_set(key, value)
        assert system_env_get(key) == value

        system_env_delete(key)

    def test_delete_missing_returns_false(self) -> None:
        from app.core.system_env import system_env_delete

        assert system_env_delete("_NONEXISTENT_KEY_XYZ") is False

    def test_overwrite_existing(self) -> None:
        """重复 set 同一 key 应覆盖旧值。"""
        from app.core.system_env import system_env_set, system_env_get, system_env_delete

        key = "_TEST_OVERWRITE"
        system_env_set(key, "v1")
        system_env_set(key, "v2")
        assert system_env_get(key) == "v2"

        system_env_delete(key)


class TestApiKeyLookupOrder:
    """密钥查找顺序：进程环境变量 > 隔离存储 > None"""

    def test_reads_from_system_env_store(self) -> None:
        """隔离存储有值时直接返回。"""
        from app.core.system_env import system_env_set, system_env_delete
        from app.core.security import get_api_key

        key = "_TEST_LOOKUP_ORDER"
        system_env_set(key, "from_store")

        value, missing = get_api_key(key)
        assert value == "from_store"
        assert missing is False

        system_env_delete(key)

    def test_missing_key(self) -> None:
        """不存在时返回 None。"""
        from app.core.security import get_api_key

        value, missing = get_api_key("_DEFINITELY_NOT_SET_XYZ")
        assert value is None
        assert missing is True


class TestProviderSecretLifecycle:
    """Provider 密钥生命周期（创建 → 更新 → 删除）"""

    async def test_create_provider_writes_system_env(self, client) -> None:
        """创建 Provider 时 api_key_value 应写入系统环境变量。"""
        resp = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "lifecycle-create",
                "type": "openai_compatible",
                "api_key_env": "_TEST_LIFECYCLE_CREATE_KEY",
                "api_key_value": "sk-lifecycle-create-12345",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] is not None

        # 验证密钥已写入系统环境变量
        from app.core.security import get_api_key
        value, missing = get_api_key("_TEST_LIFECYCLE_CREATE_KEY")
        assert not missing
        assert value == "sk-lifecycle-create-12345"

    async def test_delete_provider_removes_system_env(self, client) -> None:
        """删除 Provider 时应移除系统环境变量中的密钥。"""
        resp = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "lifecycle-delete",
                "type": "openai_compatible",
                "api_key_env": "_TEST_LIFECYCLE_DELETE_KEY",
                "api_key_value": "sk-lifecycle-delete-67890",
            },
        )
        provider_id = resp.json()["data"]["id"]

        # 验证密钥已写入
        from app.core.security import get_api_key
        value, missing = get_api_key("_TEST_LIFECYCLE_DELETE_KEY")
        assert not missing
        assert value == "sk-lifecycle-delete-67890"

        # 删除
        resp = await client.delete(f"/api/v1/llm/providers/{provider_id}")
        assert resp.status_code == 200

        # 验证密钥已移除
        value, missing = get_api_key("_TEST_LIFECYCLE_DELETE_KEY")
        assert missing

    async def test_shared_env_name_not_deleted(self, client) -> None:
        """多个 Provider 共用同一 api_key_env 时，删除一个 Provider 不应移除该密钥。"""
        # 创建两个 Provider，共用同一 env name
        shared_env = "_TEST_SHARED_ENV_KEY"
        shared_value = "sk-shared-value-11111"

        resp1 = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "provider-a",
                "type": "openai_compatible",
                "api_key_env": shared_env,
                "api_key_value": shared_value,
            },
        )
        provider_a_id = resp1.json()["data"]["id"]

        resp2 = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "provider-b",
                "type": "openai_compatible",
                "api_key_env": shared_env,
                "api_key_value": shared_value,
            },
        )
        provider_b_id = resp2.json()["data"]["id"]

        # 删除 A
        resp = await client.delete(f"/api/v1/llm/providers/{provider_a_id}")
        assert resp.status_code == 200

        # 密钥应仍在（B 还在用）
        from app.core.security import get_api_key
        value, missing = get_api_key(shared_env)
        assert not missing
        assert value == shared_value

        # 删除 B
        resp = await client.delete(f"/api/v1/llm/providers/{provider_b_id}")
        assert resp.status_code == 200

        # 现在密钥应该被移除
        value, missing = get_api_key(shared_env)
        assert missing

    async def test_clear_api_key_removes_system_env(self, client) -> None:
        """更新 Provider 时 clear_api_key=true 应移除系统环境变量中的密钥。"""
        resp = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "clear-apikey",
                "type": "openai_compatible",
                "api_key_env": "_TEST_CLEAR_KEY",
                "api_key_value": "sk-clear-22222",
            },
        )
        provider_id = resp.json()["data"]["id"]

        # 验证密钥已写入
        from app.core.security import get_api_key
        value, missing = get_api_key("_TEST_CLEAR_KEY")
        assert not missing
        assert value == "sk-clear-22222"

        # 清空密钥
        resp = await client.patch(
            f"/api/v1/llm/providers/{provider_id}",
            json={"clear_api_key": True},
        )
        assert resp.status_code == 200

        # 验证密钥已移除
        value, missing = get_api_key("_TEST_CLEAR_KEY")
        assert missing

    async def test_rename_env_leaves_no_orphan(self, client) -> None:
        """修改 api_key_env 名称时，旧名称应从系统环境变量移除。"""
        old_env = "_TEST_OLD_ENV_NAME"
        new_env = "_TEST_NEW_ENV_NAME"
        value = "sk-rename-33333"

        resp = await client.post(
            "/api/v1/llm/providers",
            json={
                "name": "rename-env",
                "type": "openai_compatible",
                "api_key_env": old_env,
                "api_key_value": value,
            },
        )
        provider_id = resp.json()["data"]["id"]

        # 验证旧密钥已写入
        from app.core.security import get_api_key
        value_old, missing_old = get_api_key(old_env)
        assert not missing_old
        assert value_old == value

        # 改名
        resp = await client.patch(
            f"/api/v1/llm/providers/{provider_id}",
            json={"api_key_env": new_env, "api_key_value": value},
        )
        assert resp.status_code == 200

        # 验证旧密钥已移除，新密钥已写入
        value_old, missing_old = get_api_key(old_env)
        assert missing_old, "旧名称不应残留"
        value_new, missing_new = get_api_key(new_env)
        assert not missing_new
        assert value_new == value

        # 清理
        await client.delete(f"/api/v1/llm/providers/{provider_id}")
