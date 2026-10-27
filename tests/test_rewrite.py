# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_rewrite.py
作者: xionggaoqing
时间: 2026-10-27
说明: 选区 AI 改写测试（POST /chapters/{id}/rewrite，手册「三、(3)【正文】」选中浮层）。

    用假模型驱动：验证四种 mode、参数校验、上下文截断、包装剥离、审计埋点、不落盘。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from tests.test_agent_e2e import _setup_provider_model

from app.agent.llm.factory import register_model_override


class _EchoValidator:
    """假改写模型：返回带包裹的固定文本（验证 _strip_wrappers）。"""

    model_name = "fake-rewrite"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def ainvoke(self, messages: Any, *a: Any, **k: Any) -> AIMessage:
        self.prompts.append(str(messages[-1].content) if messages else "")
        return AIMessage(content=self.reply)


async def _install_rewrite_model(client, reply: str = "```改写后的正文```") -> tuple[str, _EchoValidator]:
    _pid, model_ref = await _setup_provider_model(client)
    fake = _EchoValidator(reply)
    register_model_override(model_ref, fake)
    return model_ref, fake


class TestRewriteApi:
    async def test_polish_returns_stripped_text(self, client, chapter_id):
        model_ref, fake = await _install_rewrite_model(client, "```润色后的文字```")
        resp = await client.post(
            f"/api/v1/chapters/{chapter_id}/rewrite",
            json={"selected_text": "少年走出了山村。", "mode": "polish", "model_ref": model_ref},
        )
        assert resp.json()["code"] == 0
        data = resp.json()["data"]
        assert data["rewritten"] == "润色后的文字"  # 代码块包裹被剥离
        assert data["original"] == "少年走出了山村。"
        assert data["mode"] == "polish"
        assert data["word_counts"]["original"] == 8
        # 提示词包含选区文本与模式要求
        assert "少年走出了山村。" in fake.prompts[0]
        assert "润色" in fake.prompts[0]

    @pytest.mark.parametrize("mode,label", [
        ("rewrite", "改写"), ("expand", "扩写"), ("condense", "缩写"), ("polish", "润色"),
    ])
    async def test_all_four_modes(self, client, chapter_id, mode, label):
        model_ref, fake = await _install_rewrite_model(client, "结果文本")
        resp = await client.post(
            f"/api/v1/chapters/{chapter_id}/rewrite",
            json={"selected_text": "一段正文", "mode": mode, "model_ref": model_ref},
        )
        assert resp.json()["code"] == 0, resp.text
        assert resp.json()["data"]["mode"] == mode
        assert label in fake.prompts[0]

    async def test_invalid_mode_rejected(self, client, chapter_id):
        model_ref, _ = await _install_rewrite_model(client)
        resp = await client.post(
            f"/api/v1/chapters/{chapter_id}/rewrite",
            json={"selected_text": "文字", "mode": "translate", "model_ref": model_ref},
        )
        assert resp.status_code == 400  # pydantic Literal → 40001

    async def test_empty_selection_rejected(self, client, chapter_id):
        resp = await client.post(
            f"/api/v1/chapters/{chapter_id}/rewrite",
            json={"selected_text": "", "mode": "polish"},
        )
        assert resp.status_code == 400

    async def test_oversize_selection_rejected(self, client, chapter_id):
        resp = await client.post(
            f"/api/v1/chapters/{chapter_id}/rewrite",
            json={"selected_text": "字" * 9000, "mode": "expand"},
        )
        assert resp.json()["code"] == 40001

    async def test_unknown_chapter_404(self, client):
        resp = await client.post(
            "/api/v1/chapters/deadbeef/rewrite",
            json={"selected_text": "文字", "mode": "polish"},
        )
        assert resp.json()["code"] == 40401

    async def test_context_and_instruction_in_prompt(self, client, chapter_id):
        model_ref, fake = await _install_rewrite_model(client, "结果")
        resp = await client.post(
            f"/api/v1/chapters/{chapter_id}/rewrite",
            json={
                "selected_text": "目标句。",
                "mode": "expand",
                "model_ref": model_ref,
                "context_before": "前文内容甲。",
                "context_after": "后文内容乙。",
                "instruction": "多写环境描写",
            },
        )
        assert resp.json()["code"] == 0
        prompt = fake.prompts[0]
        assert "前文内容甲。" in prompt
        assert "后文内容乙。" in prompt
        assert "多写环境描写" in prompt

    async def test_rewrite_does_not_touch_file(self, client, chapter_id):
        """改写只读不落盘：章节内容与 version 均不变。"""
        model_ref, _ = await _install_rewrite_model(client, "改写结果")
        before = (await client.get(f"/api/v1/chapters/{chapter_id}/content")).json()["data"]

        await client.post(
            f"/api/v1/chapters/{chapter_id}/rewrite",
            json={"selected_text": "旧内容", "mode": "rewrite", "model_ref": model_ref},
        )
        after = (await client.get(f"/api/v1/chapters/{chapter_id}/content")).json()["data"]
        assert before["content"] == after["content"]
        assert before["version"] == after["version"]

    async def test_rewrite_audited(self, client, chapter_id):
        model_ref, _ = await _install_rewrite_model(client, "改写结果")
        await client.post(
            f"/api/v1/chapters/{chapter_id}/rewrite",
            json={"selected_text": "旧内容", "mode": "condense", "model_ref": model_ref},
        )
        logs = (
            await client.get("/api/v1/audit-logs", params={"action": "chapter.rewrite"})
        ).json()["data"]
        assert logs["total"] >= 1
        assert logs["items"][0]["detail"]["mode"] == "condense"

    async def test_model_failure_readable_error(self, client, chapter_id):
        """LLM 调用失败 → 40001 + 可读错误（不静默）。"""
        _pid, model_ref = await _setup_provider_model(client)

        class _Boom:
            model_name = "boom"

            async def ainvoke(self, *a: Any, **k: Any) -> AIMessage:
                raise RuntimeError("上游超时")

        register_model_override(model_ref, _Boom())
        resp = await client.post(
            f"/api/v1/chapters/{chapter_id}/rewrite",
            json={"selected_text": "文字", "mode": "polish", "model_ref": model_ref},
        )
        assert resp.json()["code"] == 40001
        assert "上游超时" in resp.json()["message"]


class TestStripWrappers:
    def test_variants(self):
        from app.agent.llm.rewrite import _strip_wrappers

        assert _strip_wrappers("```\n正文\n```") == "正文"
        assert _strip_wrappers("```markdown\n正文\n```") == "正文"
        assert _strip_wrappers("改写后：正文") == "正文"
        assert _strip_wrappers("“正文”") == "正文"
        assert _strip_wrappers("  正文  ") == "正文"
        assert _strip_wrappers("") == ""
