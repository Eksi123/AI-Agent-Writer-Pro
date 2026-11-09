# -*- coding: utf-8 -*-
"""
模块: app.agent.tools.builtin.create_structure
文件名: create_structure.py
作者: xionggaoqing
时间: 2026-11-08
说明: Agent 内置工具：卷/章的**建**与**改名**（`create_volume` / `create_chapter` /
     `rename_volume` / `rename_chapter`）。

    为什么需要它们——这是对边界约定（手册「二、(3)」：写作业务函数不作为 Agent 工具）
    的一次**有意收窄的例外**。原约定让 Agent 用文件工具直接建卷建章，后果是：

      · 建卷 = 手写 `volumes.yaml` + 手建 `chapters/v{id}/` 目录 + 手造 uuid，
        Agent 做不到（`volumes.yaml` 的 id/sort_order/时间戳格式它只能猜），
        实际表现为「卷建不出来」或「建出孤儿目录等 `register_orphan_volumes` 兜底」；
      · 改名 = 改文件里的 frontmatter title，但 `volumes.yaml` 里的卷名不会跟着变，
        于是目录树与正文标题不一致；
      · 两者都绕过校验（重名、非法标题字符）、审计、索引刷新与前端热更新。

    所以这四个工具**只做结构操作**（建/改名），不碰正文内容——正文仍然一律走
    文件工具（`write_file`/`edit_file`），由 `FileChangeNotifyMiddleware` 登记。
    工具本体只是 `services/volumes.py` 里既有函数的薄封装，业务规则不重复实现。

    ★ `rename_*` **只改名字**，绝不改 `sort_order`、绝不触发 `_renumber_chapters`
      （后者会把标题里的「第X章」序数词按位置重写，等于把改名改回去）。
      底层用的是 `update_volume` / `update_chapter_meta`，二者均已核对为纯改名。

    ★ 改名能生效的前提是 `register_orphan_volumes` 不再纠偏已登记卷的卷名
      （旧实现会在每次 `GET /volumes` 时把卷名改回大纲里的名字）。该纠偏已收窄为
      「仅对首次发现的孤儿卷目录取名」，见 `services/volumes.py`。

    这些工具不需要审批（审批只保留给 `delete` 与「整篇读大纲」两处），
    也**只挂主 Agent**：结构变更影响全局目录，不应由 writer 之类的子代理自行决定。
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from app.core.logging import get_logger
from app.db.session import session_scope
from app.services.outline import normalize_title

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 内部工具：上下文、卷/章寻址、变更通知
# ---------------------------------------------------------------------------


def _context(runtime: ToolRuntime) -> tuple[str, str, Any]:
    """取 `(project_id, run_id, emit)`。"""
    context = getattr(runtime, "context", None)
    project_id = str(getattr(context, "project_id", "") or "")
    run_id = str(getattr(context, "run_id", "") or "")
    return project_id, run_id, getattr(context, "emit", None)


def _err(message: str, **extra: Any) -> str:
    return json.dumps({"ok": False, "error": message, **extra}, ensure_ascii=False)


async def _resolve_volume(project_id: str, volume: str) -> tuple[str | None, str]:
    """把「卷 id 或卷名」解析为 vol_id。

    Returns:
        `(vol_id, error)`；成功时 error 为空串。卷名匹配用 `normalize_title`
        归一（忽略空格与全半角差异），与大纲匹配逻辑一致。
    """
    from app.services import files as files_service

    key = (volume or "").strip()
    if not key:
        return None, "请提供 volume（卷 id 或卷名）。"
    volumes = files_service.read_volumes(project_id)
    found = files_service.find_volume(volumes, key)
    if found is not None:
        return str(found.get("id")), ""
    target = normalize_title(key)
    hits = [v for v in volumes if normalize_title(str(v.get("title") or "")) == target]
    if len(hits) == 1:
        return str(hits[0].get("id")), ""
    if len(hits) > 1:
        return None, f"卷名「{key}」在本项目内有 {len(hits)} 个同名卷，请改用卷 id 指定。"
    available = [f"{v.get('title')}({v.get('id')})" for v in volumes][:20]
    return None, f"未找到卷「{key}」。现有卷：{'、'.join(available) or '（无）'}"


async def _resolve_chapter(
    db: Any, project_id: str, chapter: str, volume: str = ""
) -> tuple[str | None, str | None, str]:
    """把「章 id 或章名」解析为 `(chapter_id, vol_id, error)`。

    给了 `volume` 就只在该卷内找（同名章跨卷时消歧）；否则全项目扫描，
    命中多个则要求消歧而不是猜。
    """
    from app.services import files as files_service
    from app.services import volumes as volumes_service

    key = (chapter or "").strip()
    if not key:
        return None, None, "请提供 chapter（章 id 或章名）。"

    vol_ids: list[str] = []
    if volume.strip():
        vid, err = await _resolve_volume(project_id, volume)
        if vid is None:
            return None, None, err
        vol_ids = [vid]
    else:
        vol_ids = [str(v.get("id")) for v in files_service.read_volumes(project_id)]

    target = normalize_title(key)
    hits: list[tuple[str, str]] = []
    for vid in vol_ids:
        for ch in await volumes_service.list_chapters(db, project_id, vid):
            cid = str(ch.get("id") or "")
            if cid == key or normalize_title(str(ch.get("title") or "")) == target:
                hits.append((cid, vid))
    if len(hits) == 1:
        return hits[0][0], hits[0][1], ""
    if len(hits) > 1:
        return None, None, (
            f"章名「{key}」命中 {len(hits)} 章，请补充 volume（卷 id 或卷名）消歧，或直接给章 id。"
        )
    return None, None, f"未找到章「{key}」。"


async def _notify(
    runtime: ToolRuntime,
    project_id: str,
    *,
    virtual_path: str,
    change_kind: str,
    chapter_id: str | None = None,
) -> None:
    """发 `file.changed` 让前端热更新目录树，并重建派生索引。

    这些工具不走文件系统工具，`FileChangeNotifyMiddleware` 不会自动触发，
    必须自己发事件 + 自己刷索引，否则用户得刷新页面才看得到新卷/新名字。
    失败只记日志：结构变更已经落盘，不能让通知失败把成功的工具调用变成报错。
    """
    _pid, _run_id, emit = _context(runtime)
    if emit is not None:
        try:
            await emit.publish(
                "file.changed",
                {
                    "virtual_path": virtual_path,
                    "change_kind": change_kind,
                    "chapter_id": chapter_id,
                    "is_outline": False,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("create_structure_emit_failed", error=str(exc))

    try:
        from app.services import novels as novels_service
        from app.services import search as search_service

        async with session_scope() as db:
            await novels_service.refresh_project_index(db, project_id)
            await search_service.reindex_project(db, project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_structure_reindex_failed", project_id=project_id, error=str(exc))


# ---------------------------------------------------------------------------
# 工具本体
# ---------------------------------------------------------------------------


@tool("create_volume", parse_docstring=True)
async def create_volume(
    runtime: ToolRuntime,
    title: str,
    chapters: list[str],
    summary: str = "",
) -> str:
    """新建一卷，并在卷内一次建好若干章（卷与章同时诞生，原子操作）。

    这是新建卷的**唯一**正确方式：不要用 write_file 手写 volumes.yaml，
    也不要用 mkdir 建 chapters/v* 目录——那样 id、sort_order 与时间戳都是错的。

    任一章创建失败会**整体回滚**（已建的章与本卷登记一并撤销），不会留下半截卷。
    建完即可用 write_file 往 `chapters/v{卷id}/c{章id}.md` 写正文。

    Args:
        title: 卷名（如「第一卷 谪凡间」）。
        chapters: 章名列表，按顺序成为卷内第 1..N 章（如 ["第一章 青衣客", "第二章 渡江"]）。
            至少给一章——本项目不支持空卷。
        summary: 卷简介（可选）。

    Returns:
        JSON 字符串：`{ok, volume_id, volume_title, chapters:[{id,title}]}`。
    """
    project_id, _run_id, _emit = _context(runtime)
    if not project_id:
        return _err("缺少项目上下文")

    names = [str(t).strip() for t in (chapters or []) if str(t or "").strip()]
    if not names:
        return _err("chapters 不能为空：本项目不支持空卷，请至少给出第一章的章名。")

    from app.services import volumes as volumes_service

    try:
        async with session_scope() as db:
            vol = await volumes_service.create_volume(
                db, project_id, title=title, summary=summary or "", chapters=names
            )
    except Exception as exc:  # noqa: BLE001 - 校验失败/回滚都要如实回给模型
        logger.warning("agent_create_volume_failed", project_id=project_id, error=str(exc))
        return _err(f"建卷失败：{exc}")

    vol_id = str(vol.get("id") or "")
    created = [
        {"id": str(c.get("id") or ""), "title": str(c.get("title") or "")}
        for c in (vol.get("chapters") or [])
    ]
    await _notify(
        runtime,
        project_id,
        virtual_path=f"/projects/{project_id}/chapters/v{vol_id}/",
        change_kind="create",
    )
    return json.dumps(
        {
            "ok": True,
            "volume_id": vol_id,
            "volume_title": vol.get("title"),
            "chapters": created,
            "hint": "正文请用 write_file 写入 chapters/v{volume_id}/c{chapter_id}.md",
        },
        ensure_ascii=False,
    )


@tool("create_chapter", parse_docstring=True)
async def create_chapter(
    runtime: ToolRuntime, volume: str, title: str, position: int = 0
) -> str:
    """在**已存在**的卷里新建一章（新卷请用 create_volume 一次建卷+章）。

    不要用 write_file 直接造章节文件：那样 `volumes.yaml` 里没有登记，
    目录树看不到，且要靠孤儿卷自愈兜底，字数统计与检索索引也不会更新。

    Args:
        volume: 目标卷（卷 id 或卷名）。
        title: 章名（如「第三章 江上雪」）。
        position: 插入到卷内第几章（1-based）。0 或不传 = 追加到卷末。
            插入会让其后各章顺延，并**同步重写它们标题里的「第X章」序数词**。

    Returns:
        JSON 字符串：`{ok, chapter_id, title, volume_id, path}`。
    """
    project_id, _run_id, _emit = _context(runtime)
    if not project_id:
        return _err("缺少项目上下文")

    vol_id, err = await _resolve_volume(project_id, volume)
    if vol_id is None:
        return _err(err)

    from app.services import volumes as volumes_service

    try:
        async with session_scope() as db:
            ch = await volumes_service.create_chapter(
                db,
                vol_id,
                title=title,
                content="",
                ordinal=int(position) if int(position or 0) > 0 else None,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_create_chapter_failed", project_id=project_id, error=str(exc))
        return _err(f"建章失败：{exc}")

    chapter_id = str(ch.get("id") or "")
    await _notify(
        runtime,
        project_id,
        virtual_path=f"/projects/{project_id}/chapters/v{vol_id}/c{chapter_id}.md",
        change_kind="create",
        chapter_id=chapter_id,
    )
    return json.dumps(
        {
            "ok": True,
            "chapter_id": chapter_id,
            "title": ch.get("title"),
            "volume_id": vol_id,
            "path": f"chapters/v{vol_id}/c{chapter_id}.md",
            "hint": "正文请用 write_file 写入该路径。",
        },
        ensure_ascii=False,
    )


@tool("rename_volume", parse_docstring=True)
async def rename_volume(runtime: ToolRuntime, volume: str, new_title: str) -> str:
    """改卷名。**只改名字**：不动卷顺序、不动卷内章、不动任何正文。

    改名后目录树即时更新，不会被大纲「纠偏」改回去（大纲只是参考资料）。
    不要用 edit_file 去改 volumes.yaml 或章节 frontmatter 来改名——那样卷名与
    章内记录会不一致。

    Args:
        volume: 目标卷（卷 id 或卷名）。
        new_title: 新卷名。

    Returns:
        JSON 字符串：`{ok, volume_id, from, to}`。
    """
    project_id, _run_id, _emit = _context(runtime)
    if not project_id:
        return _err("缺少项目上下文")

    vol_id, err = await _resolve_volume(project_id, volume)
    if vol_id is None:
        return _err(err)

    from app.services import volumes as volumes_service

    try:
        async with session_scope() as db:
            _pid, _volumes, vol = volumes_service.locate_volume(vol_id, project_id)
            old_title = str(vol.get("title") or "")
            updated = await volumes_service.update_volume(db, vol_id, title=new_title)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_rename_volume_failed", project_id=project_id, error=str(exc))
        return _err(f"改卷名失败：{exc}")

    await _notify(
        runtime,
        project_id,
        virtual_path=f"/projects/{project_id}/chapters/v{vol_id}/",
        change_kind="rename",
    )
    return json.dumps(
        {
            "ok": True,
            "volume_id": vol_id,
            "from": old_title,
            "to": updated.get("title"),
        },
        ensure_ascii=False,
    )


@tool("rename_chapter", parse_docstring=True)
async def rename_chapter(
    runtime: ToolRuntime, chapter: str, new_title: str, volume: str = ""
) -> str:
    """改章名。**只改名字**：不动章顺序、不动卷、不动正文、不递增 version。

    ⚠️ 若新章名带「第X章」前缀而与该章在卷内的实际位置不符，之后任何触发
    卷内重新编号的操作（建章插入 / 删章 / 移动章）都会把序数词按位置改回去
    （例如把「第九章 别的名字」改成「第三章 别的名字」，后半截保留）。
    想避免就别在章名里写序数词，或让序数词与位置一致。

    Args:
        chapter: 目标章（章 id 或章名）。
        new_title: 新章名。
        volume: 章所在卷（卷 id 或卷名）。同名章跨卷时必填，用于消歧。

    Returns:
        JSON 字符串：`{ok, chapter_id, from, to, volume_id}`。
    """
    project_id, _run_id, _emit = _context(runtime)
    if not project_id:
        return _err("缺少项目上下文")

    from app.services import volumes as volumes_service

    try:
        async with session_scope() as db:
            chapter_id, vol_id, err = await _resolve_chapter(db, project_id, chapter, volume)
            if chapter_id is None or vol_id is None:
                return _err(err)
            _pid, _vid, meta, _content = volumes_service.locate_chapter(chapter_id, project_id)
            old_title = str(meta.get("title") or "")
            updated = await volumes_service.update_chapter_meta(db, chapter_id, title=new_title)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_rename_chapter_failed", project_id=project_id, error=str(exc))
        return _err(f"改章名失败：{exc}")

    await _notify(
        runtime,
        project_id,
        virtual_path=f"/projects/{project_id}/chapters/v{vol_id}/c{chapter_id}.md",
        change_kind="rename",
        chapter_id=chapter_id,
    )
    return json.dumps(
        {
            "ok": True,
            "chapter_id": chapter_id,
            "volume_id": vol_id,
            "from": old_title,
            "to": updated.get("title"),
        },
        ensure_ascii=False,
    )


__all__ = ["create_chapter", "create_volume", "rename_chapter", "rename_volume"]
