# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_ask_user.py
作者: xionggaoqing
时间: 2026-11-06
说明: `ask_user` 多问题卡片契约（AI 思考中间过程询问）：

    - 工具调用 → 落 consult 中断 + 发 `consult.request`（带 questions）+ run 暂停；
    - resolve（decision=option，choice=答案清单 JSON）→ resume 续跑，答案回灌模型；
    - 空问题清单不弹卡（直接返回错误 JSON，run 正常完成）；
    - 归一化 / 答案对齐的纯函数单测。
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage

from helpers import (
    drain_events,
    install_fake,
    new_session,
    post_message,
    setup_provider_model,
    wait_status,
)

from app.agent.tools.builtin.ask_user import _answers_of, _normalize_questions


def _ask_call(questions, call_id: str = "call_ask") -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": "ask_user", "args": {"questions": questions}, "id": call_id}]
    )


async def _tree(client, session_id: str) -> dict:
    resp = await client.get(f"/api/v1/agent/sessions/{session_id}/messages")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestAskUserCard:
    async def test_ask_user_pauses_then_resume_continues(self, client, novel_id):
        """弹卡暂停 → 提交全部答案 → 续跑完成，答案回灌模型第二轮调用。"""
        _, model_ref = await setup_provider_model(client)
        questions = [
            {
                "question": "删除范围需要明确：删哪些部分？",
                "header": "范围",
                "options": [{"label": "全部删除"}, {"label": "仅正文，保留大纲"}],
            },
            {"question": "删除理由需要说明", "options": []},
        ]
        fake = install_fake(model_ref, [_ask_call(questions), AIMessage(content="已按确认执行。")])
        sid = await new_session(client, novel_id)
        run_id = await post_message(client, sid, "帮我删除第二卷，先和我确认几件事")

        events = await drain_events(run_id)
        run = await wait_status(client, run_id, ("awaiting_consult", "completed", "failed"))
        assert run["status"] == "awaiting_consult"

        # SSE 事件带 questions 清单
        req = next(e for e in events if e["event"] == "consult.request")
        assert len(req["data"]["questions"]) == 2

        tree = await _tree(client, sid)
        pending = tree["latest_run"]["pending_interrupts"]
        assert len(pending) == 1 and pending[0]["type"] == "consult"
        assert pending[0]["tool_name"] == "ask_user"
        payload = pending[0]["payload"]
        assert payload["kind"] == "ask_user"

        # 提交：decision=option，choice = 与 questions 对齐的答案清单 JSON
        answers = [
            {"selected": ["全部删除"], "text": ""},
            {"selected": [], "text": "剧情方向调整"},
        ]
        resp = await client.post(
            f"/api/v1/agent/interrupts/{pending[0]['id']}/resolve",
            json={"decision": "option", "choice": json.dumps(answers, ensure_ascii=False)},
        )
        assert resp.status_code == 200, resp.text

        await drain_events(run_id)
        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "completed"

        # 第二轮模型调用的消息里含工具结果（答案 JSON）
        assert len(fake.calls) == 2
        second = [str(getattr(m, "content", "")) for m in fake.calls[1]]
        assert any("剧情方向调整" in c for c in second)
        assert any("全部删除" in c for c in second)

    async def test_ask_user_empty_questions_does_not_pause(self, client, novel_id):
        """空问题清单 → 不弹卡不暂停，工具返回错误 JSON，run 正常完成。"""
        _, model_ref = await setup_provider_model(client)
        fake = install_fake(model_ref, [_ask_call([]), AIMessage(content="没事了。")])
        sid = await new_session(client, novel_id)
        run_id = await post_message(client, sid, "随便问问")
        await drain_events(run_id)
        run = await wait_status(client, run_id, ("completed", "failed"))
        assert run["status"] == "completed"
        tree = await _tree(client, sid)
        assert tree["latest_run"]["pending_interrupts"] == []
        assert len(fake.calls) == 2


class TestAskUserPure:
    def test_normalize_questions_tolerant(self):
        out = _normalize_questions(
            [
                {"question": "q1", "options": [{"label": "a", "description": "d"}, "b", 7]},
                "q2",
                42,
                {"question": "  "},
            ]
        )
        assert [q["question"] for q in out] == ["q1", "q2"]
        assert [o["label"] for o in out[0]["options"]] == ["a", "b"]
        assert out[0]["options"][0]["description"] == "d"
        assert out[1]["options"] == []

    def test_normalize_questions_caps_at_eight(self):
        out = _normalize_questions([{"question": f"q{i}"} for i in range(12)])
        assert len(out) == 8

    def test_answers_of_aligns_and_tolerates(self):
        questions = _normalize_questions([{"question": "q1"}, {"question": "q2"}])
        good = {"decision": "option", "choice": json.dumps([{"selected": ["a"]}, {"text": "t"}])}
        answers = _answers_of(good, questions)
        assert answers[0]["selected"] == ["a"] and answers[1]["text"] == "t"

        # 坏 JSON → 空答案不崩；自由文本回落第一题
        bad = {"decision": "option", "choice": "not-json"}
        assert _answers_of(bad, questions) == [
            {"question": "q1", "header": "", "selected": [], "text": ""},
            {"question": "q2", "header": "", "selected": [], "text": ""},
        ]
        txt = {"decision": "text", "text": "自由回答"}
        assert _answers_of(txt, questions)[0]["text"] == "自由回答"
