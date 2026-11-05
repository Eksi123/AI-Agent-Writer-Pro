# -*- coding: utf-8 -*-
"""
模块: app.services.read_grant
文件名: read_grant.py
作者: xionggaoqing
时间: 2026-11-05
说明: 会话级「读取授权」注册表。

    AI 读取大纲 / 章节正文属于高 token 成本操作，默认需要用户授权；
    授权按**会话**记忆（一次允许后本会话不再询问），进程内单例，
    与 RunManager 同生命周期（重启即清空，保守起见重新询问）。
"""

from __future__ import annotations

import asyncio
import re

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 授权范围：大纲文件 / 章节正文
SCOPES = ("outline", "chapters")

#: 显式读取意图：**必须有读取类动词**（读/查看/参考/回顾…）指向大纲或章节类宾语，
#: 或宾语在前、动词在后（「大纲内容我要看看」）。
#: 只有「大纲/章/正文」等名词而无读取动词时**不算**——例如「按大纲生成第一章正文」
#: 是写作意图（由读取守卫档② 放行），不应因此授予会话级读取授权。
_READ_VERBS = r"(?:读取|读一下|读|查看|查阅|参考|回顾|看看|翻一下|打开|发我|给我看)"
_READ_OBJECTS = r"(?:章|卷|大纲|全书|前文|正文|内容|简介)"
EXPLICIT_READ_RE = re.compile(
    rf"{_READ_VERBS}.{{0,24}}?{_READ_OBJECTS}|{_READ_OBJECTS}.{{0,12}}?{_READ_VERBS}"
)

#: 前情提要请求：用户要「回顾/总结已写内容」——比读取授权判定更宽。
#: 命中时用确定性的 `build_recap` 注入（零额外 LLM 调用），
#: 比派发 recaller 子代理（一次 LLM 调用）更省 token，故优先走这条。
_RECAP_RE = re.compile(
    r"(?:前情提要|前情|已写(?:的)?(?:章节|内容|剧情)|前面(?:写的|的剧情|的内容|章节)|"
    r"(?:回顾|总结|梳理|整理).{0,12}(?:前文|前面|剧情|内容|已写)|全书(?:信息|内容|设定))"
)


def mentions_explicit_read(text: str) -> bool:
    """用户消息是否显式要求读取大纲/章节内容（读取授权守卫放行档③）。"""
    return bool(EXPLICIT_READ_RE.search((text or "").strip()))


def wants_recap(text: str) -> bool:
    """用户消息是否要求回顾已写内容（前情提要按需注入的门控）。

    比 `mentions_explicit_read` 宽：包含「总结一下前情提要」「梳理前面写的剧情」
    这类没有明确读取动词、但意图就是要回顾的请求。
    """
    t = (text or "").strip()
    return bool(_RECAP_RE.search(t)) or mentions_explicit_read(t)


class ReadGrantRegistry:
    """session_id → 已授权 scope 集合。"""

    def __init__(self) -> None:
        self._grants: dict[str, set[str]] = {}
        self._mu = asyncio.Lock()

    def is_granted(self, session_id: str, scope: str) -> bool:
        return scope in self._grants.get(session_id, set())

    async def grant(self, session_id: str, scope: str) -> None:
        if not session_id or scope not in SCOPES:
            return
        async with self._mu:
            self._grants.setdefault(session_id, set()).add(scope)
        logger.info("read_grant_granted", session_id=session_id, scope=scope)

    async def revoke_session(self, session_id: str) -> None:
        async with self._mu:
            self._grants.pop(session_id, None)


#: 进程内单例
read_grants = ReadGrantRegistry()
