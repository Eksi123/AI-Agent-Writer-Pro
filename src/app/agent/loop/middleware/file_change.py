# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.file_change
文件名: file_change.py
作者: xionggaoqing
时间: 2026-10-27
说明: `FileChangeNotifyMiddleware`：`awrap_tool_call` 里做三件事——
     ① 写入**前**预读章节旧正文（供快照回退）；
     ② 工具成功后发 SSE `file.changed`（前端据此热更新正文/目录树）；
     ③ 写入成功后调 `services.volumes.record_agent_write` 登记这次直写
        （补修订快照 + 抬 `version` + 重算 `word_count` + 去抖触发章节摘要 + 审计），
        再同步派生索引（项目索引 / FTS / 孤儿卷自愈）。

    ③ 是必需的：Agent 写正文走文件系统工具而**不经过** `write_content`，
    不登记就会出现「AI 改过的章节保存时不报 409、直接整章覆盖且无快照可回退」。

    虚拟路径形如 `/projects/{pid}/chapters/v{vol}/c{ch}.md`。
    路径分类 `classify_change` 在 `harness/sandbox/virtual_path.py`（沙箱域语义，
    与 harness/lock 的编辑锁、harness/sandbox/safe_delete 共用），内部已做归一化。

    `ContentLockMiddleware` 已迁至 `harness/lock/content_lock.py`（安全措施归 harness）。
    本中间件同时挂在**主 Agent 与子代理**栈上——正文生成的主路径是 writer 子代理
    （见 `loop/prompts/main_agent.md.j2` 的委派铁律），只挂主栈等于没挂。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from app.agent.harness.sandbox.virtual_path import classify_change
from app.core.logging import get_logger

logger = get_logger(__name__)

_WRITE_TOOLS = ("write_file", "edit_file", "delete")


class FileChangeNotifyMiddleware(AgentMiddleware):
    """文件变更通知 + 派生索引同步。"""

    name = "FileChangeNotify"

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = request.tool_call.get("name") or ""
        is_write_tool = tool_name in _WRITE_TOOLS
        args = request.tool_call.get("args") or {}
        virtual_path = str(args.get("file_path") or args.get("path") or "")
        change = classify_change(virtual_path) if (is_write_tool and virtual_path) else {}
        in_project = bool(change.get("in_project"))
        chapter_id = change.get("chapter_id") if in_project else None
        volume_id = str(change.get("volume_id") or "") if in_project else ""
        change_kind = "delete" if tool_name == "delete" else ("edit" if tool_name == "edit_file" else "write")

        # ★ 必须在 handler **之前**读旧正文：Agent 直写文件不经过 `write_content`，
        #   漏了这一步就没有任何可回退的快照——AI 的产出被下一次写入覆盖后无从找回。
        pre_content: str | None = None
        pre_version = 0
        if chapter_id and change_kind != "delete":
            pre_content, pre_version = self._read_pre(change)

        result = await handler(request)

        if not is_write_tool or not in_project:
            return result
        if getattr(result, "status", None) == "error":
            return result

        context = getattr(request.runtime, "context", None)
        emit = getattr(context, "emit", None)
        project_id = getattr(context, "project_id", "") or change.get("project_id") or ""

        if emit is not None:
            await emit.publish(
                "file.changed",
                {
                    "virtual_path": change["virtual_path"],
                    "change_kind": change_kind,
                    "chapter_id": chapter_id,
                    "is_outline": change.get("is_outline", False),
                },
            )

        # ★ 写入成功后登记：补快照 + 抬 version + 重算 word_count + 触发摘要 + 审计。
        #   不抬 version 的话，用户端 buffer 的 base_version 永远「不冲突」，
        #   保存时会静默整章覆盖 AI 刚写的内容（既不报 409 也无快照可回退）。
        if chapter_id and volume_id and change_kind != "delete":
            await self._record_write(request, project_id, volume_id, chapter_id, pre_content, pre_version)

        await self._sync_derived(project_id, change, change_kind)
        return result

    @staticmethod
    def _read_pre(change: dict[str, Any]) -> tuple[str | None, int]:
        """读章节文件**写入前**的正文与 version，返回 `(content, version)`。

        失败返回 `(None, 0)` 而不是抛错：新建章节时文件尚不存在属正常情况，
        宁可不快照也不能阻断 Agent 的写入。

        ★ 连 version 一起读的原因：Agent 的 `write_file` 是**整文件覆盖**，模型给的
          frontmatter 往往来自它早先 `read_file` 到的旧内容。若那一版 version 比磁盘
          上的旧（同一轮里已经写过一次），覆盖就会把 version **打回去**；随后
          `record_agent_write` 再从被打回的值 +1，两次写入得到同一个 version，
          乐观锁就漏了——用户端陈旧 buffer 保存时不再报 409，静默覆盖 AI 的产出。
          故登记时以「写入前的 version」为下界取 max。
        """
        project_id = str(change.get("project_id") or "")
        vol_id = str(change.get("volume_id") or "")
        chapter_id = str(change.get("chapter_id") or "")
        if not (project_id and vol_id and chapter_id):
            return None, 0
        try:
            from app.services import files as files_service

            meta, content = files_service.read_chapter(project_id, vol_id, chapter_id)
            try:
                version = int(meta.get("version") or 0)
            except (TypeError, ValueError):
                version = 0
            return content, version
        except Exception as exc:  # noqa: BLE001 - 文件不存在 / frontmatter 脏数据
            logger.debug("agent_write_pre_content_unavailable", chapter_id=chapter_id, error=str(exc))
            return None, 0

    @staticmethod
    async def _record_write(
        request: Any,
        project_id: str,
        volume_id: str,
        chapter_id: str,
        pre_content: str | None,
        pre_version: int = 0,
    ) -> None:
        """调 `volumes.record_agent_write` 登记这次直写（失败只告警，不让工具调用报错）。"""
        if not project_id:
            return
        try:
            from app.db.session import session_scope
            from app.services import volumes as volumes_service

            context = getattr(request.runtime, "context", None)
            run_id = str(getattr(context, "run_id", "") or "") or None
            async with session_scope() as db:
                await volumes_service.record_agent_write(
                    db,
                    project_id,
                    volume_id,
                    chapter_id,
                    run_id=run_id,
                    pre_content=pre_content,
                    pre_version=pre_version,
                )
        except Exception as exc:  # noqa: BLE001 - 登记属派生动作，不得反噬写入结果
            logger.warning(
                "agent_write_record_failed", project_id=project_id, chapter_id=chapter_id, error=str(exc)
            )

    async def _sync_derived(self, project_id: str, change: dict[str, Any], change_kind: str) -> None:
        """同步派生索引（失败不阻断，标 dirty 待全量重建）。"""
        if not project_id:
            return
        try:
            from app.db.session import session_scope
            from app.services import novels as novels_service
            from app.services import search as search_service

            async with session_scope() as db:
                if change.get("is_outline"):
                    # 大纲是参考资料，不再回填卷章 sort_order；只重建检索索引
                    await search_service.reindex_project(db, project_id)
                    await novels_service.refresh_project_index(db, project_id)
                elif change.get("chapter_id"):
                    from app.services import volumes as volumes_service

                    # Agent 直写章节文件可能绕过卷登记 → 自愈登记孤儿卷，目录即时可见。
                    # 这两个函数是**同步遍历工作区文件**的，放事件循环里会卡住
                    # SSE 推送与所有并发请求，故一律丢到线程池。
                    await asyncio.to_thread(volumes_service.register_orphan_volumes, project_id)
                    # 章节删除已由 SafeDeleteMiddleware 走 `volumes.delete_chapter`
                    # （含「章节删空 → 卷一并删除」级联），这里的剪空卷只是**兜底**：
                    # 覆盖 delete 落到非章节路径、或服务层级联失败的情况。
                    if change_kind == "delete":
                        await asyncio.to_thread(volumes_service.prune_empty_volumes, project_id)
                    await novels_service.refresh_project_index(db, project_id)
                    await search_service.reindex_project(db, project_id)
                else:
                    await novels_service.refresh_project_index(db, project_id)
        except Exception as exc:
            logger.warning("derived_index_sync_failed", project_id=project_id, error=str(exc))
            await self._mark_dirty(project_id)

    @staticmethod
    async def _mark_dirty(project_id: str) -> None:
        try:
            from sqlalchemy import update

            from app.db.models import ProjectIndex
            from app.db.session import session_scope

            async with session_scope() as db:
                await db.execute(
                    update(ProjectIndex).where(ProjectIndex.project_id == project_id).values(dirty=1)
                )
        except Exception:  # pragma: no cover
            pass
