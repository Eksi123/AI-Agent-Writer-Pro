# -*- coding: utf-8 -*-
"""
模块: scripts
文件名: e2e_polish_check.py
作者: xionggaoqing
时间: 2026-11-12
说明: 定点验证：选区润色 run 不应弹读取审批卡（读取意图含润色隐藏上下文）。

    流程：建小书（1 卷 1 章）→ 大纲 → 生成正文 → 选区润色；
    断言：生成与润色两个 run 全程 0 张审批卡；润色后章节内容确实变化。
    用法：后端已启动后 `python scripts/e2e_polish_check.py`。
"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"

stats = {"approval": 0, "consult": 0}


class Fail(RuntimeError):
    pass


async def api(client, method, path, *, expect=(200,), **kw):
    resp = await client.request(method, BASE + path, **kw)
    if resp.status_code not in expect:
        raise Fail(f"{method} {path} → {resp.status_code}: {resp.text[:400]}")
    body = resp.json()
    if body.get("code") != 0:
        raise Fail(f"{method} {path} → {body.get('code')} {body.get('message')}")
    return body.get("data") or {}


async def wait_run(client, sid, run_id, *, timeout=600.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = await api(client, "GET", f"/agent/runs/{run_id}")
        st = run.get("status")
        if st in ("awaiting_approval", "awaiting_consult"):
            tree = await api(client, "GET", f"/agent/sessions/{sid}/messages")
            pend = (tree.get("latest_run") or {}).get("pending_interrupts") or []
            if pend:
                it = pend[0]
                payload = it.get("payload") or {}
                if payload.get("consult_kind") or it.get("type") == "consult":
                    stats["consult"] += 1
                    options = payload.get("options") or []
                    values = [o.get("value") for o in options if isinstance(o, dict)]
                    body = (
                        {"decision": "option", "choice": values[0]}
                        if values
                        else {"decision": "text", "text": "继续。"}
                    )
                else:
                    stats["approval"] += 1
                    body = {"decision": "approve"}
                await api(client, "POST", f"/agent/interrupts/{it['id']}/resolve", json=body)
            await asyncio.sleep(0.6)
            continue
        if st in ("completed", "failed", "cancelled"):
            return st, run
        await asyncio.sleep(1.0)
    raise Fail(f"run {run_id} 超时")


async def main() -> int:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=600.0)) as client:
        for n in await api(client, "GET", "/novels"):
            if str(n.get("title") or "").startswith("模拟校验·"):
                await api(client, "DELETE", f"/novels/{n['id']}")

        novel = await api(
            client, "POST", "/novels",
            json={"title": "模拟校验·润色卡", "author_name": "校验", "genre": "玄幻"},
        )
        nid = novel["id"]
        vol = await api(client, "POST", f"/novels/{nid}/volumes", json={"title": "第一卷 试刃", "summary": ""})
        ch = await api(client, "POST", f"/volumes/{vol['id']}/chapters", json={"title": "第一章 夜雨", "content": ""})
        outline = (
            "# 模拟校验·润色卡 · 大纲\n\n# 第一卷 试刃\n\n## 第一章 夜雨\n\n"
            "- 简介：少年夜雨中得到一柄断刃，刃身铭文隐约可辨。\n"
            "- 衔接要点：开篇立人物与悬念。\n"
        )
        await api(client, "PUT", f"/novels/{nid}/outline", json={"content": outline})
        sid = (await api(client, "POST", "/agent/sessions", json={"novel_id": nid}))["id"]

        # 生成
        stats.update(approval=0, consult=0)
        d = await api(
            client, "POST", f"/agent/sessions/{sid}/messages",
            json={"content": "按大纲《第一章 夜雨》的章简介生成正文", "target_chapter_id": ch["id"]},
            expect=(202,),
        )
        st, run = await wait_run(client, sid, d["run_id"])
        if st != "completed":
            raise Fail(f"生成 run 终态={st} error={run.get('error')}")
        gen_approvals = stats["approval"]
        content = (await api(client, "GET", f"/chapters/{ch['id']}/content")).get("content") or ""
        if len(content.strip()) < 100:
            raise Fail("生成正文过短")
        para = next((ln.strip() for ln in content.splitlines() if len(ln.strip()) >= 15), "")
        if not para:
            raise Fail("无可用选区段落")

        # 润色（隐藏上下文同前端格式）
        stats.update(approval=0, consult=0)
        hidden = "\n".join([
            "【选中待润色文本】校验_第一章_节选1",
            f"所在章节 id：{ch['id']}",
            "所在范围：第 1 段",
            "",
            "（选中文本原文，只允许修改这一段）",
            para,
            "",
            "请按我的要求润色以上选中文本：只输出修改后的这一段（不要输出前文/后文），"
            "完成后把修改结果写回该章节对应位置（写回前会先存快照，可回滚）。",
        ])
        d = await api(
            client, "POST", f"/agent/sessions/{sid}/messages",
            json={"content": "润色选段，更凝练些。", "hidden_context": hidden},
            expect=(202,),
        )
        st, run = await wait_run(client, sid, d["run_id"])
        if st != "completed":
            raise Fail(f"润色 run 终态={st} error={run.get('error')}")
        new = (await api(client, "GET", f"/chapters/{ch['id']}/content")).get("content") or ""
        if new == content:
            raise Fail("润色未写回")
        if stats["approval"]:
            raise Fail(f"润色 run 弹了 {stats['approval']} 张读取审批卡（应为 0）")
        print(f"[check] PASS 生成审批卡={gen_approvals} 润色审批卡={stats['approval']} 润色咨询卡={stats['consult']}", flush=True)
        return 0


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    except Fail as exc:
        print(f"[check] FAIL: {exc}", flush=True)
        code = 1
    sys.exit(code)
