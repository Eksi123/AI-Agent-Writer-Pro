# -*- coding: utf-8 -*-
"""
模块: app.agent.workflows.writing_steps.intent
文件名: intent.py
作者: xionggaoqing
时间: 2026-11-01
说明: 流程（四阶段工作流）改为**意图驱动**——不再有「生成本章」按钮，
    仅当用户消息表达「按大纲某章简介生成/修改正文」的意图时，才把该 run
    标记为固定流程运行（`target_chapter_id` 非空 → chapter_workflow/runner 四阶段编排）。

    判定为纯规则（可单测、可预测），不用 LLM：
    - 意图正则命中（大纲/章简介 + 生成/修改类动词 + 正文/本章/第X章 目标）；
    - 目标章解析：消息中显式出现的章标题 > 前端回传的当前章（active_chapter_id）。
    - 只改大纲/简介（不碰正文）的请求被负向规则排除，不误触发流程。
"""

from __future__ import annotations

import re
from typing import Any

#: 意图命中：大纲/章简介 作依据 + 生成/修改类动词 + 正文/章节 目标
_WORKFLOW_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"大纲.{0,24}?(?:生成|写|撰写|创作|改写|修改|重写|续写|完善)"
        r".{0,24}?(?:正文|本章|该章|这一章|章节|第[一二三四五六七八九十百零两\d]+章)"
    ),
    re.compile(r"(?:章简介|章节简介).{0,24}?(?:生成|写|撰写|创作|改写|修改|重写|续写|完善)"),
    re.compile(r"(?:生成|重写|续写|撰写|创作).{0,12}?(?:正文|本章|该章|这一章)"),
    # 「把/将 + 本章正文 + 重写/改写」语序（目标在前、动词在后）
    re.compile(r"(?:把|将).{0,16}?(?:正文|本章|该章|这一章).{0,12}?(?:重写|改写|修改|生成|续写|完善)"),
    # 「生成/续写 + 第X章到第Y章」或「完成第X章到第Y章的生成」
    re.compile(r"(?:生成|续写|完成).{0,24}?(?:第[一二三四五六七八九十百零两\d]+章).{0,12}?(?:到|至).{0,12}?(?:第[一二三四五六七八九十百零两\d]+章)"),
)

#: 负向：动作的宾语是大纲/简介本身（改大纲而非写正文）→ 不触发流程
_WORKFLOW_NEGATIVE = re.compile(r"(?:修改|编辑|更新|调整|完善|补充|填写).{0,12}(?:简介|大纲)")
#: 负向豁免：同时明确提到正文/本章时仍算正文意图
_WORKFLOW_NEGATIVE_EXEMPT = re.compile(r"(?:正文|本章|该章|这一章)")

#: 显式章标题提及：「第三章 …」/「第3章」
_CHAPTER_MENTION = re.compile(r"第\s*([一二三四五六七八九十百零两\d]+)\s*章")


def has_workflow_intent(text: str) -> bool:
    """消息是否表达「按大纲生成/修改正文」的意图。"""
    t = (text or "").strip()
    if not t:
        return False
    if _WORKFLOW_NEGATIVE.search(t) and not _WORKFLOW_NEGATIVE_EXEMPT.search(t):
        return False
    return any(p.search(t) for p in _WORKFLOW_INTENT_PATTERNS)


def resolve_workflow_chapter(
    text: str,
    chapters: list[dict[str, Any]],
    active_chapter_id: str | None = None,
) -> str | None:
    """意图命中后定位目标章：显式标题提及 > 当前章；都无则 None（走普通对话）。"""
    if not has_workflow_intent(text):
        return None
    return resolve_mentioned_chapter(text, chapters) or (active_chapter_id or None)


def resolve_mentioned_chapter(
    text: str,
    chapters: list[dict[str, Any]],
) -> str | None:
    """定位消息里**显式提到**的章（不做写作意图判定，也不回落当前章）。

    优先级：完整章标题 > 「第X章」序数；都无则 None。
    大纲按需摘录注入复用本函数——用户只说「参考第三章简介润色这段」时
    不命中写作意图，但仍应摘录该章大纲而非整篇读取。
    """
    t = (text or "").strip()
    if not t or not chapters:
        return None

    # ① 消息里显式写了章标题（longest-first 防前缀误配）
    titled = [c for c in chapters if str(c.get("title") or "").strip()]
    titled.sort(key=lambda c: len(str(c.get("title"))), reverse=True)
    for ch in titled:
        if str(ch["title"]) in t:
            return str(ch["id"])

    # ② 「第X章」序数提及 → 按 sort_order 序匹配
    m = _CHAPTER_MENTION.search(t)
    if m:
        want = _ordinal_of(m.group(1))
        if want is not None:
            ordered = sorted(chapters, key=lambda c: int(c.get("sort_order") or 0))
            for idx, ch in enumerate(ordered, start=1):
                if idx == want:
                    return str(ch["id"])

    return None


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _ordinal_of(raw: str) -> int | None:
    """「三」/「12」/「十二」→ 序数（支持到 99，够用且保持简单）。"""
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if len(raw) == 1:
        return _CN_DIGITS.get(raw)
    if raw.startswith("十"):
        rest = raw[1:]
        if not rest:
            return 10
        ones = _CN_DIGITS.get(rest)
        return 10 + ones if ones is not None else None
    if raw.endswith("十"):
        tens = _CN_DIGITS.get(raw[:-1])
        return tens * 10 if tens else None
    if "十" in raw:
        tens, _, ones = raw.partition("十")
        a, b = _CN_DIGITS.get(tens), _CN_DIGITS.get(ones)
        return a * 10 + b if a and b is not None else None
    # 多位纯中文数字（如「二三」不规范）不支持
    return None


def detect_workflow_target(
    text: str,
    chapters: list[dict[str, Any]],
    active_chapter_id: str | None = None,
) -> str | None:
    """对外入口：意图 + 目标章一步判定；返回 chapter_id 或 None。"""
    return resolve_workflow_chapter(text, chapters, active_chapter_id)


# ---------------------------------------------------------------------------
# 卷 / 附录提及定位（大纲按需摘录的三级目标：章 → 卷 → 附录）
# ---------------------------------------------------------------------------

_VOLUME_MENTION = re.compile(r"第\s*([一二三四五六七八九十百零两\d]+)\s*卷")
_APPENDIX_MENTION = re.compile(r"附录\s*([A-Za-z0-9一二三四五六七八九十]+)?")


def resolve_mentioned_volume(text: str, volumes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """定位消息里显式提到的卷：完整卷标题 > 「第X卷」序数（按大纲卷序）。

    `volumes` 元素为 `{title, index}`（index 从 1 起的大纲卷序）。
    """
    t = (text or "").strip()
    if not t or not volumes:
        return None
    titled = [v for v in volumes if str(v.get("title") or "").strip()]
    titled.sort(key=lambda v: len(str(v.get("title"))), reverse=True)
    for vol in titled:
        if str(vol["title"]) in t:
            return vol
    m = _VOLUME_MENTION.search(t)
    if m:
        want = _ordinal_of(m.group(1))
        if want is not None:
            ordered = sorted(volumes, key=lambda v: int(v.get("index") or 0))
            for vol in ordered:
                if int(vol.get("index") or 0) == want:
                    return vol
    return None


def resolve_mentioned_appendix(text: str, appendices: list[dict[str, Any]]) -> dict[str, Any] | None:
    """定位消息里显式提到的附录。

    返回 `{"title": 命中标题, "all": False}`（具名命中，归一化子串匹配，
    忽略空格/标点差异）或 `{"title": "", "all": True}`（裸「附录」→ 全部附录节）；
    未提及返回 None。
    """
    from app.services.outline import normalize_title

    t = (text or "").strip()
    if not t or not appendices:
        return None
    tn = normalize_title(t)
    titled = [a for a in appendices if str(a.get("title") or "").strip()]
    titled.sort(key=lambda a: len(str(a.get("title"))), reverse=True)
    for app in titled:
        if normalize_title(str(app["title"])) in tn:
            return {"title": str(app["title"]), "all": False}
    if _APPENDIX_MENTION.search(t):
        return {"title": "", "all": True}
    return None


def resolve_chapter_in_volume(
    text: str, chapters: list[dict[str, Any]], volume_title: str
) -> str | None:
    """「第X章」序数在**指定卷内**定位（卷+章组合提及优先于全局序数）。"""
    t = (text or "").strip()
    if not t or not volume_title:
        return None
    m = _CHAPTER_MENTION.search(t)
    if not m:
        return None
    want = _ordinal_of(m.group(1))
    if want is None:
        return None
    in_vol = [
        c for c in chapters if str(c.get("volume_title") or "") == volume_title
    ]
    in_vol.sort(key=lambda c: int(c.get("sort_order") or 0))
    for idx, ch in enumerate(in_vol, start=1):
        if idx == want:
            return str(ch.get("id"))
    return None


# ---------------------------------------------------------------------------
# 明确上下文需求判定（两层保险的第二层门控）
# ---------------------------------------------------------------------------

#: 回顾/读取已生成内容的明确指令
_REVIEW_DEMAND_RE = re.compile(
    r"(?:回顾|总结|梳理|整理|复习|回想).{0,16}(?:前文|前面|已写|写过|的?章节|的?内容|的?剧情|伏笔|人物)|"
    r"(?:读|读取|查看|查阅|参考|看看|翻一下|打开|发我|给我看).{0,16}(?:第[一二三四五六七八九十百零两\d]+[卷章]|已写|前面|前文|的?章节|的?正文|的?内容)|"
    r"(?:前情提要|前情|已写(?:的)?(?:章节|内容|剧情))"
)
#: 基于大纲/附录/卷章内容生成或修改小说的明确指令
_OUTLINE_GEN_DEMAND_RE = re.compile(
    r"(?:大纲|附录|卷|章).{0,24}?(?:内容|简介|设定|信息).{0,24}?"
    r"(?:生成|写|撰写|创作|改写|修改|重写|续写|完善|补写)|"
    r"(?:依据|基于|按照|根据|参考).{0,16}(?:大纲|附录|简介|设定).{0,24}?"
    r"(?:生成|写|撰写|创作|改写|修改|重写|续写|完善|补写)"
)


def context_demand_kind(text: str) -> str:
    """用户消息是否含**明确**的上下文需求（两层保险第二层门控）。

    返回 "review"（回顾/读取已生成内容）/ "outline_gen"（基于大纲内容生成）/ ""（无）。
    无明确需求时上层**不得**自作主张注入或抓取任何内容。
    """
    t = (text or "").strip()
    if not t:
        return ""
    if _REVIEW_DEMAND_RE.search(t):
        return "review"
    if _OUTLINE_GEN_DEMAND_RE.search(t):
        return "outline_gen"
    return ""
