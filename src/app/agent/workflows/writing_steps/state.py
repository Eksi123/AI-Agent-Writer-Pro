# -*- coding: utf-8 -*-
"""
模块: app.agent.workflows.writing_steps.state
文件名: state.py
作者: xionggaoqing
时间: 2026-10-27
说明: 大纲驱动生成四阶段固定流程（手册「二、(6)」）。

    **服务层硬编排**（非 Agent 自觉执行）：
    - S0 大纲存在性：`parse_outline` 定位目标章简介，两类缺失（无小节 / 简介空）→ 咨询；
    - S1 衔接预检：独立 LLM 校验（带上一卷末章锚点），不通过 → 咨询（可强制跳过 + 审计）；
    - S2 生成：writer 子代理写文件（生成前先快照旧正文 source="agent"）；
    - S3 匹配后检：独立 LLM 校验，不匹配自动重生成 ≤2 次，仍不匹配 → 咨询。

    S0/S1/S3 直连 ChatModel + JSON 结构化输出（可预测、可单测）；只有 S2 进 deep agent。
    咨询经 `ConsultRequired` 信号 → runner 落 `agent_interrupts(type=consult)` + 发 `consult.request`；
    resolve 后从当前流程阶段续跑（状态序列化在 `agent_runs.workflow_result_json`）。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 流程阶段
STAGES = ("S0", "S1", "S2", "S3", "done", "failed")

#: S3 自动重生成上限
POSTCHECK_MAX_RETRY = 2

#: 咨询类型（resume 时据此路由决策）
CONSULT_OUTLINE_MISSING = "outline_missing"
CONSULT_PRECHECK = "precheck"
CONSULT_POSTCHECK = "postcheck"


class ConsultRequired(Exception):
    """流程需要用户决策：抛出后 runner 落中断并暂停运行。"""

    def __init__(self, kind: str, payload: dict[str, Any]) -> None:
        self.kind = kind
        self.payload = payload
        super().__init__(kind)


@dataclass
class WorkflowTarget:
    """生成目标章的定位信息。"""

    project_id: str
    volume_id: str
    volume_title: str
    chapter_id: str
    chapter_title: str
    #: 虚拟路径 `/projects/{pid}/chapters/v{vol}/c{ch}.md`
    target_path: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkflowTarget:
        return cls(
            project_id=d["project_id"],
            volume_id=d["volume_id"],
            volume_title=d.get("volume_title", ""),
            chapter_id=d["chapter_id"],
            chapter_title=d.get("chapter_title", ""),
            target_path=d.get("target_path", ""),
        )


@dataclass
class WorkflowState:
    """流程状态机的可序列化状态（存 `agent_runs.workflow_result_json`）。"""

    stage: str = "S0"
    target: WorkflowTarget | None = None
    intro: str = ""
    link: str = ""
    #: 上一卷末章锚点文本（S1 用）
    anchor: str = ""
    appendix: str = ""
    precheck_passed: bool | None = None
    precheck_result: dict[str, Any] = field(default_factory=dict)
    precheck_skipped: bool = False
    postcheck_retry: int = 0
    postcheck_feedback: str = ""
    postcheck_result: dict[str, Any] = field(default_factory=dict)
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
    def from_json(cls, raw: str | None) -> WorkflowState:
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
            state.target = WorkflowTarget.from_dict(target)
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
