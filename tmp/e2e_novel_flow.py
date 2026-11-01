# -*- coding: utf-8 -*-
"""
端到端自动化验收（v1101）：新建小说 → 生成并完善大纲 → 按大纲门禁生成章节 →
修改部分章简介 → 按改动修改对应章节 → 选段 AI 润色（优美 / 古朴清秀 / 壮美）。

隔离运行：独立 DATA_ROOT（tmp/e2e_data）+ 端口 8123 + 本地模拟大模型服务
（OpenAI 兼容 /v1/chat/completions，脚本化回复 + 工具调用编排），
不消耗真实 token、不触碰用户真实书库。

用法：uv run python tmp/e2e_novel_flow.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO / "tmp" / "e2e_data"
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}/api/v1"
API_KEY_ENV = "E2E_LLM_KEY"

RESULTS: list[tuple[str, str, str]] = []  # (步骤, 结果, 说明)


def record(step: str, ok: bool, note: str = "") -> None:
    RESULTS.append((step, "PASS" if ok else "FAIL", note))
    print(f"[{'PASS' if ok else 'FAIL'}] {step}" + (f" —— {note}" if note else ""), flush=True)


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------


def req(method: str, path: str, body: dict | None = None, timeout: int = 60, base: str = BASE):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def req_raw(method: str, path: str, body: dict | None = None, timeout: int = 60):
    """返回 (status, payload)。"""
    try:
        return 200, req(method, path, body, timeout)
    except urllib.error.HTTPError as e:  # noqa: PERF203
        return e.code, json.loads(e.read())


# ---------------------------------------------------------------------------
# 模拟大模型服务（OpenAI 兼容）
# ---------------------------------------------------------------------------

CHAPTER_TITLES = [
    "第一章 雾起站台", "第二章 无名乘客", "第三章 餐车密语", "第四章 隧道失明",
    "第五章 行李厢里的人", "第六章 逆行时刻表", "第七章 乘务长的名字", "第八章 断桥信号",
    "第九章 车头之火", "第十章 到站无名",
]
VOLUME_TITLE = "第一卷 雾锁山海"
INTROS = [
    "林昭深夜登上 G1721 次列车，站台大雾中乘务长老周递来一只黑色对讲机并立下禁忌规矩。",
    "九号车厢多出一名不在名单上的乘客，林昭核对票务时发现其车票日期是三十年后。",
    "餐车里乘客们压低声音交换传闻：列车每逢隧道就会少一个人，而无人记得少的是谁。",
    "列车驶入最长隧道时全车断电，黑暗中有人按响开门键，林昭第一次听见自己的声音从对讲机里传出。",
    "行李厢中发现昏迷的检票员，其制服编号与林昭相同，醒来后只重复一句「别到站」。",
    "时刻表开始倒行，车站名逐一熄灭，老周承认列车早已脱轨，跑的是三十年前那班车的记忆。",
    "林昭追问乘务长真名，老周坦白自己就是三十年前失踪的司机，靠「不被叫名」留在车上。",
    "前方断桥信号亮起，车上乘客分成两派：跳车逃生或陪车冲桥，林昭必须做出选择。",
    "车头起火，火中浮现三十年前事故现场，林昭用对讲机唤出老周的真名，火势骤退。",
    "列车缓缓到站，站牌空白无名；乘客逐一消失于雾中，林昭接过老周的对讲机成为新的乘务长。",
]
POLISH_FLAVORS = [
    "，字句如清泉漱石，温润流转，优美动人",
    "，语带古意，清瘦秀逸，如魏晋短札般古朴清秀",
    "，笔势开阔，山河壮色尽收眼底，极写壮美",
]
polish_counter = {"n": 0}


def _outline_text() -> str:
    lines = ["# 山海列车 · 大纲", "", f"# {VOLUME_TITLE}", ""]
    for i, (title, intro) in enumerate(zip(CHAPTER_TITLES, INTROS)):
        link = "承接上一章结尾的悬念，推进主线。" if i else "全书开篇，奠定雾与列车的意象。"
        lines += [f"## {title}", "", f"- 简介：{intro}", f"- 衔接要点：{link}", ""]
    lines += ["# 附录A 世界观", "", "- 世界背景：近未来山海之间的高速铁路网", "- 力量体系：雾中记忆可附于列车", ""]
    return "\n".join(lines)


def _body_for(idx: int, extra: str = "") -> str:
    intro = INTROS[idx]
    paras = [f"<p>{intro}</p>"]
    for k in range(4):
        paras.append(
            f"<p>雾在第{k + 1}段里程里翻涌，车灯只能照亮三米之内的铁轨。{extra}"
            f"林昭握紧对讲机，指节发白；车厢深处传来若有若无的脚步声，与车轮节奏错开半拍。</p>"
        )
    paras.append(f"<p>章末，{intro[:12]}……的悬念留在雾里，等待下一章揭开。</p>")
    return "\n\n".join(paras)


def _frontmatter_of(read_result: str) -> str:
    """从 read_file 结果（@@ lines … + 全文）中截取 frontmatter 段（含首尾 ---）。"""
    body = read_result.split("@@", 2)[-1] if read_result.startswith("@@") else read_result
    lines = body.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "---":
            start = i
            break
    if start is None:
        return ""
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[start : i + 1])
    return ""


def _first_body_line(read_result: str) -> str:
    body = read_result.split("@@", 2)[-1] if read_result.startswith("@@") else read_result
    lines = body.split("\n")
    in_fm = False
    for ln in lines:
        if ln.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm:
            continue
        if ln.strip():
            return ln.rstrip()
    return ""


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # 静默
        pass

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/v1/chat/completions"):
            self._json(404, {"error": "not found"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n))
        msgs = payload.get("messages") or []
        tools = payload.get("tools") or []
        message = self._decide(msgs, tools)
        if payload.get("stream"):
            self._sse(message)
        else:
            self._json(200, {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                             "model": "e2e-mock", "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    # ---- SSE 流式响应（langchain streaming=True 走这条路径） ----
    def _sse(self, message: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.close_connection = True
        self.end_headers()

        def chunk(delta: dict, finish: str | None = None) -> None:
            obj = {
                "id": "chatcmpl-e2e", "object": "chat.completion.chunk",
                "created": int(time.time()), "model": "e2e-mock",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n")
            self.wfile.flush()

        tcs = message.get("tool_calls") or []
        if tcs:
            tc = tcs[0]
            fn = tc["function"]
            args = fn.get("arguments") or ""
            chunk({"role": "assistant", "content": "",
                   "tool_calls": [{"index": 0, "id": tc["id"], "type": "function",
                                   "function": {"name": fn["name"], "arguments": ""}}]})
            step = max(1, len(args) // 4)
            for i in range(0, len(args), step):
                chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[i:i + step]}}]})
            chunk({}, finish="tool_calls")
        else:
            text = str(message.get("content") or "")
            chunk({"role": "assistant", "content": ""})
            step = max(1, len(text) // 6)
            for i in range(0, len(text), step):
                chunk({"content": text[i:i + step]})
            chunk({}, finish="stop")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    # ---- 决策 ----
    def _decide(self, msgs: list, tools: list) -> dict:
        sys_text = " ".join(str(m.get("content") or "") for m in msgs if m.get("role") == "system")
        last = msgs[-1] if msgs else {}
        last_text = str(last.get("content") or "")
        # ★分支依据 = 最近一条 **user** 消息（工具结果之后 last 不再是 user）
        user_text = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                user_text = str(m.get("content") or "")
                break
        has_tools = bool(tools)

        if "章节生成前的衔接预检" in last_text:
            return self._final(json.dumps(
                {"passed": True, "score": 9, "summary": "衔接合理", "issues": [], "suggestions": []},
                ensure_ascii=False))
        if "章节生成后的匹配后检" in last_text:
            return self._final(json.dumps(
                {"matched": True, "score": 9, "reason": "覆盖全部要点", "missing_points": [], "deviations": []},
                ensure_ascii=False))
        if "生成**摘要**" in last_text:
            return self._final(json.dumps(
                {"key_events": ["主角登车", "雾中异象", "悬念收尾"], "characters": ["林昭", "老周"],
                 "foreshadowing": ["对讲机"], "ending_state": "悬念留存"}, ensure_ascii=False))

        if has_tools and "【选中待润色文本】" in sys_text:
            return self._polish_flow(msgs, sys_text)
        if has_tools and "补写大纲小节" in user_text:
            # G0 咨询选 agent_write 后的代写指令：直接写入完整大纲（含全部小节）
            path = re.search(r"(/projects/\S+?/outline/\S+?\.md)", user_text)
            if not path:
                path = re.search(r"(/projects/\S+?\.md)", " ".join(
                    str(m.get("content")) for m in msgs if m.get("role") == "tool"))
            return self._write_flow(msgs, path.group(1) if path else "", kind="outline")
        if has_tools and "请按大纲生成" in user_text:
            path = re.search(r"(/projects/\S+?\.md)", user_text)
            return self._write_flow(msgs, path.group(1) if path else "", kind="gate")
        if has_tools and "目标文件：" in user_text and "生成完整大纲" not in user_text:
            path = re.search(r"(/projects/\S+?\.md)", user_text)
            return self._write_flow(msgs, path.group(1) if path else "", kind="revise")
        if has_tools and "生成完整大纲" in user_text:
            path = re.search(r"(/projects/\S+?/outline/\S+?\.md)", user_text)
            return self._write_flow(msgs, path.group(1) if path else "", kind="outline")
        if has_tools:
            return self._final("好的，我已了解项目情况，请告诉我下一步要做什么。")
        return self._final("收到，这一步已完成。")

    # ---- 工具编排 ----
    def _turn_messages(self, msgs: list) -> list:
        """最近一条 user 消息之后的消息（当前轮），避免跨轮误判。"""
        idx = 0
        for i, m in enumerate(msgs):
            if m.get("role") == "user":
                idx = i
        return msgs[idx:]

    def _wrote_before(self, msgs: list) -> bool:
        return any(
            m.get("role") == "assistant"
            and any(tc.get("function", {}).get("name") in ("write_file", "edit_file")
                    for tc in (m.get("tool_calls") or []))
            for m in self._turn_messages(msgs)
        )

    def _last_tool_text(self, msgs: list) -> str:
        for m in reversed(msgs):
            if m.get("role") == "tool":
                c = m.get("content")
                return c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
        return ""

    def _last_read_text(self, msgs: list) -> str:
        """最近一次成功的 read_file 结果（@@/--- 开头），供重试写入时重建内容。"""
        for m in reversed(msgs):
            if m.get("role") == "tool":
                c = m.get("content")
                c = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                if c.startswith("@@") or c.startswith("---"):
                    return c
        return ""

    def _is_interrupt_error(self, text: str) -> bool:
        return text.startswith("工具执行失败") and "Interrupt" in text

    def _write_flow(self, msgs: list, path: str, kind: str) -> dict:
        last = msgs[-1]
        if last.get("role") == "user":
            return self._tool("read_file", {"file_path": path})
        tool_text = self._last_tool_text(msgs)
        read_text = self._last_read_text(msgs)
        if tool_text.startswith("Updated file") or tool_text.startswith("Created file"):
            return self._tool("read_file", {"file_path": path})
        if tool_text.startswith("@@") or tool_text.startswith("---"):
            if self._wrote_before(msgs):
                return self._final(f"已完成写入并回读确认：{path}")
            return self._tool("write_file", {"file_path": path, "content": self._content_for(kind, read_text, msgs)})
        if self._is_interrupt_error(tool_text):
            # 高危写入审批挂起：本轮到此为止；wait_run 批准后会 resume 重放同一 tool_call 真正执行
            return self._final("写入操作已提交审批，等待用户批准后继续。")
        if tool_text.startswith("Error") or "not found" in tool_text.lower():
            # 读取失败（路径/文件缺失）→ 直接写入（revise 场景无原文可保留）
            return self._tool("write_file", {"file_path": path, "content": self._content_for(kind, "", msgs)})
        return self._final(f"已完成：{path}")

    def _content_for(self, kind: str, read_text: str, msgs: list) -> str:
        fm = _frontmatter_of(read_text)
        if kind == "outline":
            return _outline_text()
        if kind == "gate":
            user_text = ""
            for m in reversed(msgs):
                if m.get("role") == "user":
                    user_text = str(m.get("content") or "")
                    break
            m = re.search(r"《.*?·\s*(第.+?章.*?)》", user_text)
            idx = 0
            if m:
                for i, t in enumerate(CHAPTER_TITLES):
                    if m.group(1).strip() == t:
                        idx = i
                        break
            return (fm + "\n\n" + _body_for(idx)).strip() + "\n"
        # revise：保留第一段正文，其余按新简介重写并加入灯塔意象
        first = _first_body_line(read_text)
        keep = first if first else "<p>雾锁山海，列车前行。</p>"
        new_paras = [
            keep,
            "<p>改写后的中段：浓雾深处亮起一点橘红——那是雾中灯塔的光，三十年前的信号工仍在塔上值守，"
            "为迷航的列车指路。灯塔意象贯穿本章，呼应新简介。</p>",
            "<p>章末，灯塔的光缓缓熄灭，新的悬念浮出雾面。</p>",
        ]
        return (fm + "\n\n" + "\n\n".join(new_paras)).strip() + "\n"

    def _last_assistant_tool(self, msgs: list) -> tuple[str, dict]:
        for m in reversed(msgs[:-1]):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                tc = m["tool_calls"][0]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                return tc["function"].get("name") or "", args
        return "", {}

    def _polish_flow(self, msgs: list, sys_text: str) -> dict:
        pid = ""
        m = re.search(r"/projects/([0-9a-f-]{36})/", sys_text)
        if m:
            pid = m.group(1)
        cid = ""
        m = re.search(r"所在章节 id：(\w+)", sys_text)
        if m:
            cid = m.group(1)
        last = msgs[-1]
        tool_text = self._last_tool_text(msgs)
        prev_name, prev_args = self._last_assistant_tool(msgs)

        if last.get("role") == "user":
            return self._tool("ls", {"path": f"/projects/{pid}/chapters/"})
        if prev_name == "ls":
            p = str(prev_args.get("path") or "")
            if p.rstrip("/").endswith("/chapters"):
                vm = re.search(r"(v[0-9a-f]{8})", tool_text)
                return self._tool("ls", {"path": f"/projects/{pid}/chapters/{vm.group(1) if vm else ''}/"})
            vm = re.search(r"(v[0-9a-f]{8})", p)
            cm = re.search(rf"(c{cid}\.md)", tool_text) if cid else re.search(r"(c[0-9a-f]{8}\.md)", tool_text)
            if vm and cm:
                return self._tool("read_file", {"file_path": f"/projects/{pid}/chapters/{vm.group(1)}/{cm.group(1)}"})
        if prev_name == "read_file" and tool_text.startswith("@@"):
            line = _first_body_line(tool_text)
            style = POLISH_FLAVORS[polish_counter["n"] % len(POLISH_FLAVORS)]
            polish_counter["n"] += 1
            return self._tool("edit_file", {
                "file_path": prev_args.get("file_path", ""),
                "old_string": line,
                "new_string": line.rstrip() + style + "。",
            })
        if self._is_interrupt_error(tool_text) and prev_name == "edit_file":
            # 审批挂起：本轮结束；批准后 resume 重放同一 edit 真正执行
            return self._final("润色写入已提交审批，等待用户批准后继续。")
        if self._wrote_before(msgs):
            return self._final("润色完成，已写回原章节对应位置。")
        return self._final("润色流程结束。")

    # ---- 响应构造 ----
    def _final(self, text: str) -> dict:
        return {"role": "assistant", "content": text}

    def _tool(self, name: str, args: dict) -> dict:
        # ★确定性 tool_call id（name+args 哈希）：审批决策按 tool_call_id 关联，
        # resume/重放时同一调用必须同 id，否则批准永远匹配不上（死循环）。
        digest = hashlib.md5(
            json.dumps({"n": name, "a": args}, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:12]
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call_{digest}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }],
        }

    def _json(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


# ---------------------------------------------------------------------------
# 运行等待 / 中断自动处置
# ---------------------------------------------------------------------------


def wait_run(run_id: str, timeout: int = 300) -> dict:
    """轮询 run；仅在 run 处于暂停态时自动处置中断（审批 approve / 咨询 force_skip）；
    终态返回 run dto。resolve 的 409 竞态（已恢复/重复）忽略后继续轮询。"""
    deadline = time.time() + timeout
    seen: set[str] = set()
    while time.time() < deadline:
        run = req("GET", f"/agent/runs/{run_id}")["data"]
        paused = run["status"] in ("awaiting_approval", "awaiting_consult")
        if paused:
            for it in run.get("interrupts") or []:
                if it.get("decision") not in (None, "") or it["id"] in seen:
                    continue
                seen.add(it["id"])
                try:
                    if it.get("type") == "approval":
                        req("POST", f"/agent/interrupts/{it['id']}/resolve", {"decision": "approve"})
                        print(f"    · 自动批准审批：{(it.get('payload') or {}).get('reason', '')[:60]}", flush=True)
                    else:
                        opts = (it.get("payload") or {}).get("options") or []
                        choice = "force_skip"
                        values = [o.get("value") if isinstance(o, dict) else o for o in opts]
                        if choice not in values:
                            choice = values[0] if values else "cancel"
                        req("POST", f"/agent/interrupts/{it['id']}/resolve",
                            {"decision": "option", "choice": choice})
                        print(f"    · 自动处置咨询：{choice}", flush=True)
                except urllib.error.HTTPError as e:  # 409 竞态：已被恢复/重复 resolve
                    print(f"    · resolve 竞态忽略：{e.code}", flush=True)
        if run["status"] in ("completed", "failed", "cancelled"):
            return run
        time.sleep(1.5)
    return req("GET", f"/agent/runs/{run_id}")["data"]


def send(session_id: str, content: str, hidden: str = "") -> dict:
    body = {"content": content}
    if hidden:
        body["hidden_context"] = hidden
    return req("POST", f"/agent/sessions/{session_id}/messages", body, timeout=60)["data"]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


class QuietServer(ThreadingHTTPServer):
    """客户端断连等噪音错误静默（keep-alive 探测常见）。"""

    def handle_error(self, request, client_address):  # noqa: ARG002
        pass


def body_chars(content: str) -> int:
    """正文实际字数：去掉 frontmatter 与空白/markdown 标记后计数（与 files.count_words 同口径）。"""
    text = content or ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    cleaned = re.sub(r"[#>*`\-\[\]!|<>/]", "", text)
    return len(re.sub(r"\s+", "", cleaned))


def main() -> int:
    for _ in range(5):
        try:
            if DATA_ROOT.exists():
                shutil.rmtree(DATA_ROOT)
            break
        except OSError:
            time.sleep(2)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    server = QuietServer(("127.0.0.1", PORT + 1), MockHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"模拟大模型服务已启动：127.0.0.1:{PORT + 1}", flush=True)

    env = dict(os.environ)
    env.update({
        "DATA_ROOT": str(DATA_ROOT),
        API_KEY_ENV: "e2e-test-key",
        "AGENT_RECURSION_LIMIT": "100",
        "AGENT_MODEL_CALL_LIMIT": "60",
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            time.sleep(1)
            try:
                req("GET", "/health", base=f"http://127.0.0.1:{PORT}/api/v1")
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            print("后端启动失败")
            return 1

        # ---- 模型配置 ----
        prov = req("POST", "/llm/providers", {
            "name": "E2E模拟", "type": "openai_compatible",
            "base_url": f"http://127.0.0.1:{PORT + 1}/v1",
            "api_key_env": API_KEY_ENV, "api_key_value": "e2e-test-key",
        })["data"]
        model = req("POST", "/llm/models", {
            "provider_id": prov["id"], "model_id": "e2e-mock", "display_name": "E2E 模拟模型",
            "is_default": True, "max_context": 32768,
            "default_params": {"temperature": 0.3, "max_tokens": 8000},
        })["data"]
        model_ref = model["model_ref"]
        record("环境准备（隔离数据目录 + 模拟模型）", True, f"model_ref={model_ref}")

        # ---- 新建小说 + 一卷十章 ----
        novel = req("POST", "/novels", {
            "title": "山海列车", "author_name": "E2E", "genre": "悬疑奇幻",
            "synopsis": "一列行驶在雾中的列车，载着三十年前的记忆与一名新乘务长。",
        })["data"]
        nid = novel["id"]
        vol = req("POST", f"/novels/{nid}/volumes", {"title": VOLUME_TITLE, "summary": "雾中十站"})["data"]
        ch_ids = []
        for t in CHAPTER_TITLES:
            ch = req("POST", f"/volumes/{vol['id']}/chapters", {"title": t, "content": "占位正文。"})["data"]
            ch_ids.append(ch["id"])
        record("新建小说 + 一卷十章", len(ch_ids) == 10, f"novel={nid[:8]}")

        sess = req("POST", "/agent/sessions", {"novel_id": nid, "model_ref": model_ref})["data"]
        sid = sess["id"]

        # ---- 步骤A：生成并完善大纲 ----
        outline_path = f"/projects/{nid}/outline/山海列车-大纲.md"
        r = send(sid, (
            f"请为本书生成完整大纲并写入大纲文件 {outline_path}：一卷十章，"
            "每章给出「- 简介：」与「- 衔接要点：」，格式遵循现有模板（# 卷、## 章）。"
        ))
        run = wait_run(r["run_id"])
        outline = req("GET", f"/novels/{nid}/outline")["data"]
        otext = outline.get("content") or ""
        sections = re.findall(r"^## (.+)$", otext, re.M)
        intros_ok = otext.count("- 简介：") >= 10 and not re.search(r"- 简介：\s*$", otext, re.M)
        record("A 生成并完善大纲（10 章简介齐备）",
               run["status"] == "completed" and len(sections) >= 10 and intros_ok,
               f"status={run['status']} sections={len(sections)}")

        # ---- 步骤B：按大纲门禁生成前三章 ----
        gate_ok = True
        notes = []
        for i in (0, 1, 2):
            r = send(sid, f"请按大纲「{VOLUME_TITLE} · {CHAPTER_TITLES[i]}」的章简介生成正文")
            run = wait_run(r["run_id"])
            content = req("GET", f"/chapters/{ch_ids[i]}/content")["data"]
            wc = body_chars(content.get("content") or "")
            # 门禁运行判定：意图命中 → target_chapter_id 非空（gate_stage 在 done 后会清空）
            ok = run["status"] == "completed" and wc >= 300 and bool(run.get("target_chapter_id"))
            gate_ok = gate_ok and ok
            notes.append(f"ch{i + 1}:{run['status']}/{wc}字/target={'Y' if run.get('target_chapter_id') else 'N'}")
        record("B 门禁四阶段生成前三章（G0→G3 + 落盘≥300字）", gate_ok, "; ".join(notes))

        # ---- 步骤C：修改大纲第二章简介 ----
        new_intro = ("林昭核对无名乘客时，雾中浮现一座灯塔的轮廓；"
                     "本章新增「雾中灯塔」一幕：灯塔信号工留下半页值班日志，指向三十年前的事故。")
        otext2 = re.sub(
            r"(## 第二章 无名乘客\n\n- 简介：).*", lambda m: m.group(1) + new_intro, otext, count=1, flags=re.M
        )
        req("PUT", f"/novels/{nid}/outline", {"content": otext2})
        val = req("GET", f"/novels/{nid}/outline/validate")["data"]
        record("C 修改大纲第二章简介（新增雾中灯塔一幕）",
               new_intro in (req("GET", f"/novels/{nid}/outline")["data"]["content"] or "")
               and val.get("ok") and not val.get("missing"),
               f"ok={val.get('ok')} missing={len(val.get('missing') or [])} issues={len(val.get('issues') or [])}")

        # ---- 步骤D：按改动修改第二章正文 ----
        ch2_path = f"/projects/{nid}/chapters/v{vol['id']}/c{ch_ids[1]}.md"
        r = send(sid, (
            "第二章的简介我更新过了（加了雾中灯塔一幕）。请打开章节文件，保留第一段，"
            f"其余段落按新简介调整，加入「灯塔」意象。目标文件：{ch2_path}"
        ))
        run = wait_run(r["run_id"])
        c2 = req("GET", f"/chapters/{ch_ids[1]}/content")["data"]["content"] or ""
        record("D 按大纲改动修改第二章正文（含灯塔意象）",
               run["status"] == "completed" and "灯塔" in c2,
               f"status={run['status']} 灯塔in={'灯塔' in c2}")

        # ---- 步骤E：选段 AI 润色 ×3（优美 / 古朴清秀 / 壮美）+ 隐藏上下文验证 + 编辑锁验证 ----
        styles = [
            ("语句优美", POLISH_FLAVORS[0].strip("，。")),
            ("对话带有古朴清秀的风格", POLISH_FLAVORS[1].strip("，。")),
            ("描述景色的壮美", POLISH_FLAVORS[2].strip("，。")),
        ]
        polish_ok = True
        pnotes = []
        lock_checked = False
        for i, (style_req, marker) in enumerate(styles):
            ch_id = ch_ids[i]
            content = req("GET", f"/chapters/{ch_id}/content")["data"]["content"] or ""
            body_lines = [ln for ln in content.split("\n") if ln.strip().startswith("<p>")]
            if not body_lines:
                body_lines = [ln for ln in content.split("\n") if ln.strip() and not ln.startswith("---") and ":" not in ln[:12]]
            if not body_lines:
                body_lines = ["<p>雾锁山海，列车在夜色中前行。</p>"]
            snippet = re.sub(r"</?p>", "", body_lines[1] if len(body_lines) > 1 else body_lines[0])[:60]
            hidden = "\n".join([
                f"【选中待润色文本】第{i + 1}章_节选1",
                f"所在章节 id：{ch_id}",
                "所在范围：第2-2段（全章共6段）",
                "", "（选中文本原文，只允许修改这一段）", snippet,
                "", "请按我的要求润色以上选中文本：只输出修改后的这一段（不要输出前文/后文），"
                    "完成后把修改结果写回该章节对应位置（写回前会先存快照，可回滚）。",
            ])
            r = send(sid, f"请润色第{i + 1}章选段，要求：{style_req}", hidden=hidden)
            if not lock_checked:
                # 轮询 /chapters/locks：Agent 写入该章期间应出现在锁列表（用户保存被拒的根源）
                lock_ok = False
                for _ in range(60):
                    time.sleep(1.0)
                    locks = req("GET", "/chapters/locks")["data"]
                    if any(lk.get("chapter_id") == ch_id for lk in locks):
                        st, payload = req_raw("PUT", f"/chapters/{ch_id}/content",
                                              {"content": content, "base_version": 999})
                        lock_ok = st == 409 and payload.get("code") == 40903
                        break
                    run_now = req("GET", f"/agent/runs/{r['run_id']}")["data"]
                    if run_now["status"] in ("completed", "failed", "cancelled"):
                        break
                lock_checked = True
                record("E0 AI 编辑锁：润色运行中用户保存被拒（409/40903）", lock_ok,
                       f"locked_seen={lock_ok}")
            run = wait_run(r["run_id"])
            after = req("GET", f"/chapters/{ch_id}/content")["data"]["content"] or ""
            tree = req("GET", f"/agent/sessions/{sid}/messages")["data"]
            leaked = any("选中待润色文本" in str(it["content"]) for it in tree["items"])
            ok = run["status"] == "completed" and marker in after and not leaked
            polish_ok = polish_ok and ok
            pnotes.append(f"style{i + 1}:{run['status']}/marker={'ok' if marker in after else 'no'}"
                          f"/hidden={'ok' if not leaked else 'no'}")
        record("E 选段 AI 润色×3（优美/古朴清秀/壮美）+ 提示词隐藏", polish_ok, "; ".join(pnotes))

        # ---- 步骤F：会话列表运行标记（is_running）----
        r = send(sid, "随便聊聊，一句话回复我")
        flag_on = False
        for _ in range(20):  # mock 响应极快：发送后立即轮询捕捉运行态
            listed = {s["id"]: s for s in req("GET", f"/agent/sessions?novel_id={nid}")["data"]}
            if listed[sid].get("is_running"):
                flag_on = True
                break
            time.sleep(0.3)
        wait_run(r["run_id"])
        listed = {s["id"]: s for s in req("GET", f"/agent/sessions?novel_id={nid}")["data"]}
        flag_off = listed[sid].get("is_running") is False
        record("F 会话列表「AI思考执行中」标记出现/消失", flag_on and flag_off,
               f"on={flag_on} off={flag_off}")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        server.shutdown()

    # ---- 报告 ----
    print("\n================ E2E 自动化验收报告 ================")
    for step, res, note in RESULTS:
        print(f"{res:>4} | {step}" + (f" | {note}" if note else ""))
    failed = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"\n合计：{len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    print(f"隔离数据目录：{DATA_ROOT}（可自行删除）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

