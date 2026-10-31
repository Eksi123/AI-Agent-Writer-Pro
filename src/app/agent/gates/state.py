# -*- coding: utf-8 -*-
"""
模块: app.agent.gates.state
文件名: state.py
作者: xionggaoqing
时间: 2026-10-27
说明: 大纲驱动生成四阶段门禁（手册「二、(6)」）。

    **服务层硬编排**（非 Agent 自觉执行）：
    - G0 大纲存在性：`parse_outline` 定位目标章简介，两类缺失（无小节 / 简介空）→ 咨询；
    - G1 衔接预检：独立 LLM 校验（带上一卷末章锚点），不通过 → 咨询（可强制跳过 + 审计）；
    - G2 生成：writer 子代理写文件（生成前先快照旧正文 source="agent"）；
    - G3 匹配后检：独立 LLM 校验，不匹配自动重生成 ≤2 次，仍不匹配 → 咨询。

    G0/G1/G3 直连 ChatModel + JSON 结构化输出（可预测、可单测）；只有 G2 进 deep agent。
    咨询经 `ConsultRequired` 信号 → runner 落 `agent_interrupts(type=consult)` + 发 `consult.request`；
    resolve 后从当前门禁阶段续跑（状态序列化在 `agent_runs.gate_result_json`）。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 门禁阶段
STAGES = ("G0", "G1", "G2", "G3", "done", "failed")

#: G3 自动重生成上限
G3_MAX_RETRY = 2

#: 咨询类型（resume 时据此路由决策）
CONSULT_G0_MISSING = "g0_missing"
CONSULT_G1_PRECHECK = "g1_precheck"
CONSULT_G3_POSTCHECK = "g3_postcheck"


class ConsultRequired(Exception):
    """门禁需要用户决策：抛出后 runner 落中断并暂停运行。"""

    def __init__(self, kind: str, payload: dict[str, Any]) -> None:
        self.kind = kind
        self.payload = payload
        super().__init__(kind)


@dataclass
class GateTarget:
    """生成目标章的定位信息。"""

    project_id: str
    volume_id: str
    volume_title: str
    chapter_id: str
    chapter_title: str
    #: 虚拟路径 `/projects/{pid}/chapters/v{vol}/c{ch}.md`
    target_path: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GateTarget:
        return cls(
            project_id=d["project_id"],
            volume_id=d["volume_id"],
            volume_title=d.get("volume_title", ""),
            chapter_id=d["chapter_id"],
            chapter_title=d.get("chapter_title", ""),
            target_path=d.get("target_path", ""),
        )


@dataclass
class GateState:
    """门禁状态机的可序列化状态（存 `agent_runs.gate_result_json`）。"""

    stage: str = "G0"
    target: GateTarget | None = None
    intro: str = ""
    link: str = ""
    #: 上一卷末章锚点文本（G1 用）
    anchor: str = ""
    appendix: str = ""
    g1_passed: bool | None = None
    g1_result: dict[str, Any] = field(default_factory=dict)
    g1_skipped: bool = False
    g3_retry: int = 0
    g3_feedback: str = ""
    g3_result: dict[str, Any] = field(default_factory=dict)
    word_target: int = 3000
    #: 当前挂起的咨询类型（resume 路由用）
    pending_consult: str = ""
    #: 完成标记与最终结论
    outcome: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, raw: str | None) -> GateState:
        if not raw:
            return cls()
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            return cls()
        if not isinstance(d, dict):
            return cls()
        target = d.pop("target", None)
        state = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        if isinstance(target, dict):
            state.target = GateTarget.from_dict(target)
        return state

    def record(self, stage: str, event: str, **detail: Any) -> None:
        self.history.append({"stage": stage, "event": event, **detail})


def llm_json(text: str) -> dict[str, Any]:
    """容错解析模型返回的 JSON（可能带代码块标记或前后解释文字）。"""
    if not text:
        return {}

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except ValueError:
        pass
    m = re.search(r"\{.*\}", cleaned, re.S)
    if m:
        try:
            value = json.loads(m.group(0))
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}
    return {}
