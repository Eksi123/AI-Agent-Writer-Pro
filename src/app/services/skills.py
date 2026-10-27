# -*- coding: utf-8 -*-
"""
模块: app.services.skills
文件名: skills.py
作者: xionggaoqing
时间: 2026-10-27
说明: 写作技能配置服务（手册「二、(3) Skills」）。

    用户自建技能存 `skill_configs`，并**物化**到 `data/skills/{name}/SKILL.md`
    （经 `/skills/user/` 路由供 Agent 只读加载）；内置技能随包只读，不入库不改写。

    frontmatter 约束（deepagents 解析规则，实测）：
    - `name` **必须等于父目录名**、小写字母数字连字符、≤64 字符；
    - `description` ≤1024 字符；
    - 可选 `allowed-tools`、`metadata.include_tools`（空格分隔工具名）；
    - SKILL.md 上限 10MB。
"""

from __future__ import annotations

import json
import re
import shutil
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.logging import get_logger
from app.core.paths import BUILTIN_SKILLS_DIR, skills_root
from app.db.base import new_id, utcnow
from app.db.models import SkillConfig
from app.services import audit

logger = get_logger(__name__)

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_MAX_SKILL_BYTES = 10 * 1024 * 1024
_MAX_DESCRIPTION = 1024


def validate_skill_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise BadRequestError(
            f"技能名不合法：{name!r}（小写字母开头，仅小写字母/数字/连字符，≤64 字符；须与物化目录名一致）"
        )
    return name


def render_skill_md(name: str, description: str, body: str, *, allowed_tools: list[str] | None = None) -> str:
    """组装 SKILL.md（frontmatter + 正文）。★frontmatter `name` 必须等于目录名。"""
    description = (description or "").strip()
    if not description:
        raise BadRequestError("description 不能为空（Agent 据此决定何时加载技能）")
    if len(description) > _MAX_DESCRIPTION:
        raise BadRequestError(f"description 超过 {_MAX_DESCRIPTION} 字符上限")

    front: dict[str, Any] = {"name": name, "description": description}
    if allowed_tools:
        front["allowed-tools"] = list(allowed_tools)

    fm = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)
    body = (body or "").strip()
    content = f"---\n{fm.strip()}\n---\n\n# {name}\n\n{body}\n"
    if len(content.encode("utf-8")) > _MAX_SKILL_BYTES:
        raise BadRequestError("SKILL.md 超过 10MB 上限")
    return content


def materialize_skill(name: str, content_md: str) -> None:
    """把 SKILL.md 物化到 `data/skills/{name}/SKILL.md`（服务层写，Agent 只读）。"""
    from app.services.workspace import WorkspaceGuard

    target = WorkspaceGuard.write_shared(f"skills/{name}/SKILL.md", content_md)
    logger.info("skill_materialized", name=name, path=str(target))


def unmaterialize_skill(name: str) -> None:
    """删除物化目录（停用/删除技能时）。"""
    path = skills_root() / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
        logger.info("skill_unmaterialized", name=name)


def _dto(row: SkillConfig, *, materialized: bool | None = None) -> dict[str, Any]:
    assets: Any = []
    if row.assets_json:
        try:
            assets = json.loads(row.assets_json)
        except ValueError:
            assets = []
    if materialized is None:
        materialized = (skills_root() / row.name / "SKILL.md").exists()
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "content_md": row.content_md,
        "allowed_tools": _extract_allowed_tools(row.content_md),
        "assets": assets,
        "enabled": row.enabled,
        "is_builtin": row.is_builtin,
        "materialized": materialized,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _extract_allowed_tools(content_md: str) -> list[str]:
    """从 SKILL.md frontmatter 读 allowed-tools（供 UI 回显）。"""
    if not content_md:
        return []
    m = re.match(r"^---\n(.*?)\n---", content_md, re.S)
    if not m:
        return []
    try:
        front = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return []
    value = front.get("allowed-tools")
    return [str(x) for x in value] if isinstance(value, list) else []


async def list_skills(session: AsyncSession, *, include_builtin: bool = True) -> list[dict[str, Any]]:
    """用户自建技能（DB）+ 内置技能（包内目录，只读呈现）。"""
    result: list[dict[str, Any]] = []

    rows = (
        (await session.execute(select(SkillConfig).order_by(SkillConfig.created_at.asc()))).scalars().all()
    )
    result.extend(_dto(r) for r in rows)

    if include_builtin:
        result.extend(_builtin_skill_dtos())
    return result


def _builtin_skill_dtos() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not BUILTIN_SKILLS_DIR.is_dir():
        return out
    for d in sorted(BUILTIN_SKILLS_DIR.iterdir()):
        skill_md = d / "SKILL.md"
        if not d.is_dir() or not skill_md.is_file():
            continue
        content = skill_md.read_text(encoding="utf-8")
        description = _extract_description(content)
        out.append(
            {
                "id": f"builtin:{d.name}",
                "name": d.name,
                "description": description,
                "content_md": content,
                "allowed_tools": _extract_allowed_tools(content),
                "assets": [],
                "enabled": True,
                "is_builtin": True,
                "materialized": True,
                "created_at": None,
                "updated_at": None,
            }
        )
    return out


def _extract_description(content_md: str) -> str:
    m = re.match(r"^---\n(.*?)\n---", content_md, re.S)
    if not m:
        return ""
    try:
        front = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return ""
    return str(front.get("description") or "")


async def create_skill(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    body: str,
    allowed_tools: list[str] | None = None,
    assets: list[dict[str, Any]] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    name = validate_skill_name(name)

    dup = (await session.execute(select(SkillConfig).where(SkillConfig.name == name))).scalar_one_or_none()
    if dup is not None:
        raise BadRequestError(f"技能名已存在：{name}")
    # 与内置技能同名会在 /skills/user/ 覆盖 /skills/builtin/（deepagents 行为），禁止以免混淆
    builtin_names = {d.name for d in _builtin_dirs()}
    if name in builtin_names:
        raise BadRequestError(f"技能名与内置技能冲突：{name}（用户技能会覆盖同名内置技能）")

    content_md = render_skill_md(name, description, body, allowed_tools=allowed_tools)

    row = SkillConfig(
        id=new_id(),
        name=name,
        description=description.strip(),
        content_md=content_md,
        assets_json=json.dumps(assets, ensure_ascii=False) if assets else None,
        enabled=enabled,
        is_builtin=False,
    )
    session.add(row)
    await session.flush()

    if enabled:
        materialize_skill(name, content_md)

    await audit.record(
        session,
        "skill.create",
        entity_type="skill",
        entity_id=row.id,
        detail={"name": name, "enabled": enabled, "allowed_tools": allowed_tools or []},
    )
    logger.info("skill_created", name=name, enabled=enabled)
    return _dto(row, materialized=enabled)


def _builtin_dirs() -> list[Any]:
    if not BUILTIN_SKILLS_DIR.is_dir():
        return []
    return [d for d in BUILTIN_SKILLS_DIR.iterdir() if d.is_dir()]


async def update_skill(session: AsyncSession, pk: str, **fields: Any) -> dict[str, Any]:
    row = await session.get(SkillConfig, pk)
    if row is None:
        raise NotFoundError(f"技能不存在：{pk}")
    if row.is_builtin:
        raise BadRequestError("内置技能只读，不可修改（可复制为用户技能后编辑）")

    changes: dict[str, Any] = {}

    name = fields.get("name")
    renamed = False
    if name is not None and name != row.name:
        name = validate_skill_name(name)
        dup = (
            await session.execute(select(SkillConfig).where(SkillConfig.name == name, SkillConfig.id != pk))
        ).scalar_one_or_none()
        if dup is not None:
            raise BadRequestError(f"技能名已存在：{name}")
        if name in {d.name for d in _builtin_dirs()}:
            raise BadRequestError(f"技能名与内置技能冲突：{name}")
        old_name = row.name
        changes["name"] = {"from": old_name, "to": name}
        unmaterialize_skill(old_name)  # 目录名即技能名，改名要迁移物化目录
        row.name = name
        renamed = True

    description = fields.get("description")
    body = fields.get("body")
    allowed_tools = fields.get("allowed_tools")
    # ★改名后 frontmatter `name` 必须同步（deepagents 要求 name == 父目录名）
    if description is not None or body is not None or allowed_tools is not None or renamed:
        new_description = (description if description is not None else row.description) or ""
        new_body = body if body is not None else _extract_body(row.content_md)
        new_tools = allowed_tools if allowed_tools is not None else _extract_allowed_tools(row.content_md)
        content_md = render_skill_md(row.name, new_description, new_body, allowed_tools=new_tools)
        if content_md != row.content_md:
            changes["content_md"] = {"size": len(content_md)}
            row.content_md = content_md
            row.description = new_description.strip()

    assets = fields.get("assets")
    if assets is not None:
        new_json = json.dumps(assets, ensure_ascii=False) if assets else None
        if new_json != row.assets_json:
            changes["assets"] = assets
            row.assets_json = new_json

    enabled = fields.get("enabled")
    if enabled is not None and bool(enabled) != row.enabled:
        changes["enabled"] = {"from": row.enabled, "to": bool(enabled)}
        row.enabled = bool(enabled)

    if changes:
        row.updated_at = utcnow()
        await session.flush()
        # 物化状态与 enabled 保持一致
        if row.enabled:
            materialize_skill(row.name, row.content_md)
        else:
            unmaterialize_skill(row.name)
        await audit.record(session, "skill.update", entity_type="skill", entity_id=pk, detail=changes)

    return _dto(row)


def _extract_body(content_md: str) -> str:
    """取 SKILL.md 去掉 frontmatter 与首行标题后的正文（供 UI 编辑回填）。"""
    text = re.sub(r"^---\n.*?\n---\n", "", content_md, count=1, flags=re.S)
    text = re.sub(r"^#\s+.*\n+", "", text, count=1)
    return text.strip()


async def delete_skill(session: AsyncSession, pk: str) -> dict[str, Any]:
    row = await session.get(SkillConfig, pk)
    if row is None:
        raise NotFoundError(f"技能不存在：{pk}")
    if row.is_builtin:
        raise BadRequestError("内置技能不可删除")
    name = row.name
    await session.delete(row)
    await session.flush()
    unmaterialize_skill(name)
    await audit.record(session, "skill.delete", entity_type="skill", entity_id=pk, detail={"name": name})
    return {"id": pk, "deleted": True}


async def resync_materialization(session: AsyncSession) -> dict[str, Any]:
    """按 DB 状态重新物化全部启用的用户技能（修复目录缺失/漂移）。"""
    rows = (await session.execute(select(SkillConfig))).scalars().all()
    written, removed = 0, 0
    for row in rows:
        if row.enabled:
            materialize_skill(row.name, row.content_md)
            written += 1
        else:
            unmaterialize_skill(row.name)
            removed += 1
    return {"materialized": written, "removed": removed, "total": len(rows)}


async def materialize_session_skills(
    session: AsyncSession, session_id: str, skill_names: set[str]
) -> int:
    """把会话选中的技能物化到 `data/session-skills/{session_id}/{name}/SKILL.md`。

    手册「三、(3) 输入窗口」：用户可主动添加写作 Skill 作为会话上下文；
    deepagents `skills=` 只接受目录级虚拟路径，故按会话生成精选目录再挂载。
    内置技能从包内复制，用户技能从 `data/skills/`（或 DB content_md）复制。
    返回实际物化的技能数。
    """
    from app.core.paths import session_skills_dir

    target_root = session_skills_dir(session_id)
    # 清空重建（选择可能已变化）
    if target_root.exists():
        shutil.rmtree(target_root, ignore_errors=True)
    target_root.mkdir(parents=True, exist_ok=True)

    count = 0
    rows = (await session.execute(select(SkillConfig))).scalars().all()
    user_by_name = {r.name: r for r in rows}

    for name in sorted(skill_names):
        src = BUILTIN_SKILLS_DIR / name / "SKILL.md"
        content: str | None = None
        if src.is_file():
            content = src.read_text(encoding="utf-8")
        elif name in user_by_name:
            content = user_by_name[name].content_md
        if not content:
            logger.warning("session_skill_missing", session_id=session_id, name=name)
            continue
        dest = target_root / name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(content, encoding="utf-8")
        count += 1
    logger.info("session_skills_materialized", session_id=session_id, count=count)
    return count
