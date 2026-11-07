# -*- coding: utf-8 -*-
"""
模块: app.services.volumes
文件名: volumes.py
作者: xionggaoqing
时间: 2026-10-27
说明: 卷 / 章服务：CRUD、正文读写（乐观锁）、修订快照与回滚。

    手册约束：
    - **短 ID 永不变**（`v{vol_id}` / `c{chapter_id}`），文件名与目录名含 ID；
    - 显式命名（42201）；
    - 正文写入乐观锁：`base_version` 不符 → 409 / `40901` 并回传最新内容；
    - 修订快照触发：手动保存 / Agent 写章前 / 章节定稿；**自动保存不快照**；
    - 删除卷级联删除其下章节文件。

    顺序模型变更（大纲降级为「参考资料」而非结构约束）：
    - `sort_order` 由本服务直接维护：不传 `ordinal` → 追加到末尾（`max+1`）；
      传 `ordinal` → 插入到该位置，**其后各项自动顺延 +1**（往前/往后插入卷章）；
    - 删除章后卷内章重新连续编号（1..N）；**某卷下章节删空后该卷一并删除**，卷层同样重新连续编号；
      重编号同时同步卷/章标题与大纲文件中的序数词（消除大纲校验的橙色警告）；
      `renumber_all` 提供幂等的修复入口（`POST /novels/{id}/volumes/renumber`）；
    - 仍不提供跨卷移动 / 任意拖拽重排：位置编辑入口为「插入」与「章节重定位」
      （`reposition_chapter`：卷内移动到指定位置后全卷重新连续编号）；
    - 大纲保存不再回填 `sort_order`（见 `services/story.py`），大纲仅作 AI 创作的参考来源。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BadRequestError, NotFoundError, VersionConflictError
from app.core.logging import get_logger
from app.core.paths import chapter_file, volume_dir, workspaces_root
from app.db.base import new_id, short_id, utcnow
from app.db.models import ChapterRevision
from app.services import audit, files, novels, recycle
from app.services.naming import validate_chapter_title, validate_volume_title
from app.services.outline import normalize_title, parse_outline
from app.services.workspace import WorkspaceGuard

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 卷
# ---------------------------------------------------------------------------


def meta_int(meta: dict[str, Any], key: str, default: int) -> int:
    """frontmatter 数值容错解析。

    Agent 直写文件时可能产出非规范值（如 `version: v1`），`int()` 直转会抛
    ValueError 把目录/正文接口打成 500。这里提取字符串里的数字，取不到回落默认值。
    """
    raw = meta.get(key)
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return int(digits) if digits else default


def _vol_dto(vol: dict[str, Any], chapters: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    dto = {
        "id": vol.get("id"),
        "title": vol.get("title"),
        "summary": vol.get("summary") or "",
        "sort_order": meta_int(vol, "sort_order", 0),
        "created_at": vol.get("created_at"),
        "updated_at": vol.get("updated_at"),
    }
    if chapters is not None:
        dto["chapters"] = chapters
        dto["chapter_count"] = len(chapters)
        dto["word_count"] = sum(meta_int(c, "word_count", 0) for c in chapters)
    return dto


def _chapter_dto(meta: dict[str, Any], chapter_id: str, vol_id: str) -> dict[str, Any]:
    return {
        "id": chapter_id,
        "volume_id": vol_id,
        "title": meta.get("title"),
        "sort_order": meta_int(meta, "sort_order", 0),
        "word_count": meta_int(meta, "word_count", 0),
        "status": meta.get("status") or "draft",
        "version": meta_int(meta, "version", 1),
        "updated_at": meta.get("updated_at"),
    }


def _outline_volume_sections(project_id: str) -> list[tuple[str, set[str]]]:
    """大纲卷节清单 `[(卷名, {归一化章名})]`（只含有章小节的卷节）。

    文档标题 H1 / 附录节因没有章小节而自然排除；用于孤儿卷登记与卷名纠偏时
    按「章节名交集」把卷目录匹配到大纲卷节。
    """
    try:
        meta = files.read_novel_meta(project_id)
        rel = str(meta.get("outline_file") or "").strip()
        if not rel:
            return []
        path = workspaces_root() / project_id / rel
        if not path.is_file():
            return []
        parsed = parse_outline(path.read_text(encoding="utf-8"))
        return [(v.title, {c.normalized for c in v.chapters}) for v in parsed.volumes if v.chapters]
    except Exception as exc:  # 大纲缺失/解析失败不阻断自愈
        logger.warning("outline_volume_sections_failed", project_id=project_id, error=str(exc))
        return []


def _match_outline_volume(chapter_norms: set[str], sections: list[tuple[str, set[str]]]) -> str | None:
    for title, norms in sections:
        if norms & chapter_norms:
            return title
    return None


def register_orphan_volumes(project_id: str) -> list[str]:
    """自愈登记「孤儿卷目录」+ 纠偏错误卷名。

    Agent 可能绕过卷章服务直接 `write_file` 到 `chapters/v{X}/c{Y}.md`（自造路径），
    导致章节文件已存在但 `volumes.yaml` 里没有该卷 → 目录树为空；或登记时卷名
    取错来源（如误用首章标题）→ 目录里「卷」显示成章名。

    卷名的唯一权威来源是**大纲的卷节（H1）**：按「卷目录内章节名 ∩ 大纲卷节章名」
    匹配；匹配不到才回落「卷 N」。已登记但卷名不在大纲卷节集合内的，同样纠偏。

    Returns:
        本次新登记的卷 id 列表（空 = 无需登记；纠偏不计入返回值）。
    """
    volumes = files.read_volumes(project_id)
    registered = {str(v.get("id")) for v in volumes}
    sections = _outline_volume_sections(project_id)
    outline_titles = {normalize_title(t) for t, _ in sections}
    chapters_root = workspaces_root() / project_id / "chapters"
    if not chapters_root.is_dir():
        return []

    def _volume_facts(vdir: Any) -> tuple[set[str], int, str]:
        """卷目录 → (归一化章名集合, 首章序号, 首章标题)。"""
        norms: set[str] = set()
        first_order = 0
        first_title = ""
        for cfile in sorted(vdir.glob("c*.md")):
            try:
                meta = files.read_chapter_meta(project_id, vdir.name[1:], cfile.stem[1:])
            except Exception:  # pragma: no cover - 坏文件跳过
                continue
            title = str(meta.get("title") or "")
            norms.add(normalize_title(title))
            if not first_title:
                first_title = title
                first_order = meta_int(meta, "sort_order", 0)
        return norms, first_order, first_title

    added: list[dict[str, Any]] = []
    now = utcnow()
    changed = False

    # ① 纠偏：已登记卷名不在大纲卷节集合内 → 按章节交集改回大纲卷名
    for vol in volumes:
        vid = str(vol.get("id"))
        vdir = chapters_root / f"v{vid}"
        if not vdir.is_dir():
            continue
        if normalize_title(str(vol.get("title") or "")) in outline_titles:
            continue
        norms, _order, _title = _volume_facts(vdir)
        matched = _match_outline_volume(norms, sections)
        if matched and matched != vol.get("title"):
            logger.warning(
                "volume_title_corrected", project_id=project_id, volume_id=vid,
                old=vol.get("title"), new=matched,
            )
            vol["title"] = matched
            vol["updated_at"] = now
            changed = True

    # ② 登记：未登记的卷目录补进 volumes.yaml（卷名取大纲卷节）
    for vdir in sorted(chapters_root.iterdir()):
        if not vdir.is_dir() or not vdir.name.startswith("v"):
            continue
        vol_id = vdir.name[1:]
        if vol_id in registered or not vol_id:
            continue
        if not any(vdir.glob("c*.md")):
            continue  # 空目录不登记
        norms, first_order, first_title = _volume_facts(vdir)
        title = _match_outline_volume(norms, sections) or first_title or f"卷 {len(registered) + len(added) + 1}"
        added.append(
            {
                "id": vol_id,
                "title": title,
                "summary": "",
                "sort_order": first_order or (len(registered) + len(added) + 1),
                "created_at": now,
                "updated_at": now,
            }
        )

    if added or changed:
        files.write_volumes(project_id, volumes + added if added else volumes)
    if added:
        ids = [str(v["id"]) for v in added]
        logger.warning("orphan_volumes_registered", project_id=project_id, volume_ids=ids)
        return ids
    return []


async def list_volumes(session: AsyncSession, project_id: str) -> list[dict[str, Any]]:
    """卷列表（含各卷章节）。按 `sort_order` 只读排序渲染。

    读取前先自愈登记孤儿卷目录（Agent 直写章节文件而未登记卷的兜底）。
    """
    await novels.get_novel(session, project_id)  # 404 校验
    register_orphan_volumes(project_id)
    volumes = sorted(
        files.read_volumes(project_id),
        key=lambda v: (int(v.get("sort_order") or 0), str(v.get("title") or "")),
    )
    result = []
    for vol in volumes:
        vol_id = str(vol.get("id"))
        chapters = await list_chapters(session, project_id, vol_id)
        result.append(_vol_dto(vol, chapters))
    return result


# ---------------------------------------------------------------------------
# 序号（位置）编辑 —— 插入即唯一的位置编辑入口
# ---------------------------------------------------------------------------


def _validate_ordinal(ordinal: int | None, count: int, *, kind: str) -> int | None:
    """校验插入序号：正整数且 1..count+1（count+1 = 追加到末尾）。

    不传 ordinal → None（保持旧行为：追加末尾）。违规抛 400。
    """
    if ordinal is None:
        return None
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
        raise BadRequestError(f"{kind}序号必须是正整数")
    if ordinal > count + 1:
        raise BadRequestError(f"{kind}序号超出范围（当前共 {count} 个，允许 1~{count + 1}）")
    return ordinal


def _shift_ordinal(items: list[tuple[Any, int]], position: int) -> None:
    """把 `position` 及之后的项顺延 +1。`items` 为 `(可写字典, 当前sort_order)` 列表。"""
    for obj, order in items:
        if order >= position:
            obj["sort_order"] = order + 1


async def create_volume(
    session: AsyncSession,
    project_id: str,
    *,
    title: str,
    summary: str = "",
    ordinal: int | None = None,
    first_chapter_title: str | None = None,
) -> dict[str, Any]:
    """新建卷：**显式命名**。

    - 不传 `ordinal` → 追加到末尾（`sort_order = max+1`，兼容旧行为）；
    - 传 `ordinal`→ 插入到该位置，其后各卷顺延 +1；
    - 传 `first_chapter_title` → 卷建立后立刻在它里面建第一章
      （**不支持空卷**，插入卷必须同时给出第一章的章节名）。
    """
    await novels.get_novel(session, project_id)
    clean = validate_volume_title(title)
    volumes = files.read_volumes(project_id)

    position = _validate_ordinal(ordinal, len(volumes), kind="卷")
    if position is not None:
        _shift_ordinal(
            [(v, int(v.get("sort_order") or 0)) for v in volumes], position
        )

    vol_id = short_id({str(v.get("id")) for v in volumes})
    now = utcnow()
    vol = {
        "id": vol_id,
        "title": clean,
        "summary": summary or "",
        "sort_order": position if position is not None else files.next_volume_sort(volumes),
        "created_at": now,
        "updated_at": now,
    }
    volumes.append(vol)
    files.write_volumes(project_id, volumes)
    volume_dir(project_id, vol_id).mkdir(parents=True, exist_ok=True)

    # 插入（ordinal）会顺延其后各卷 → 重新连续编号并同步标题/大纲序数，避免序数告警
    if position is not None:
        _renumber_volumes(volumes)
        files.write_volumes(project_id, volumes)
        _sync_outline_volume_ordinals(project_id, volumes)

    await audit.record(
        session,
        "volume.create",
        entity_type="volume",
        entity_id=f"{project_id}:{vol_id}",
        detail={"title": clean, "sort_order": vol["sort_order"], "inserted_at": position},
        commit=True,
    )
    logger.info("volume_created", project_id=project_id, volume_id=vol_id, title=clean)

    # 插入卷时一并创建它的第一章（避免空卷）
    first_chapter: dict[str, Any] | None = None
    if first_chapter_title and first_chapter_title.strip():
        first_chapter = await create_chapter(
            session, vol_id, title=first_chapter_title, content="", ordinal=1
        )

    chapters = [first_chapter] if first_chapter is not None else []
    return _vol_dto(vol, chapters)


async def update_volume(
    session: AsyncSession, vol_id: str, *, title: str | None = None, summary: str | None = None
) -> dict[str, Any]:
    """改卷名 / 简介。**不改 ID、不改目录名、不改 sort_order**。"""
    project_id, volumes, vol = _locate_volume(vol_id)
    changes: dict[str, Any] = {}

    if title is not None:
        clean = validate_volume_title(title)
        if clean != vol.get("title"):
            changes["title"] = {"from": vol.get("title"), "to": clean}
            vol["title"] = clean
    if summary is not None and summary != (vol.get("summary") or ""):
        changes["summary"] = summary
        vol["summary"] = summary

    if not changes:
        return _vol_dto(vol, await list_chapters(session, project_id, vol_id))

    vol["updated_at"] = utcnow()
    files.write_volumes(project_id, volumes)
    await audit.record(
        session,
        "volume.update",
        entity_type="volume",
        entity_id=f"{project_id}:{vol_id}",
        detail=changes,
        commit=True,
    )
    return _vol_dto(vol, await list_chapters(session, project_id, vol_id))


async def delete_volume(session: AsyncSession, vol_id: str) -> dict[str, Any]:
    """删除卷：**级联删除其下章节文件**（前端二次确认列出章名与字数）。

    删除后其余卷重新连续编号（1..N）。
    其下章节先进回收站（可回收 / 彻底删除），再删文件。
    """
    project_id, volumes, vol = _locate_volume(vol_id)
    guard = WorkspaceGuard(project_id)
    novel_title = await recycle.get_novel_title(session, project_id)

    chapters = await list_chapters(session, project_id, vol_id)
    removed_files: list[str] = []
    for ch in chapters:
        # 归档到回收站（卷名/序号一并快照，供「卷 → 被删章节」目录展示）
        recycle.archive_chapter(
            project_id,
            vol_id=vol_id,
            volume_title=str(vol.get("title") or ""),
            volume_sort_order=int(vol.get("sort_order") or 0),
            chapter_id=str(ch["id"]),
            meta=ch,
            novel_title=novel_title,
        )
        rel = f"chapters/v{vol_id}/c{ch['id']}.md"
        if guard.exists(rel):
            guard.delete(rel)
            removed_files.append(rel)

    # 卷目录本身
    vdir = volume_dir(project_id, vol_id)
    if vdir.exists():
        guard.delete(f"chapters/v{vol_id}")

    remaining = [v for v in volumes if str(v.get("id")) != vol_id]
    _renumber_volumes(remaining)
    files.write_volumes(project_id, remaining)

    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "volume.delete",
        entity_type="volume",
        entity_id=f"{project_id}:{vol_id}",
        detail={"title": vol.get("title"), "chapters_deleted": len(removed_files)},
        commit=True,
    )
    logger.info(
        "volume_deleted", project_id=project_id, volume_id=vol_id, chapters=len(removed_files)
    )
    return {"id": vol_id, "deleted": True, "chapters_deleted": len(removed_files)}


def prune_empty_volumes(project_id: str) -> list[str]:
    """剪掉「章节已删空」的卷（Agent 用 `delete` 工具直删章文件时的自愈）。

    「某卷章节删空 → 卷一并删除」的规则在 `delete_chapter`（REST 路径）里；
    Agent 经 `delete` 工具直接删章文件会绕过它，留下 0 章空卷（目录里显示空卷）。
    本函数按文件事实自愈：已登记卷的卷目录不存在或不含任何 `c*.md`
    → 从 `volumes.yaml` 移除（空目录一并删除）+ 其余卷重新连续编号 + 同步大纲卷序数。

    Returns:
        被剪掉的卷 id 列表（空 = 无需处理）。
    """
    volumes = files.read_volumes(project_id)
    if not volumes:
        return []
    guard = WorkspaceGuard(project_id)
    keep: list[dict[str, Any]] = []
    pruned: list[str] = []
    for vol in volumes:
        vid = str(vol.get("id"))
        vdir = volume_dir(project_id, vid)
        if vdir.is_dir() and any(vdir.glob("c*.md")):
            keep.append(vol)
            continue
        pruned.append(vid)
        if vdir.is_dir():
            try:
                guard.delete(f"chapters/v{vid}")
            except Exception as exc:  # pragma: no cover - 空目录删除失败不阻断
                logger.warning("empty_volume_dir_delete_failed", project_id=project_id, volume_id=vid, error=str(exc))
    if not pruned:
        return []
    _renumber_volumes(keep)
    files.write_volumes(project_id, keep)
    _sync_outline_volume_ordinals(project_id, keep)
    logger.info("empty_volumes_pruned", project_id=project_id, volumes=pruned)
    return pruned


def _renumber_volumes(volumes: list[dict[str, Any]]) -> None:
    """按当前 `sort_order` 次序把卷重新连续编号为 1..N（原地修改）。

    同时同步标题里的序数词（如「第二卷 醉锦城」→「第一卷 醉锦城」），
    保持标题序数与 sort_order 一致，消除大纲校验的橙色警告。
    标题无「第X卷」前缀的不动。
    """
    ordered = sorted(
        volumes,
        key=lambda v: (int(v.get("sort_order") or 0), str(v.get("created_at") or "")),
    )
    for i, vol in enumerate(ordered, start=1):
        vol["sort_order"] = i
        vol["title"] = _sync_title_ordinal(vol.get("title") or "", i, kind="卷")


def _sync_title_ordinal(title: str, ordinal: int, *, kind: str) -> str:
    """把标题里的「第X{kind}」序数词替换为 `ordinal`，其余部分原样保留。

    - 标题无「第X{kind}」前缀 → 原样返回（不擅自加序数）；
    - 原序数为阿拉伯数字 → 仍用阿拉伯数字；
    - 原序数为中文数字 → 仍用中文数字（1-99 覆盖）。
    """
    import re

    cn_digits = "零一二三四五六七八九十百千万两"
    pattern = rf"^第\s*([0-9{cn_digits}]+)\s*{kind}"
    m = re.match(pattern, title.strip())
    if not m:
        return title
    token = m.group(1)
    use_cn = not token.isdigit()
    if use_cn:
        new_num = _to_cn_ordinal(ordinal)
    else:
        new_num = str(ordinal)
    suffix = title[m.end():]
    return f"第{new_num}{kind}{suffix}"


#: 1-99 中文数字表（覆盖小说卷/章常见范围）
_CN_ORDINALS = [
    "", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "二十一", "二十二", "二十三", "二十四", "二十五", "二十六", "二十七", "二十八", "二十九", "三十",
    "三十一", "三十二", "三十三", "三十四", "三十五", "三十六", "三十七", "三十八", "三十九", "四十",
    "四十一", "四十二", "四十三", "四十四", "四十五", "四十六", "四十七", "四十八", "四十九", "五十",
    "五十一", "五十二", "五十三", "五十四", "五十五", "五十六", "五十七", "五十八", "五十九", "六十",
    "六十一", "六十二", "六十三", "六十四", "六十五", "六十六", "六十七", "六十八", "六十九", "七十",
    "七十一", "七十二", "七十三", "七十四", "七十五", "七十六", "七十七", "七十八", "七十九", "八十",
    "八十一", "八十二", "八十三", "八十四", "八十五", "八十六", "八十七", "八十八", "八十九", "九十",
    "九十一", "九十二", "九十三", "九十四", "九十五", "九十六", "九十七", "九十八", "九十九", "一百",
]


def _to_cn_ordinal(n: int) -> str:
    """整数 → 中文序数词（1-100 查表，超出回落阿拉伯数字）。"""
    if 1 <= n <= len(_CN_ORDINALS) - 1:
        return _CN_ORDINALS[n]
    return str(n)


def _locate_volume(vol_id: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """由 vol_id 反查 project_id 与卷记录。

    卷 ID 全局唯一（uuid 前 8 位），扫描各工作区的 volumes.yaml 定位。
    """

    for ws in workspaces_root().iterdir() if workspaces_root().exists() else []:
        if not ws.is_dir():
            continue
        vfile = ws / "volumes.yaml"
        if not vfile.exists():
            continue
        volumes = files.read_volumes(ws.name)
        vol = files.find_volume(volumes, vol_id)
        if vol is not None:
            return ws.name, volumes, vol
    raise NotFoundError(f"卷不存在：{vol_id}")


def locate_volume(vol_id: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """公开入口：由 vol_id 反查 `(project_id, volumes, volume)`。"""
    return _locate_volume(vol_id)


def locate_chapter(chapter_id: str) -> tuple[str, str, dict[str, Any], str]:
    """公开入口：由 chapter_id 反查 `(project_id, vol_id, meta, content)`。"""
    return _locate_chapter(chapter_id)


# ---------------------------------------------------------------------------
# 章
# ---------------------------------------------------------------------------


async def list_chapters(session: AsyncSession, project_id: str, vol_id: str) -> list[dict[str, Any]]:
    """章列表：以 volumes.yaml 所属卷目录下的文件为准，按 `sort_order` 只读排序。"""
    result: list[dict[str, Any]] = []
    for path in files.list_chapter_files(project_id, vol_id):
        chapter_id = path.stem[1:]  # 去掉前缀 'c'
        try:
            meta = files.read_chapter_meta(project_id, vol_id, chapter_id)
        except Exception as exc:  # pragma: no cover - 坏文件跳过并告警
            logger.warning("chapter_meta_failed", path=str(path), error=str(exc))
            continue
        result.append(_chapter_dto(meta, chapter_id, vol_id))
    result.sort(key=lambda c: (c["sort_order"], c["title"] or ""))
    return result


async def create_chapter(
    session: AsyncSession,
    vol_id: str,
    *,
    title: str,
    content: str = "",
    ordinal: int | None = None,
) -> dict[str, Any]:
    """新建章：**显式命名**。

    - 不传 `ordinal` → 追加到卷末尾（`sort_order = max+1`，兼容旧行为）；
    - 传 `ordinal`→ 插入到该位置，其后各章顺延 +1。
    """
    project_id, _volumes, vol = _locate_volume(vol_id)
    clean = validate_chapter_title(title)

    existing = await list_chapters(session, project_id, vol_id)
    position = _validate_ordinal(ordinal, len(existing), kind="章")

    taken = {c["id"] for c in existing}
    chapter_id = short_id(taken)

    if position is not None:
        for ch in existing:
            if ch["sort_order"] >= position:
                _shift_chapter_meta(project_id, vol_id, str(ch["id"]), ch["sort_order"] + 1)
        sort_order = position
    else:
        sort_order = max((c["sort_order"] for c in existing), default=0) + 1

    now = utcnow()
    meta = {
        "title": clean,
        "sort_order": sort_order,
        "word_count": files.count_words(content),
        "status": "draft",
        "version": 1,
        "updated_at": now,
        "created_at": now,
        "volume_id": vol_id,
    }
    files.write_chapter(project_id, vol_id, chapter_id, meta, content)

    # 插入（ordinal）会顺延其后各章 → 重新连续编号并同步标题/大纲序数，避免序数告警
    if position is not None:
        _renumber_chapters(
            project_id, vol_id, await list_chapters(session, project_id, vol_id)
        )
        meta = files.read_chapter_meta(project_id, vol_id, chapter_id)

    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "chapter.create",
        entity_type="chapter",
        entity_id=f"{project_id}:{chapter_id}",
        detail={
            "title": meta.get("title"),
            "volume_id": vol_id,
            "sort_order": sort_order,
            "inserted_at": position,
        },
        commit=True,
    )
    logger.info(
        "chapter_created", project_id=project_id, chapter_id=chapter_id, title=meta.get("title")
    )
    return _chapter_dto(meta, chapter_id, vol_id)


def _shift_chapter_meta(project_id: str, vol_id: str, chapter_id: str, new_sort: int) -> None:
    """只改某章 frontmatter 的 `sort_order`（保留正文，不递增 version、不写快照）。"""
    meta, content = files.read_chapter(project_id, vol_id, chapter_id)
    meta["sort_order"] = new_sort
    files.write_chapter(project_id, vol_id, chapter_id, meta, content)


def _sync_chapter_title_ordinal(project_id: str, vol_id: str, chapter_id: str, ordinal: int) -> None:
    """把章节标题里的「第X章」序数词替换为 `ordinal`，其余部分原样保留。

    - 标题无「第X章」前缀 → 不动；
    - 原序数为阿拉伯数字 → 仍用阿拉伯数字；
    - 原序数为中文数字 → 仍用中文数字（1-99 覆盖）。
    只改 frontmatter 的 `title`，不动正文、不递增 version、不写快照。
    """
    meta, content = files.read_chapter(project_id, vol_id, chapter_id)
    old_title = meta.get("title") or ""
    new_title = _sync_title_ordinal(old_title, ordinal, kind="章")
    if new_title != old_title:
        meta["title"] = new_title
        files.write_chapter(project_id, vol_id, chapter_id, meta, content)
        logger.info(
            "chapter_title_ordinal_synced",
            project_id=project_id,
            chapter_id=chapter_id,
            old_title=old_title,
            new_title=new_title,
        )


def _outline_path_of(project_id: str) -> Path | None:
    """大纲文件绝对路径（按 novel.yaml.outline_file 寻址）；不存在返回 None。"""
    from app.services.story import outline_relpath_of

    rel = outline_relpath_of(project_id)
    guard = WorkspaceGuard(project_id)
    if not guard.exists(rel):
        return None
    return guard.resolve(rel)


def _sync_outline_volume_ordinals(project_id: str, volumes: list[dict[str, Any]]) -> None:
    """同步大纲文件里卷节标题（H1）的序数词，与 volumes.yaml 的 sort_order 保持一致。

    只改序数词，不动卷节下的其它内容；任何异常只记日志，不阻断主流程。
    """
    try:
        outline_path = _outline_path_of(project_id)
        if outline_path is None:
            return
        content = outline_path.read_text(encoding="utf-8")

        ordinal_map = {
            normalize_title(str(v.get("title") or "")): int(v.get("sort_order") or 0)
            for v in volumes
            if str(v.get("title") or "")
        }

        lines = content.split("\n")
        changed = False
        for idx, line in enumerate(lines):
            if not re.match(r"^#\s+", line):
                continue
            title = line.lstrip("#").strip()
            normalized = normalize_title(title)
            if normalized not in ordinal_map:
                continue
            new_title = _sync_title_ordinal(title, ordinal_map[normalized], kind="卷")
            if new_title != title:
                lines[idx] = f"# {new_title}"
                changed = True

        if changed:
            outline_path.write_text("\n".join(lines), encoding="utf-8")
            logger.info("outline_volume_ordinals_synced", project_id=project_id)
    except Exception as exc:  # pragma: no cover - 大纲同步失败不阻断卷章操作
        logger.warning("outline_volume_ordinals_sync_failed", project_id=project_id, error=str(exc))


def _sync_outline_chapter_ordinals(
    project_id: str, vol_id: str, ordered_chapters: list[dict[str, Any]]
) -> None:
    """同步大纲文件里对应卷的章小节标题（H2）序数词，与卷内实际顺序保持一致。

    只改序数词，不动简介 / 衔接要点等内容；任何异常只记日志，不阻断主流程。
    """
    try:
        outline_path = _outline_path_of(project_id)
        if outline_path is None:
            return
        content = outline_path.read_text(encoding="utf-8")

        vol_meta = next(
            (v for v in files.read_volumes(project_id) if str(v.get("id")) == vol_id), None
        )
        vol_title = str((vol_meta or {}).get("title") or "")
        if not vol_title:
            return

        ordinal_map: dict[str, int] = {}
        for i, ch in enumerate(ordered_chapters, start=1):
            ch_title = str(ch.get("title") or "")
            if ch_title:
                ordinal_map[normalize_title(ch_title)] = i

        lines = content.split("\n")
        in_target_volume = False
        changed = False
        for idx, line in enumerate(lines):
            if re.match(r"^#\s+", line):
                in_target_volume = (
                    normalize_title(line.lstrip("#").strip()) == normalize_title(vol_title)
                )
                continue
            if in_target_volume and re.match(r"^##\s+", line):
                ch_title = line.lstrip("#").strip()
                normalized = normalize_title(ch_title)
                if normalized in ordinal_map:
                    new_title = _sync_title_ordinal(ch_title, ordinal_map[normalized], kind="章")
                    if new_title != ch_title:
                        lines[idx] = f"## {new_title}"
                        changed = True

        if changed:
            outline_path.write_text("\n".join(lines), encoding="utf-8")
            logger.info(
                "outline_chapter_ordinals_synced",
                project_id=project_id,
                volume_id=vol_id,
            )
    except Exception as exc:  # pragma: no cover - 大纲同步失败不阻断卷章操作
        logger.warning("outline_chapter_ordinals_sync_failed", project_id=project_id, error=str(exc))



def _renumber_chapters(project_id: str, vol_id: str, keep: list[dict[str, Any]]) -> None:
    """按当前 `sort_order` 次序把卷内章重新连续编号为 1..N。

    同时同步章节标题里的序数词（如「第五章 青衣谪凡」→「第二章 青衣谪凡」），
    保持标题序数与 sort_order 一致，消除大纲校验的橙色警告。
    标题无「第X章」前缀的不动。
    """
    ordered = sorted(keep, key=lambda c: (int(c.get("sort_order") or 0), str(c.get("id"))))
    for i, ch in enumerate(ordered, start=1):
        old_sort = int(ch.get("sort_order") or 0)
        if old_sort != i:
            _shift_chapter_meta(project_id, vol_id, str(ch["id"]), i)
        # 同步标题序数（无论 sort_order 是否变化，标题可能已过时）
        _sync_chapter_title_ordinal(project_id, vol_id, str(ch["id"]), i)
    # 同步大纲文件中的章节标题序数
    _sync_outline_chapter_ordinals(project_id, vol_id, ordered)


async def renumber_all(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """按当前结构把全书卷/章重新连续编号，并同步标题与大纲里的序数词。

    用于修复历史数据（如旧版本删章后只改了 `sort_order`、未同步标题序数）
    造成的「序数与 sort_order 不一致」告警；幂等，无差异时不做任何写盘。
    """
    await novels.get_novel(session, project_id)
    volumes = files.read_volumes(project_id)
    _renumber_volumes(volumes)
    files.write_volumes(project_id, volumes)
    _sync_outline_volume_ordinals(project_id, volumes)

    chapters_synced = 0
    for vol in volumes:
        vol_id = str(vol.get("id"))
        siblings = await list_chapters(session, project_id, vol_id)
        if not siblings:
            continue
        _renumber_chapters(project_id, vol_id, siblings)
        chapters_synced += len(siblings)

    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "volumes.renumber",
        entity_type="novel",
        entity_id=project_id,
        detail={"volumes": len(volumes), "chapters": chapters_synced},
        commit=True,
    )
    logger.info(
        "volumes_renumbered", project_id=project_id, volumes=len(volumes), chapters=chapters_synced
    )
    return {
        "novel_id": project_id,
        "volumes": len(volumes),
        "chapters": chapters_synced,
    }


def _locate_chapter(chapter_id: str) -> tuple[str, str, dict[str, Any], str]:
    """由 chapter_id 反查 `(project_id, vol_id, meta, content)`。"""

    root = workspaces_root()
    if not root.exists():
        raise NotFoundError(f"章节不存在：{chapter_id}")

    for ws in root.iterdir():
        if not ws.is_dir():
            continue
        chapters_root = ws / "chapters"
        if not chapters_root.exists():
            continue
        for vdir in chapters_root.iterdir():
            if not vdir.is_dir() or not vdir.name.startswith("v"):
                continue
            path = vdir / f"c{chapter_id}.md"
            if path.exists():
                vol_id = vdir.name[1:]
                meta, content = files.read_chapter(ws.name, vol_id, chapter_id)
                return ws.name, vol_id, meta, content
    raise NotFoundError(f"章节不存在：{chapter_id}")


async def update_chapter_meta(
    session: AsyncSession,
    chapter_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
    volume_title: str | None = None,
) -> dict[str, Any]:
    """改章名 / 状态（draft|finalized）。**不改 ID、文件名、sort_order**。

    定稿（status → finalized）会触发一次修订快照（手册「五」触发时机）。

    重命名弹窗同时含「章名 + 卷名」（卷名默认填当前章所属卷名，默认不修改）；
    `volume_title` 有变更时**重命名该章所属的卷**（章仍留在原卷）。
    """
    project_id, vol_id, meta, content = _locate_chapter(chapter_id)
    changes: dict[str, Any] = {}

    if title is not None:
        clean = validate_chapter_title(title)
        if clean != meta.get("title"):
            changes["title"] = {"from": meta.get("title"), "to": clean}
            meta["title"] = clean

    if volume_title is not None:
        _project_id, volumes, vol = _locate_volume(vol_id)
        clean_vol = validate_volume_title(volume_title)
        if clean_vol != (vol.get("title") or ""):
            changes["volume_title"] = {"from": vol.get("title"), "to": clean_vol}
            vol["title"] = clean_vol
            vol["updated_at"] = utcnow()
            files.write_volumes(project_id, volumes)

    chapter_changed = "title" in changes

    finalize = False
    if status is not None:
        if status not in ("draft", "finalized"):
            from app.core.errors import BadRequestError

            raise BadRequestError(f"非法章节状态：{status}（只允许 draft|finalized）")
        if status != meta.get("status"):
            changes["status"] = {"from": meta.get("status"), "to": status}
            finalize = status == "finalized" and meta.get("status") != "finalized"
            meta["status"] = status
            chapter_changed = True

    if not changes:
        return _chapter_dto(meta, chapter_id, vol_id)

    if finalize:
        await _snapshot(session, project_id, chapter_id, meta, content, source="manual")

    if chapter_changed:
        meta["updated_at"] = utcnow()
        files.write_chapter(project_id, vol_id, chapter_id, meta, content)

    await audit.record(
        session,
        "chapter.update",
        entity_type="chapter",
        entity_id=f"{project_id}:{chapter_id}",
        detail=changes,
        commit=True,
    )
    return _chapter_dto(meta, chapter_id, vol_id)


async def reposition_chapter(session: AsyncSession, chapter_id: str, *, position: int) -> dict[str, Any]:
    """章节重定位：把本章插入到所属卷的 `position`（1-based）位置。

    - 其后各章顺延 +1；随后卷内重新连续编号（1..N）并同步标题/大纲序数词；
    - 不跨卷移动（换卷仍需新建章复制正文再删旧章）；
    - 位置越界（<1 或 >N）→ 400。
    """
    project_id, vol_id, meta, _content = _locate_chapter(chapter_id)
    siblings = await list_chapters(session, project_id, vol_id)
    count = len(siblings)
    if position < 1 or position > count:
        raise BadRequestError(f"位置超出范围（当前卷共 {count} 章，允许 1~{count}）")

    ordered = sorted(siblings, key=lambda c: (c["sort_order"], c["title"] or ""))
    others = [c for c in ordered if str(c["id"]) != chapter_id]
    new_order = others[: position - 1] + [next(c for c in ordered if str(c["id"]) == chapter_id)] + others[position - 1 :]

    for i, ch in enumerate(new_order, start=1):
        if int(ch.get("sort_order") or 0) != i:
            _shift_chapter_meta(project_id, vol_id, str(ch["id"]), i)

    _renumber_chapters(project_id, vol_id, await list_chapters(session, project_id, vol_id))
    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "chapter.move",
        entity_type="chapter",
        entity_id=f"{project_id}:{chapter_id}",
        detail={"title": meta.get("title"), "volume_id": vol_id, "position": position},
        commit=True,
    )
    logger.info(
        "chapter_moved", project_id=project_id, chapter_id=chapter_id, position=position
    )
    moved = next(c for c in await list_chapters(session, project_id, vol_id) if str(c["id"]) == chapter_id)
    return moved


async def delete_chapter(session: AsyncSession, chapter_id: str) -> dict[str, Any]:
    """删除章文件。不提供跨卷移动——换卷需新建同名章复制正文再删旧章。
    
    - 删除后卷内其余章重新连续编号（1..N）；
    - **某一卷下所有章节都删除后，该卷一并删除**（卷层同样重新连续编号）。

    删除的章节先进回收站（可回收 / 彻底删除）。
    """
    project_id, vol_id, meta, _content = _locate_chapter(chapter_id)
    guard = WorkspaceGuard(project_id)

    # 归档到回收站（带书名 / 卷名快照）
    volumes_meta = files.read_volumes(project_id)
    vol_meta = files.find_volume(volumes_meta, vol_id) or {}
    recycle.archive_chapter(
        project_id,
        vol_id=vol_id,
        volume_title=str(vol_meta.get("title") or ""),
        volume_sort_order=int(vol_meta.get("sort_order") or 0),
        chapter_id=chapter_id,
        meta=meta,
        novel_title=await recycle.get_novel_title(session, project_id),
    )

    rel = f"chapters/v{vol_id}/c{chapter_id}.md"
    guard.delete(rel)

    # 该章的修订快照一并清理（硬删除，仅项目有软删除）
    rows = (
        await session.execute(
            select(ChapterRevision).where(ChapterRevision.chapter_id == chapter_id)
        )
    ).scalars().all()
    for r in rows:
        await session.delete(r)

    volume_deleted = False
    siblings = await list_chapters(session, project_id, vol_id)
    if not siblings:
        # 卷下章节删空 → 卷也不存在
        await delete_volume(session, vol_id)
        volume_deleted = True
    else:
        _renumber_chapters(project_id, vol_id, siblings)

    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "chapter.delete",
        entity_type="chapter",
        entity_id=f"{project_id}:{chapter_id}",
        detail={
            "title": meta.get("title"),
            "volume_id": vol_id,
            "volume_deleted": volume_deleted,
        },
        commit=True,
    )
    return {
        "id": chapter_id,
        "deleted": True,
        "volume_id": vol_id,
        "volume_deleted": volume_deleted,
    }


# ---------------------------------------------------------------------------
# 正文读写 + 乐观锁
# ---------------------------------------------------------------------------


async def read_content(session: AsyncSession, chapter_id: str) -> dict[str, Any]:
    """读正文。返回内容 + `version`（前端保存时须回传为 `base_version`）。"""
    project_id, vol_id, meta, content = _locate_chapter(chapter_id)
    return {
        "id": chapter_id,
        "volume_id": vol_id,
        "project_id": project_id,
        "title": meta.get("title"),
        "content": content,
        "word_count": meta_int(meta, "word_count", 0),
        "status": meta.get("status") or "draft",
        "version": meta_int(meta, "version", 1),
        "updated_at": meta.get("updated_at"),
        "last_writer": meta.get("last_writer") or "manual",
        "path": str(chapter_file(project_id, vol_id, chapter_id)),
    }


async def write_content(
    session: AsyncSession,
    chapter_id: str,
    *,
    content: str,
    base_version: int,
    source: str = "manual",
    is_autosave: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    """写正文（乐观锁）。

    - `base_version` 与文件 frontmatter `version` 不一致 → 409 / `40901`，回传最新内容；
    - 成功则 `version += 1`；**手动保存 / Agent 写入 / 定稿**先快照旧内容，自动保存不快照；
    - `source`: manual | agent。
    """
    project_id, vol_id, meta, old_content = _locate_chapter(chapter_id)
    current_version = meta_int(meta, "version", 1)

    if base_version != current_version:
        raise VersionConflictError(
            "版本冲突：AI 或其它端已修改本章，请选择合并或覆盖",
            data={
                "id": chapter_id,
                "content": old_content,
                "version": current_version,
                "updated_at": meta.get("updated_at"),
                "last_writer": meta.get("last_writer") or "manual",
                "base_version": base_version,
            },
        )

    # 快照旧内容（自动保存不快照，防版本爆炸）
    if not is_autosave:
        await _snapshot(session, project_id, chapter_id, meta, old_content, source=source, run_id=run_id)

    new_version = current_version + 1
    meta.update(
        {
            "version": new_version,
            "word_count": files.count_words(content),
            "updated_at": utcnow(),
            "last_writer": source,
        }
    )
    files.write_chapter(project_id, vol_id, chapter_id, meta, content)

    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "chapter.content.write",
        entity_type="chapter",
        entity_id=f"{project_id}:{chapter_id}",
        detail={
            "version": new_version,
            "source": source,
            "autosave": is_autosave,
            "word_count": meta["word_count"],
            "run_id": run_id,
        },
        commit=True,
    )

    # 定稿入口移除后，章节摘要（AI 前情提要的记忆来源）改由**手动保存**触发
    # （自动保存不触发，防频繁调 LLM）；后台执行，失败只记日志不阻断保存。
    if not is_autosave:
        asyncio.get_running_loop().create_task(_summarize_after_save(project_id, vol_id, chapter_id))

    return {
        "id": chapter_id,
        "volume_id": vol_id,
        "version": new_version,
        "word_count": meta["word_count"],
        "updated_at": meta["updated_at"],
        "last_writer": source,
        "snapshotted": not is_autosave,
    }


# ---------------------------------------------------------------------------
# 修订快照
# ---------------------------------------------------------------------------


async def _summarize_after_save(project_id: str, volume_id: str, chapter_id: str) -> None:
    """手动保存后后台刷新章节摘要（供 AI 前情提要使用）。

    定稿入口已移除；摘要不再依赖 `status=finalized`。force=True 保证改稿后摘要同步更新。
    无可用模型 / 正文过短时 summarize 内部自行跳过。
    """
    try:
        from app.agent.loop.knowledge.summarize import summarize_chapter
        from app.db.session import session_scope

        async with session_scope() as db:
            await summarize_chapter(db, project_id, volume_id, chapter_id, force=True)
    except Exception as exc:  # noqa: BLE001 - 摘要失败不影响保存结果
        logger.warning("summarize_after_save_failed", chapter_id=chapter_id, error=str(exc))


async def _snapshot(
    session: AsyncSession,
    project_id: str,
    chapter_id: str,
    meta: dict[str, Any],
    content: str,
    *,
    source: str,
    run_id: str | None = None,
) -> ChapterRevision:
    """把当前内容快照进 `chapter_revisions`，并按保留策略清理最旧的非定稿快照。"""

    rev = ChapterRevision(
        id=new_id(),
        novel_id=project_id,
        chapter_id=chapter_id,
        version=meta_int(meta, "version", 1),
        content_md=content,
        word_count=files.count_words(content),
        source=source,
        run_id=run_id,
        created_at=utcnow(),
    )
    session.add(rev)
    await session.flush()
    await _prune_revisions(session, chapter_id)
    return rev


async def _prune_revisions(session: AsyncSession, chapter_id: str) -> None:
    """超过保留上限时清理**最旧的非定稿**快照（定稿快照永不清理）。"""
    keep = max(1, get_settings().chapter_revision_keep)
    rows = (
        await session.execute(
            select(ChapterRevision)
            .where(ChapterRevision.chapter_id == chapter_id)
            .order_by(ChapterRevision.created_at.desc())
        )
    ).scalars().all()
    if len(rows) <= keep:
        return
    # 保留最新 keep 条；其余中，非定稿（source != finalized 标记）者删除
    for rev in rows[keep:]:
        if (rev.source or "") == "finalized":
            continue
        await session.delete(rev)


async def list_revisions(session: AsyncSession, chapter_id: str) -> list[dict[str, Any]]:
    """修订快照列表（不含正文全文，减小载荷；正文由 restore / detail 取）。"""
    project_id, vol_id, _meta, _content = _locate_chapter(chapter_id)
    rows = (
        await session.execute(
            select(ChapterRevision)
            .where(ChapterRevision.chapter_id == chapter_id)
            .order_by(ChapterRevision.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "chapter_id": r.chapter_id,
            "project_id": project_id,
            "volume_id": vol_id,
            "version": r.version,
            "word_count": r.word_count,
            "source": r.source,
            "run_id": r.run_id,
            "created_at": r.created_at,
            "preview": (r.content_md or "")[:120],
        }
        for r in rows
    ]


async def restore_revision(
    session: AsyncSession, chapter_id: str, revision_id: str
) -> dict[str, Any]:
    """回滚到指定快照：先把当前内容快照一份，再用快照内容覆盖，`version` 继续自增。"""
    project_id, vol_id, meta, current = _locate_chapter(chapter_id)

    rev = await session.get(ChapterRevision, revision_id)
    if rev is None or rev.chapter_id != chapter_id:
        raise NotFoundError(f"修订快照不存在：{revision_id}")

    await _snapshot(session, project_id, chapter_id, meta, current, source="manual")

    new_version = meta_int(meta, "version", 1) + 1
    meta.update(
        {
            "version": new_version,
            "word_count": files.count_words(rev.content_md),
            "updated_at": utcnow(),
            "last_writer": "manual",
        }
    )
    files.write_chapter(project_id, vol_id, chapter_id, meta, rev.content_md)

    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "chapter.revision.restore",
        entity_type="chapter",
        entity_id=f"{project_id}:{chapter_id}",
        detail={"revision_id": revision_id, "from_version": rev.version, "to_version": new_version},
        commit=True,
    )
    return {
        "id": chapter_id,
        "volume_id": vol_id,
        "version": new_version,
        "content": rev.content_md,
        "word_count": meta["word_count"],
        "restored_from": revision_id,
    }


# ---------------------------------------------------------------------------
# 大纲 → 顺序派生（M4 接线；M1 保持顺序为创建先后）
# ---------------------------------------------------------------------------


def assert_no_manual_reorder() -> None:
    """设计守卫：本模块**不提供**任何拖拽重排 / 跨卷移动能力。

    位置编辑的入口只有两个：「插入」（`create_volume` / `create_chapter` 的
    `ordinal` 参数，插入后其后各项自动顺延）与「章节重定位」
    （`reposition_chapter`：卷内移动到指定位置后重新连续编号）；
    仍无 reorder / 跨卷 move 接口。
    该函数供测试断言「无 reorder/move 入口」时调用，也作为文档化契约。
    """
    forbidden = {"reorder_volumes", "reorder_chapters", "move_chapter", "move_volume"}
    leaked = forbidden & set(globals())
    if leaked:  # pragma: no cover - 契约破坏时立即失败
        raise AssertionError(f"违反铁律 10：出现了手工重排函数 {leaked}")

