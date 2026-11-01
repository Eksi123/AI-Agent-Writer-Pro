# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_gate_intent.py
作者: xionggaoqing
时间: 2026-11-01
说明: ★v1101 门禁意图检测（gates/intent.py）单测。

    门禁（四阶段工作流）不再有「生成本章」按钮：仅当用户消息表达
    「按大纲某章简介生成/修改正文」的意图时才触发；目标章解析优先级：
    显式章标题 > 「第X章」序数 > 当前章（active_chapter_id）。
"""

from __future__ import annotations

from app.agent.gates.intent import detect_gate_target, has_gate_intent

_CHAPTERS = [
    {"id": "c1", "title": "第一章 出山", "sort_order": 1},
    {"id": "c2", "title": "第二章 星海", "sort_order": 2},
    {"id": "c3", "title": "第三章 归途", "sort_order": 3},
]


async def _make_chapter(client, novel_id: str, vol_title: str, ch_title: str) -> str:
    vol = (
        await client.post(f"/api/v1/novels/{novel_id}/volumes", json={"title": vol_title, "summary": ""})
    ).json()["data"]
    ch = (
        await client.post(
            f"/api/v1/volumes/{vol['id']}/chapters", json={"title": ch_title, "content": "占位"}
        )
    ).json()["data"]
    return ch["id"]


class TestPostMessageGateWiring:
    """★v1101：post_message 意图接线——命中意图 → run 带 target_chapter_id（门禁运行）。"""

    async def test_intent_message_marks_gate_run(self, client, novel_id):
        from langchain_core.messages import AIMessage
        from tests.test_agent_e2e import _install_fake, _setup_provider_model

        _pid, model_ref = await _setup_provider_model(client)
        _install_fake(model_ref, [AIMessage(content='{"passed": true, "reason": "ok"}')])
        ch_id = await _make_chapter(client, novel_id, "第一卷 荒原", "第一章 出山")
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]

        resp = await client.post(
            f"/api/v1/agent/sessions/{sid}/messages",
            json={"content": "请按大纲「第一章 出山」的章简介生成正文"},
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()["data"]
        assert data["gate_target_chapter_id"] == ch_id

        run = (await client.get(f"/api/v1/agent/runs/{data['run_id']}")).json()["data"]
        assert run["target_chapter_id"] == ch_id
        await client.post(f"/api/v1/agent/runs/{data['run_id']}/cancel")

    async def test_plain_message_not_gate_run(self, client, novel_id):
        from langchain_core.messages import AIMessage
        from tests.test_agent_e2e import _install_fake, _setup_provider_model

        _pid, model_ref = await _setup_provider_model(client)
        _install_fake(model_ref, [AIMessage(content="好的")])
        await _make_chapter(client, novel_id, "第一卷 荒原", "第一章 出山")
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]

        resp = await client.post(
            f"/api/v1/agent/sessions/{sid}/messages",
            json={"content": "帮我聊聊这本书的主线"},
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()["data"]
        assert not data["gate_target_chapter_id"]
        await client.post(f"/api/v1/agent/runs/{data['run_id']}/cancel")


class TestHiddenContext:
    """★v1101：hidden_context 随本轮送给模型，但**不入消息树、不在会话展示**。"""

    async def test_hidden_context_not_in_message_tree(self, client, novel_id):
        from langchain_core.messages import AIMessage
        from tests.test_agent_e2e import _drain_events, _install_fake, _setup_provider_model

        _pid, model_ref = await _setup_provider_model(client)
        fake = _install_fake(model_ref, [AIMessage(content="已润色完成")])
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]

        resp = await client.post(
            f"/api/v1/agent/sessions/{sid}/messages",
            json={
                "content": "把这段改得更紧张一点",
                "hidden_context": "【选中待润色文本】秘密片段\n（选中文本原文，只允许修改这一段）\n原文内容甲",
            },
        )
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["data"]["run_id"]
        await _drain_events(run_id)

        # ① 模型**收到**了隐藏上下文（请求级 system 注入，name=hidden_ctx）
        assert fake.calls, "模型应至少被调用一次"
        hidden_msgs = [m for m in fake.calls[0] if getattr(m, "name", None) == "hidden_ctx"]
        assert hidden_msgs, "隐藏上下文应以 name=hidden_ctx 的 system 消息注入模型请求"
        assert "原文内容甲" in str(hidden_msgs[0].content)

        # ② 消息树里**没有**隐藏上下文（不展示、不落库）
        tree = (await client.get(f"/api/v1/agent/sessions/{sid}/messages")).json()["data"]
        texts = [str(i["content"]) for i in tree["items"]]
        assert any("把这段改得更紧张一点" in t for t in texts), "用户可见消息应入树"
        assert not any("选中待润色文本" in t for t in texts), "隐藏上下文不得入消息树"
        assert not any("原文内容甲" in t for t in texts)


class TestIntent:
    def test_generate_by_outline_hits(self):
        assert has_gate_intent("请根据大纲里第二章的章简介生成正文")
        assert has_gate_intent("按大纲「第三章 归途」的章简介写本章正文")
        assert has_gate_intent("根据大纲生成正文")
        assert has_gate_intent("用章简介重写这一章")

    def test_modify_by_outline_hits(self):
        assert has_gate_intent("按大纲第二章简介修改正文，多加环境描写")
        assert has_gate_intent("根据大纲把本章正文重写一遍")

    def test_plain_chat_not_gate(self):
        assert not has_gate_intent("帮我看看这段文字哪里不通顺")
        assert not has_gate_intent("总结一下这本书的主线")
        assert not has_gate_intent("写一段开场白给我参考")

    def test_outline_only_edit_not_gate(self):
        """只改大纲/简介（不碰正文）→ 不触发门禁。"""
        assert not has_gate_intent("修改大纲，把第二章简介写详细点")
        assert not has_gate_intent("帮我补充第三章的简介")
        # 但同时提到正文 → 仍算正文意图
        assert has_gate_intent("修改大纲简介后，按新简介重写本章正文")


class TestTargetResolve:
    def test_explicit_title_wins(self):
        cid = detect_gate_target("按大纲「第三章 归途」的章简介生成正文", _CHAPTERS, "c1")
        assert cid == "c3"

    def test_ordinal_mention(self):
        cid = detect_gate_target("根据大纲第二章简介生成正文", _CHAPTERS, "c1")
        assert cid == "c2"
        cid = detect_gate_target("根据大纲第2章简介生成正文", _CHAPTERS, "c1")
        assert cid == "c2"

    def test_fallback_active_chapter(self):
        cid = detect_gate_target("按大纲生成本章正文", _CHAPTERS, "c2")
        assert cid == "c2"

    def test_no_target_no_gate(self):
        """意图命中但既没点名也没当前章 → 不进门禁（普通对话）。"""
        assert detect_gate_target("按大纲生成正文", _CHAPTERS, None) is None

    def test_no_intent_returns_none(self):
        assert detect_gate_target("聊聊人物动机", _CHAPTERS, "c1") is None
