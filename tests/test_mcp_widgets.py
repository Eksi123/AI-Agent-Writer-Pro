# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_mcp_widgets.py
作者: xionggaoqing
时间: 2026-10-27
说明: M6 测试：天气服务（mock HTTP）、MCP 管理（白名单 + 二次确认 + 审计）、widgets API。

    网络层全部 monkeypatch，不触网（手册「十一」：假数据驱动）。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.agent.more.mcp.adapter import STDIO_COMMAND_WHITELIST, validate_stdio_command
from app.agent.more.mcp.server import build_weather_server, weather_tools
from app.core.errors import BadRequestError, DependencyUnavailableError
from app.services import weather

# ---------------------------------------------------------------------------
# 天气服务（mock httpx）
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """按 URL 返回预设 payload 的假 httpx.AsyncClient。"""

    registry: dict[str, Any] = {}

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        for key, payload in _FakeClient.registry.items():
            if key in url:
                if isinstance(payload, Exception):
                    raise payload
                return _FakeResponse(payload)
        raise AssertionError(f"未预设的 URL：{url}")


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch):
    _FakeClient.registry = {}
    monkeypatch.setattr(weather.httpx, "AsyncClient", _FakeClient)
    weather._cache.clear()
    yield _FakeClient
    weather._cache.clear()


class TestWeatherService:
    async def test_geocode_success(self, fake_http):
        fake_http.registry["geocoding-api"] = {
            "results": [
                {
                    "name": "杭州市",
                    "country": "中国",
                    "admin1": "浙江省",
                    "latitude": 30.25,
                    "longitude": 120.17,
                    "timezone": "Asia/Shanghai",
                }
            ]
        }
        geo = await weather.geocode("杭州")
        assert geo["city"] == "杭州市"
        assert geo["latitude"] == 30.25

    async def test_geocode_not_found(self, fake_http):
        fake_http.registry["geocoding-api"] = {"results": []}

        with pytest.raises(DependencyUnavailableError):
            await weather.geocode("不存在城")

    async def test_geocode_empty_city(self, fake_http):

        with pytest.raises(DependencyUnavailableError):
            await weather.geocode("  ")

    async def test_current_weather_by_coords(self, fake_http):
        fake_http.registry["api.open-meteo.com"] = {
            "current": {
                "temperature_2m": 22.5,
                "relative_humidity_2m": 60,
                "apparent_temperature": 23.0,
                "weather_code": 1,
                "wind_speed_10m": 8.2,
                "time": "2026-10-27T12:00",
            }
        }
        data = await weather.current_weather_by_coords(30.25, 120.17, city_label="杭州")
        assert data["temperature"] == 22.5
        assert data["description"] == "大部晴朗"
        assert data["icon"] == "🌤️"
        assert data["city"] == "杭州"

    async def test_current_weather_by_city_chains(self, fake_http):
        fake_http.registry["geocoding-api"] = {
            "results": [{"name": "北京", "latitude": 39.9, "longitude": 116.4, "timezone": "auto"}]
        }
        fake_http.registry["api.open-meteo.com"] = {"current": {"temperature_2m": 10, "weather_code": 61, "time": "t"}}
        data = await weather.current_weather("北京")
        assert data["description"] == "小雨"
        assert data["city"] == "北京"

    async def test_weather_http_error_degrades(self, fake_http):

        fake_http.registry["api.open-meteo.com"] = httpx.ConnectError("网络不可达")

        with pytest.raises(DependencyUnavailableError):
            await weather.current_weather_by_coords(1, 1)

    async def test_cache_hit_skips_http(self, fake_http):
        fake_http.registry["api.open-meteo.com"] = {"current": {"temperature_2m": 20, "weather_code": 0, "time": "t"}}
        first = await weather.current_weather_by_coords(10, 10)
        # 破坏 registry，缓存命中则不会触网
        fake_http.registry["api.open-meteo.com"] = AssertionError("不应触网")
        second = await weather.current_weather_by_coords(10, 10)
        assert first == second

    async def test_reverse_geocode_failure_returns_none(self, fake_http):

        fake_http.registry["bigdatacloud"] = httpx.ConnectError("x")
        assert await weather.reverse_geocode(30.0, 120.0) is None

    async def test_reverse_geocode_success(self, fake_http):
        fake_http.registry["bigdatacloud"] = {"city": "杭州市", "locality": "西湖区"}
        result = await weather.reverse_geocode(30.25, 120.17)
        assert result is not None and result["city"] == "杭州市"

    async def test_forecast(self, fake_http):
        fake_http.registry["geocoding-api"] = {
            "results": [{"name": "上海", "latitude": 31.2, "longitude": 121.5, "timezone": "auto"}]
        }
        fake_http.registry["api.open-meteo.com"] = {
            "daily": {
                "time": ["2026-10-27", "2026-10-28"],
                "weather_code": [0, 63],
                "temperature_2m_max": [25, 20],
                "temperature_2m_min": [15, 12],
                "precipitation_probability_max": [0, 80],
            }
        }
        data = await weather.forecast("上海", days=2)
        assert len(data["days"]) == 2
        assert data["days"][0]["description"] == "晴"
        assert data["days"][1]["description"] == "中雨"
        assert data["days"][1]["temp_max"] == 20

    async def test_current_weather_includes_aqi(self, fake_http):
        """天气数据带空气质量；取 european_aqi（中国 AQI 口径）。"""
        fake_http.registry["air-quality-api"] = {
            "current": {"european_aqi": 42, "pm2_5": 12.5, "pm10": 20.0, "time": "t"}
        }
        fake_http.registry["api.open-meteo.com"] = {
            "current": {"temperature_2m": 20, "weather_code": 0, "time": "t"}
        }
        data = await weather.current_weather_by_coords(30.25, 120.17, city_label="杭州")
        assert data["aqi"] == 42
        assert data["aqi_level"] == "优"

    async def test_aqi_failure_does_not_break_weather(self, fake_http):
        """空气质量接口异常时天气主数据照常返回（aqi 为空）。"""

        fake_http.registry["air-quality-api"] = httpx.ConnectError("AQI 不可达")
        fake_http.registry["api.open-meteo.com"] = {
            "current": {"temperature_2m": 18, "weather_code": 2, "time": "t"}
        }
        data = await weather.current_weather_by_coords(31.2, 121.5)
        assert data["temperature"] == 18
        assert data["aqi"] is None

    def test_aqi_level_scale(self):
        assert weather.aqi_level(None) == ""
        assert weather.aqi_level(50) == "优"
        assert weather.aqi_level(100) == "良"
        assert weather.aqi_level(150) == "轻度污染"
        assert weather.aqi_level(200) == "中度污染"
        assert weather.aqi_level(300) == "重度污染"
        assert weather.aqi_level(500) == "严重污染"

    def test_describe_weathercode(self):
        assert weather.describe_weathercode(0) == ("晴", "☀️")
        assert weather.describe_weathercode(95) == ("雷暴", "⛈️")
        assert weather.describe_weathercode(None)[0] == "未知"
        assert weather.describe_weathercode(12345)[0].startswith("代码")

    def test_wind_level_scale(self):
        """组件风速以蒲福风力「级」为单位。"""
        assert weather.wind_level(None) is None
        assert weather.wind_level(0.5) == 0
        assert weather.wind_level(8.2) == 2
        assert weather.wind_level(30) == 5
        assert weather.wind_level(130) == 12

    async def test_current_weather_includes_wind_level(self, fake_http):
        """天气数据带风力等级字段。"""
        fake_http.registry["air-quality-api"] = {"current": {"european_aqi": 30, "time": "t"}}
        fake_http.registry["api.open-meteo.com"] = {
            "current": {"temperature_2m": 20, "weather_code": 0, "wind_speed_10m": 8.2, "time": "t"}
        }
        data = await weather.current_weather_by_coords(30.25, 120.17, city_label="杭州")
        assert data["wind_level"] == 2

    async def test_reverse_geocode_builds_address(self, fake_http):
        """反向地理编码给出「具体地址」（跳过国家、去掉与城市重复的片段）。"""
        fake_http.registry["bigdatacloud"] = {
            "city": "杭州市",
            "locality": "西湖区",
            "principalSubdivision": "浙江省",
            "localityInfo": {
                "administrative": [
                    {"name": "中国", "adminLevel": 2, "order": 1},
                    {"name": "浙江省", "adminLevel": 4, "order": 2},
                    {"name": "杭州市", "adminLevel": 6, "order": 3},
                    {"name": "西湖区", "adminLevel": 8, "order": 4},
                ]
            },
        }
        result = await weather.reverse_geocode(30.25, 120.17)
        assert result is not None
        assert result["city"] == "杭州市"
        assert result["address"] == "浙江省 西湖区"
        assert "中国" not in result["address"]

    async def test_client_follows_redirects(self):
        """回归：BigDataCloud 已改为 307 跳转到 api-bdc.io。

        httpx 默认**不**跟随重定向，若 `_client()` 丢了 `follow_redirects=True`，
        `raise_for_status()` 会因 3xx 抛错 → 「具体地址」永远为空。
        """
        client = weather._client()
        try:
            assert client.follow_redirects is True
        finally:
            await client.aclose()

    async def test_current_weather_by_city_has_address(self, fake_http):
        """城市名路径也要有「具体地址」（反向地理编码 geocode 出的坐标）。"""
        fake_http.registry["geocoding-api"] = {
            "results": [{"name": "杭州", "admin1": "浙江省", "latitude": 30.25, "longitude": 120.17}]
        }
        fake_http.registry["bigdatacloud"] = {
            "city": "杭州市",
            "principalSubdivision": "浙江省",
            "locality": "西湖区",
        }
        fake_http.registry["api.open-meteo.com"] = {
            "current": {"temperature_2m": 20, "weather_code": 0, "wind_speed_10m": 8.2, "time": "t"}
        }
        fake_http.registry["air-quality-api"] = {"current": {"european_aqi": 40}}

        data = await weather.current_weather("杭州")
        assert data["city"] == "杭州"
        assert "西湖区" in data["address"]

    async def test_current_weather_address_failure_does_not_break(self, fake_http):
        """地址是锦上添花：反向地理编码挂了也**必须**返回天气主数据（回落 admin1）。"""

        fake_http.registry["geocoding-api"] = {
            "results": [{"name": "杭州", "admin1": "浙江省", "latitude": 30.25, "longitude": 120.17}]
        }
        fake_http.registry["bigdatacloud"] = httpx.ConnectError("offline")
        fake_http.registry["api.open-meteo.com"] = {
            "current": {"temperature_2m": 20, "weather_code": 0, "wind_speed_10m": 8.2, "time": "t"}
        }
        fake_http.registry["air-quality-api"] = {"current": {"european_aqi": 40}}

        data = await weather.current_weather("杭州")
        assert data["temperature"] == 20
        assert data["address"] == "浙江省"  # admin1 回落

    async def test_current_weather_dedups_municipality_address(self, fake_http):
        """直辖市：省名与城市名重合（北京/北京市）→ 地址不重复展示城市名。"""

        fake_http.registry["geocoding-api"] = {
            "results": [{"name": "北京", "admin1": "北京市", "latitude": 39.9, "longitude": 116.4}]
        }
        fake_http.registry["bigdatacloud"] = httpx.ConnectError("offline")
        fake_http.registry["api.open-meteo.com"] = {
            "current": {"temperature_2m": 18, "weather_code": 0, "wind_speed_10m": 8.2, "time": "t"}
        }
        fake_http.registry["air-quality-api"] = {"current": {"european_aqi": 40}}

        data = await weather.current_weather("北京")
        assert data["city"] == "北京"
        assert data["address"] == ""


# ---------------------------------------------------------------------------
# stdio 白名单
# ---------------------------------------------------------------------------


class TestStdioWhitelist:
    def test_whitelisted_commands(self):
        for cmd in STDIO_COMMAND_WHITELIST:
            c, _a = validate_stdio_command(cmd, ["-y", "some-server"])
            assert c == cmd

    @pytest.mark.parametrize(
        "cmd",
        ["powershell", "cmd", "bash", "node", "/usr/bin/npx", "C:\\tools\\uvx.exe", "..\\npx", "npx.exe"],
    )
    def test_rejected_commands(self, cmd):
        with pytest.raises(BadRequestError):
            validate_stdio_command(cmd, [])

    def test_empty_command(self):
        with pytest.raises(BadRequestError):
            validate_stdio_command("", [])

    def test_args_preserved(self):
        _c, a = validate_stdio_command("npx", ["-y", "@modelcontextprotocol/server-weather"])
        assert a == ["-y", "@modelcontextprotocol/server-weather"]


# ---------------------------------------------------------------------------
# MCP 管理 API
# ---------------------------------------------------------------------------


class TestMcpApi:
    async def test_create_stdio_needs_confirm(self, client):
        resp = await client.post(
            "/api/v1/mcp-servers",
            json={
                "name": "my-stdio",
                "transport": "stdio",
                "command": {"command": "npx", "args": ["-y", "some-mcp"]},
            },
        )
        assert resp.json()["code"] == 0
        dto = resp.json()["data"]
        assert dto["needs_confirm"] is True
        assert dto["enabled"] is False
        assert dto["confirmed_at"] is None

        # 未确认不能启用
        resp = await client.patch(f"/api/v1/mcp-servers/{dto['id']}", json={"enabled": True})
        assert resp.json()["code"] == 40001

        # 确认后启用
        resp = await client.post(f"/api/v1/mcp-servers/{dto['id']}/confirm")
        assert resp.json()["data"]["confirmed_at"] is not None
        resp = await client.patch(f"/api/v1/mcp-servers/{dto['id']}", json={"enabled": True})
        assert resp.json()["code"] == 0
        assert resp.json()["data"]["enabled"] is True

    async def test_create_stdio_rejects_non_whitelist(self, client):
        resp = await client.post(
            "/api/v1/mcp-servers",
            json={"name": "evil", "transport": "stdio", "command": {"command": "powershell", "args": ["-c", "rm"]}},
        )
        assert resp.json()["code"] == 40001

    async def test_create_stdio_rejects_path_command(self, client):
        resp = await client.post(
            "/api/v1/mcp-servers",
            json={"name": "evil2", "transport": "stdio", "command": {"command": "C:\\bin\\npx.exe", "args": []}},
        )
        assert resp.json()["code"] == 40001

    async def test_create_http_no_confirm_needed(self, client):
        resp = await client.post(
            "/api/v1/mcp-servers",
            json={"name": "remote", "transport": "http", "url": "https://example.com/mcp"},
        )
        assert resp.json()["code"] == 0
        dto = resp.json()["data"]
        assert dto["needs_confirm"] is False
        # http 型无需确认即可启用
        resp = await client.patch(f"/api/v1/mcp-servers/{dto['id']}", json={"enabled": True})
        assert resp.json()["code"] == 0

    async def test_invalid_transport(self, client):
        resp = await client.post("/api/v1/mcp-servers", json={"name": "x", "transport": "carrier-pigeon"})
        assert resp.status_code == 400

    async def test_http_requires_url(self, client):
        resp = await client.post("/api/v1/mcp-servers", json={"name": "x", "transport": "sse"})
        assert resp.json()["code"] == 40001

    async def test_duplicate_name(self, client):
        await client.post("/api/v1/mcp-servers", json={"name": "dup", "transport": "http", "url": "https://a.com/mcp"})
        resp = await client.post(
            "/api/v1/mcp-servers", json={"name": "dup", "transport": "http", "url": "https://b.com/mcp"}
        )
        assert resp.json()["code"] == 40001

    async def test_command_change_resets_confirm(self, client):
        resp = await client.post(
            "/api/v1/mcp-servers",
            json={"name": "mutate", "transport": "stdio", "command": {"command": "uvx", "args": ["a"]}},
        )
        pk = resp.json()["data"]["id"]
        await client.post(f"/api/v1/mcp-servers/{pk}/confirm")

        resp = await client.patch(
            f"/api/v1/mcp-servers/{pk}", json={"command": {"command": "uvx", "args": ["b"]}}
        )
        assert resp.json()["data"]["confirmed_at"] is None
        assert resp.json()["data"]["needs_confirm"] is True

    async def test_delete(self, client):
        resp = await client.post(
            "/api/v1/mcp-servers", json={"name": "bye", "transport": "http", "url": "https://a.com/mcp"}
        )
        pk = resp.json()["data"]["id"]
        resp = await client.delete(f"/api/v1/mcp-servers/{pk}")
        assert resp.json()["data"]["deleted"] is True

    async def test_confirm_full_command_audited(self, client):
        """stdio 命令全文落审计（安全边界要求）。"""
        resp = await client.post(
            "/api/v1/mcp-servers",
            json={"name": "audited", "transport": "stdio", "command": {"command": "npx", "args": ["-y", "secret-mcp"]}},
        )
        pk = resp.json()["data"]["id"]
        await client.post(f"/api/v1/mcp-servers/{pk}/confirm")

        logs = (await client.get("/api/v1/audit-logs", params={"action": "mcp.confirm"})).json()["data"]
        assert logs["total"] >= 1
        detail = logs["items"][0]["detail"]
        assert detail["command_full"] == "npx -y secret-mcp"

    async def test_probe_unknown(self, client):
        resp = await client.post("/api/v1/mcp-servers/nope/probe")
        assert resp.json()["code"] == 40401

    async def test_confirm_http_rejected(self, client):
        resp = await client.post(
            "/api/v1/mcp-servers", json={"name": "noconfirm", "transport": "http", "url": "https://a.com/mcp"}
        )
        pk = resp.json()["data"]["id"]
        resp = await client.post(f"/api/v1/mcp-servers/{pk}/confirm")
        assert resp.json()["code"] == 40001

    async def test_builtin_weather_tools_available(self, client):
        """内置天气 MCP 工具（LangChain tool 形式）齐备且 schema 正确。"""

        tools = weather_tools()
        assert {t.name for t in tools} == {"get_current_weather", "get_forecast"}

        server = build_weather_server()
        mcp_tools = await server.list_tools()
        assert {t.name for t in mcp_tools} == {"get_current_weather", "get_forecast"}


# ---------------------------------------------------------------------------
# widgets API
# ---------------------------------------------------------------------------


class TestWidgetsApi:
    async def test_weather_by_city(self, client, fake_http):
        fake_http.registry["geocoding-api"] = {
            "results": [{"name": "杭州", "latitude": 30.25, "longitude": 120.17, "timezone": "auto"}]
        }
        fake_http.registry["api.open-meteo.com"] = {"current": {"temperature_2m": 21, "weather_code": 2, "time": "t"}}
        resp = await client.get("/api/v1/widgets/weather", params={"city": "杭州"})
        data = resp.json()["data"]
        assert data["temperature"] == 21
        assert data["description"] == "多云"

    async def test_weather_by_coords_with_reverse_geocode(self, client, fake_http):
        fake_http.registry["bigdatacloud"] = {"city": "定位城"}
        fake_http.registry["api.open-meteo.com"] = {"current": {"temperature_2m": 18, "weather_code": 0, "time": "t"}}
        resp = await client.get("/api/v1/widgets/weather", params={"latitude": 30.1, "longitude": 120.2})
        data = resp.json()["data"]
        assert data["city"] == "定位城"

    async def test_weather_by_coords_carries_address(self, client, fake_http):
        """组件天气数据带反向地理编码得到的具体地址。"""
        fake_http.registry["bigdatacloud"] = {"city": "定位城", "principalSubdivision": "某省", "locality": "某区"}
        fake_http.registry["api.open-meteo.com"] = {
            "current": {"temperature_2m": 18, "weather_code": 0, "wind_speed_10m": 15, "time": "t"}
        }
        resp = await client.get("/api/v1/widgets/weather", params={"latitude": 30.1, "longitude": 120.2})
        data = resp.json()["data"]
        assert data["city"] == "定位城"
        assert data["address"] == "某省 某区"
        assert data["wind_level"] == 3

    async def test_weather_falls_back_to_settings_city(self, client, fake_http):
        """无坐标无城市 → 回落通用设置的回落城市；source=fallback_city。"""
        await client.put("/api/v1/settings", json={"city": "回落市"})
        fake_http.registry["geocoding-api"] = {
            "results": [{"name": "回落市", "latitude": 1, "longitude": 2, "timezone": "auto"}]
        }
        fake_http.registry["api.open-meteo.com"] = {"current": {"temperature_2m": 5, "weather_code": 71, "time": "t"}}
        resp = await client.get("/api/v1/widgets/weather")
        data = resp.json()["data"]
        assert data["city"] == "回落市"
        assert data["description"] == "小雪"
        assert data["source"] == "fallback_city"

    async def test_weather_source_city_and_located(self, client, fake_http):
        """显式 city → source=city；带坐标 → source=located。"""
        fake_http.registry["geocoding-api"] = {
            "results": [{"name": "杭州", "latitude": 30.25, "longitude": 120.17, "timezone": "auto"}]
        }
        fake_http.registry["bigdatacloud"] = {"city": "杭州"}
        fake_http.registry["api.open-meteo.com"] = {"current": {"temperature_2m": 21, "weather_code": 2, "time": "t"}}
        data = (await client.get("/api/v1/widgets/weather", params={"city": "杭州"})).json()["data"]
        assert data["source"] == "city"
        data = (
            await client.get("/api/v1/widgets/weather", params={"latitude": 30.1, "longitude": 120.2})
        ).json()["data"]
        assert data["source"] == "located"

    async def test_city_reverse_geocode(self, client, fake_http):
        fake_http.registry["bigdatacloud"] = {"city": "某市"}
        resp = await client.get("/api/v1/widgets/city", params={"latitude": 30, "longitude": 120})
        data = resp.json()["data"]
        assert data["city"] == "某市"
        assert data["source"] == "reverse_geocode"

    async def test_city_fallback(self, client, fake_http):

        fake_http.registry["bigdatacloud"] = httpx.ConnectError("x")
        await client.put("/api/v1/settings", json={"city": "兜底城"})
        resp = await client.get("/api/v1/widgets/city", params={"latitude": 30, "longitude": 120})
        data = resp.json()["data"]
        assert data["city"] == "兜底城"
        assert data["source"] == "fallback"

    async def test_pet_state_idle_by_default(self, client):
        resp = await client.get("/api/v1/widgets/pet-state")
        data = resp.json()["data"]
        assert data["state"] == "idle"

    async def test_pet_state_writing_after_chapter_write(self, client, novel_id, volume_id):
        """写正文后 project_index.last_updated 刷新 → 宠物 writing 态。"""
        resp = await client.post(
            f"/api/v1/volumes/{volume_id}/chapters", json={"title": "宠物测试章", "content": "内容"}
        )
        ch = resp.json()["data"]["id"]
        content = await client.get(f"/api/v1/chapters/{ch}/content")
        version = content.json()["data"]["version"]
        await client.put(
            f"/api/v1/chapters/{ch}/content",
            json={"content": "少年写下第一行字。", "base_version": version},
        )

        resp = await client.get("/api/v1/widgets/pet-state", params={"novel_id": novel_id})
        data = resp.json()["data"]
        assert data["state"] == "writing"
        assert data["last_updated"] is not None

    async def test_pet_state_idle_for_unknown_novel(self, client):
        resp = await client.get("/api/v1/widgets/pet-state", params={"novel_id": "no-such"})
        assert resp.json()["data"]["state"] == "idle"

