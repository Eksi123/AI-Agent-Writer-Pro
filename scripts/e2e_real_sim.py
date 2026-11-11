# -*- coding: utf-8 -*-
"""
模块: scripts
文件名: e2e_real_sim.py
作者: xionggaoqing
时间: 2026-11-11
说明: 真实开发环境端到端模拟测试（打真实大模型，不走假模型）。

    流程（每步校验执行到尾，失败即抛 SimFail 并打印诊断）：
      S1 创建小说（3 卷 × 5 章）
      S2 撰写大纲 → 校验 → 修改大纲再校验
      S3 按大纲逐章生成正文（四阶段流程，审批/咨询自动批准）
      S4 AI 润色第一章选区（隐藏上下文写回）
      S5 删除第二卷 → 重建 → 重写该卷五章
      S6 全书一致性体检（报告落盘校验）
      S7 导出 PDF（zip 产物内含 %PDF 校验）

    用法：先启动后端（python main.py），再 `python scripts/e2e_real_sim.py`。
"""

from __future__ import annotations

import asyncio
import io
import sys
import time
import zipfile
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
ROOT = Path(__file__).resolve().parents[1]

VOL_TITLES = ["第一卷 梦入山门", "第二卷 剑折沉沙", "第三卷 归帆拾星"]
CH_TITLES = [
    ["第一章 青灯夜", "第二章 试剑石", "第三章 山门雪", "第四章 旧剑谱", "第五章 离人歌"],
    ["第六章 沙海客", "第七章 断剑盟", "第八章 沉沙狱", "第九章 故人刀", "第十章 夜奔辞"],
    ["第十一章 星槎归", "第十二章 拾星者", "第十三章 灯下约", "第十四章 潮声旧", "第十五章 终章帆"],
]
INTROS = {
    "第一章 青灯夜": "少年沈拾在青灯下读亡父遗书，决意上山门求剑。",
    "第二章 试剑石": "沈拾于试剑石前露拙，被长老收为记名弟子。",
    "第三章 山门雪": "大雪封山，沈拾守藏经阁，初识剑谱残页。",
    "第四章 旧剑谱": "沈拾修补旧剑谱，悟出父辈剑意一脉。",
    "第五章 离人歌": "山门变故，师门遣散，沈拾带残谱下山。",
    "第六章 沙海客": "沈拾入沙海谋生，遇商队被劫，出手救人。",
    "第七章 断剑盟": "断剑盟招揽沈拾，他目睹盟中旧怨。",
    "第八章 沉沙狱": "沈拾被陷沉沙狱，与故人刀同囚。",
    "第九章 故人刀": "故人刀讲述当年山门变故真相一角。",
    "第十章 夜奔辞": "二人夜奔出狱，断剑盟覆灭，沈拾得断剑。",
    "第十一章 星槎归": "沈拾乘星槎归海，寻父辈旧航路。",
    "第十二章 拾星者": "拾星者 guild 认出残谱，欲夺之。",
    "第十三章 灯下约": "沈拾与故人灯下立约，共护剑谱。",
    "第十四章 潮声旧": "潮声旧港重逢师门遗孤，真相补齐。",
    "第十五章 终章帆": "沈拾焚谱立帆，剑意传人，故事收束。",
}


class SimFail(RuntimeError):
    """模拟步骤失败（携带诊断信息）。"""


def log(msg: str) -> None:
    print(f"[sim] {msg}", flush=True)


def step_banner(n: int, name: str) -> None:
    print(f"\n[sim] ===== 步骤 {n}: {name} =====", flush=True)


async def api(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    expect_status: tuple[int, ...] = (200,),
    **kw,
) -> dict:
    resp = await client.request(method, BASE + path, **kw)
    if resp.status_code not in expect_status:
        raise SimFail(f"{method} {path} → HTTP {resp.status_code}: {resp.text[:800]}")
    body = resp.json()
    if body.get("code") != 0:
        raise SimFail(f"{method} {path} → code={body.get('code')} message={body.get('message')}")
    return body.get("data") or {}


async def resolve_first_pending(client: httpx.AsyncClient, sid: str) -> str | None:
    """有未决审批/咨询时自动处理第一条，返回处理描述。"""
    tree = await api(client, "GET", f"/agent/sessions/{sid}/messages")
    pend = (tree.get("latest_run") or {}).get("pending_interrupts") or []
    if not pend:
        return None
    it = pend[0]
    iid = it["id"]
    payload = it.get("payload") or {}
    is_consult = bool(payload.get("consult_kind")) or it.get("type") == "consult"
    if is_consult:
        options = payload.get("options") or []
        values = [o.get("value") for o in options if isinstance(o, dict)]
        prefer = ("agent_write", "continue", "proceed", "force_skip", "regenerate", "skip")
        pick = next((v for v in prefer if v in values), values[0] if values else None)
        if pick:
            body = {"decision": "option", "choice": pick}
        else:
            body = {"decision": "text", "text": "按默认方案继续执行。"}
        desc = f"consult({payload.get('consult_kind')}) → {body}"
    else:
        body = {"decision": "approve"}
        desc = f"approval({payload.get('risk') or payload.get('tool_name')}) → approve"
    await api(client, "POST", f"/agent/interrupts/{iid}/resolve", json=body)
    return desc


async def wait_run(
    client: httpx.AsyncClient, sid: str, run_id: str, *, timeout: float = 900.0
) -> tuple[str, dict]:
    """轮询 run 至终态；期间自动批准审批/咨询。返回 (status, run)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = await api(client, "GET", f"/agent/runs/{run_id}")
        st = run.get("status")
        if st in ("awaiting_approval", "awaiting_consult"):
            desc = await resolve_first_pending(client, sid)
            if desc:
                log(f"    自动处理中断: {desc}")
            await asyncio.sleep(0.8)
            continue
        if st in ("completed", "failed", "cancelled"):
            return st, run
        await asyncio.sleep(1.2)
    raise SimFail(f"run {run_id} 等待终态超时（{timeout:.0f}s）")


async def chat(
    client: httpx.AsyncClient,
    sid: str,
    content: str,
    *,
    target_chapter_id: str | None = None,
    hidden_context: str | None = None,
    timeout: float = 900.0,
) -> tuple[str, dict]:
    """发消息并等到终态；非 completed 即失败（带 run error 诊断）。"""
    payload: dict = {"content": content}
    if target_chapter_id:
        payload["target_chapter_id"] = target_chapter_id
    if hidden_context:
        payload["hidden_context"] = hidden_context
    data = await api(client, "POST", f"/agent/sessions/{sid}/messages", json=payload, expect_status=(202,))
    run_id = data["run_id"]
    st, run = await wait_run(client, sid, run_id, timeout=timeout)
    if st != "completed":
        raise SimFail(f"run {run_id} 终态={st} error={run.get('error')}（消息：{content[:40]}）")
    return st, run


async def chapter_content(client: httpx.AsyncClient, cid: str) -> str:
    data = await api(client, "GET", f"/chapters/{cid}/content")
    return str(data.get("content") or "")


def build_outline() -> str:
    parts = ["# 模拟测试·三卷梦华录 · 大纲\n"]
    for vi, vt in enumerate(VOL_TITLES):
        parts.append(f"# {vt}\n")
        for ct in CH_TITLES[vi]:
            parts.append(f"## {ct}\n")
            parts.append(f"- 简介：{INTROS[ct]}\n")
            parts.append("- 衔接要点：承接上一章结尾，推进主线一步。\n")
    parts.append("# 附录A 世界观\n")
    parts.append("- 世界背景：架空山海大陆，剑意可传谱。\n")
    return "\n".join(parts)


async def generate_chapter(
    client: httpx.AsyncClient, sid: str, cid: str, title: str, *, tag: str
) -> str:
    t0 = time.time()
    await chat(client, sid, f"按大纲《{title}》的章简介生成正文", target_chapter_id=cid)
    content = await chapter_content(client, cid)
    if len(content.strip()) < 150:
        raise SimFail(f"{tag} 《{title}》生成后正文过短（{len(content.strip())} 字）")
    log(f"    {tag} 《{title}》生成完成：{len(content)} 字，耗时 {time.time() - t0:.0f}s")
    return content


async def main() -> int:
    results: list[tuple[str, str]] = []

    def done(name: str, detail: str = "") -> None:
        results.append((name, "PASS"))
        log(f"步骤通过: {name}{(' → ' + detail) if detail else ''}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=900.0)) as client:
        # ---- 清理历史模拟小说（保持书库干净） ----
        olds = await api(client, "GET", "/novels")
        for n in olds:
            if str(n.get("title") or "").startswith("模拟测试·三卷梦华录"):
                await api(client, "DELETE", f"/novels/{n['id']}")
                log(f"清理历史模拟小说: {n['id'][:8]}")

        # ---- 步骤 1：创建小说与卷章结构 ----
        step_banner(1, "创建小说（3 卷 × 5 章）")
        novel = await api(
            client,
            "POST",
            "/novels",
            json={"title": "模拟测试·三卷梦华录", "author_name": "模拟测试", "genre": "玄幻"},
        )
        nid = novel["id"]
        log(f"小说已创建: {nid}")
        vols: list[dict] = []
        chapters: list[list[dict]] = []
        for vi, vt in enumerate(VOL_TITLES):
            vol = await api(client, "POST", f"/novels/{nid}/volumes", json={"title": vt, "summary": f"{vt}简介"})
            vols.append(vol)
            row: list[dict] = []
            for ct in CH_TITLES[vi]:
                ch = await api(
                    client, "POST", f"/volumes/{vol['id']}/chapters", json={"title": ct, "content": ""}
                )
                row.append(ch)
            chapters.append(row)
        check = await api(client, "GET", f"/novels/{nid}/volumes")
        if len(check) != 3 or any(len(v.get("chapters") or []) != 5 for v in check):
            raise SimFail(f"卷章结构校验失败: {[ (v.get('title'), len(v.get('chapters') or [])) for v in check ]}")
        done("创建小说与卷章结构", f"novel={nid[:8]} 3卷×5章")

        # ---- 步骤 2：撰写大纲 + 修改大纲 ----
        step_banner(2, "撰写大纲并修改")
        outline_md = build_outline()
        await api(client, "PUT", f"/novels/{nid}/outline", json={"content": outline_md})
        val = await api(client, "GET", f"/novels/{nid}/outline/validate")
        if not val.get("ok") or val.get("chapter_count") != 15:
            raise SimFail(f"大纲校验失败: ok={val.get('ok')} chapters={val.get('chapter_count')} issues={val.get('issues')}")
        errs = [i for i in val.get("issues") or [] if i.get("level") == "error"]
        if errs:
            raise SimFail(f"大纲存在 error 级问题: {errs}")
        # 修改：第一章简介扩写
        new_intro = INTROS["第一章 青灯夜"] + "当夜灯花三落，似有故人叩门。"
        outline_v2 = outline_md.replace(
            f"- 简介：{INTROS['第一章 青灯夜']}", f"- 简介：{new_intro}"
        )
        if outline_v2 == outline_md:
            raise SimFail("大纲修改替换未命中")
        await api(client, "PUT", f"/novels/{nid}/outline", json={"content": outline_v2})
        saved = await api(client, "GET", f"/novels/{nid}/outline")
        if new_intro not in saved.get("content", ""):
            raise SimFail("大纲修改后未落盘")
        done("撰写/修改大纲", "validate ok, 15 章, 修改已落盘")

        # ---- 步骤 3：按大纲逐章生成正文 ----
        step_banner(3, "按大纲生成全书正文（15 章）")
        sid = (await api(client, "POST", "/agent/sessions", json={"novel_id": nid}))["id"]
        for vi in range(3):
            for ci in range(5):
                ch = chapters[vi][ci]
                await generate_chapter(
                    client, sid, ch["id"], CH_TITLES[vi][ci], tag=f"V{vi + 1}C{ci + 1}"
                )
        done("按大纲生成正文", "15 章全部 completed 且正文 ≥150 字")

        # ---- 步骤 4：AI 润色第一章选区 ----
        step_banner(4, "AI 润色第一章选区")
        c1 = chapters[0][0]
        old = await chapter_content(client, c1["id"])
        paras = [ln.strip() for ln in old.splitlines() if len(ln.strip()) >= 20]
        if not paras:
            raise SimFail("第一章无可用段落做选区")
        para = paras[0]
        hidden = "\n".join(
            [
                "【选中待润色文本】模拟测试_第一章_节选1",
                f"所在章节 id：{c1['id']}",
                "所在范围：第 1 段",
                "",
                "（选中文本原文，只允许修改这一段）",
                para,
                "",
                "请按我的要求润色以上选中文本：只输出修改后的这一段（不要输出前文/后文），"
                "完成后把修改结果写回该章节对应位置（写回前会先存快照，可回滚）。",
            ]
        )
        await chat(
            client,
            sid,
            "把选中的这一段润色得更生动流畅，保持原意与人物语气不变。",
            hidden_context=hidden,
        )
        new = await chapter_content(client, c1["id"])
        if new == old:
            raise SimFail("润色后章节内容未变化（AI 未写回）")
        if para in new:
            raise SimFail("润色后原选区段落未被修改")
        done("AI 润色选区", "选区已改写并写回章节")

        # ---- 步骤 5：删除第二卷并重写 ----
        step_banner(5, "删除第二卷 → 重建 → 重写五章")
        v2 = vols[1]
        await api(client, "DELETE", f"/volumes/{v2['id']}")
        left = await api(client, "GET", f"/novels/{nid}/volumes")
        if any(v["id"] == v2["id"] for v in left) or len(left) != 2:
            raise SimFail(f"删除第二卷后结构异常: {len(left)} 卷")
        vol2 = await api(
            client, "POST", f"/novels/{nid}/volumes", json={"title": VOL_TITLES[1], "summary": "第二卷简介"}
        )
        new_row: list[dict] = []
        for ct in CH_TITLES[1]:
            ch = await api(client, "POST", f"/volumes/{vol2['id']}/chapters", json={"title": ct, "content": ""})
            new_row.append(ch)
        chapters[1] = new_row
        for ci in range(5):
            await generate_chapter(client, sid, new_row[ci]["id"], CH_TITLES[1][ci], tag=f"重写V2C{ci + 1}")
        done("删卷重写", "第二卷删除→重建→五章重新生成")

        # ---- 步骤 6：全书一致性体检 ----
        step_banner(6, "全书一致性体检")
        audit = await api(client, "POST", f"/novels/{nid}/audit", timeout=900.0)
        if audit.get("status") in (None, "skipped"):
            raise SimFail(f"体检未执行: {audit}")
        report = ROOT / "data" / "workspaces" / nid / "outline" / "consistency-audit.md"
        if not report.exists():
            raise SimFail(f"体检报告未落盘: {report}")
        done("全书体检", f"status={audit.get('status')} issues={len(audit.get('issues') or [])} 报告已落盘")

        # ---- 步骤 7：导出 PDF ----
        step_banner(7, "导出 PDF")
        task = await api(client, "POST", f"/novels/{nid}/export?format=pdf")
        tid = task["task_id"]
        deadline = time.time() + 300
        dto = task
        while time.time() < deadline:
            dto = await api(client, "GET", f"/novels/{nid}/export-tasks/{tid}")
            if dto.get("status") in ("done", "failed", "cancelled"):
                break
            await asyncio.sleep(1.5)
        if dto.get("status") != "done":
            raise SimFail(f"PDF 导出任务未成功: {dto}")
        resp = await client.get(f"{BASE}/novels/{nid}/export-tasks/{tid}/download")
        if resp.status_code != 200:
            raise SimFail(f"下载导出产物失败: HTTP {resp.status_code} {resp.text[:300]}")
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            pdfs = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
            if not pdfs:
                raise SimFail(f"zip 内无 PDF: {zf.namelist()}")
            head = zf.read(pdfs[0])[:5]
        if head != b"%PDF-":
            raise SimFail(f"PDF 文件头异常: {head!r}")
        done("导出 PDF", f"{pdfs[0]} ({len(resp.content)} bytes zip)")

    print("\n[sim] ===== 模拟测试汇总 =====", flush=True)
    for name, st in results:
        print(f"[sim] {st}  {name}", flush=True)
    print(f"[sim] 全部通过：{len(results)} 个步骤", flush=True)
    return 0


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    except SimFail as exc:
        print(f"\n[sim] 失败: {exc}", flush=True)
        code = 1
    sys.exit(code)
