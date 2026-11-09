# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_agent_write_registration.py
说明: Agent 直写正文的登记链路 + 可恢复删除 + 原子写 + 结构工具。

    覆盖这批新增/改动的代码（此前正式套件无覆盖）：
      · `volumes.record_agent_write`：补快照、抬 version、重算 word_count、
        同一 run 内不重复快照；
      · 抬 version 后用户端陈旧 buffer 保存必须撞乐观锁（409 / 40901）——
        这是「AI 改过的章不再被用户静默覆盖」的关键行为；
      · `create_volume(chapters=[...])` 的原子性与失败整体回滚；
      · `rename_chapter` 只改名、不动 sort_order；
      · `SafeDeleteMiddleware` 的四条分支：章节→回收站、项目内其它→留底目录、
        项目外→透传、非 delete→透传；且命中时**绝不**调用框架的物理删除；
      · `atomic_write_text`：无 `.tmp` 残留，且行尾与被它替换的
        `Path.write_text` 逐字节一致（避免 deepagents `edit_file` 字面量失配）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.errors import ErrorCode, VersionConflictError
from app.db.models import ChapterRevision
from app.services import files as F
from app.services import novels as N
from app.services import recycle as R
from app.services import volumes as V
from app.services.workspace import atomic_write_text

pytestmark = pytest.mark.asyncio


async def _make_project(session, title: str = "登记链路测试") -> str:
    novel = await N.create_novel(session, title=title)
    await session.commit()
    return str(novel["id"])


async def _make_volume(session, pid: str, title: str, chapters: list[str]) -> tuple[str, list[str]]:
    vol = await V.create_volume(session, pid, title=title, chapters=chapters)
    await session.commit()
    rows = await V.list_chapters(session, pid, vol["id"])
    return vol["id"], [r["id"] for r in rows]


def _fake_request(tool_name: str, args: dict, tool_call_id: str = "tc1"):
    return SimpleNamespace(tool_call={"name": tool_name, "args": args, "id": tool_call_id})


class _Recorder:
    """记录框架 handler 是否被调用：SafeDelete 命中时**不应**调用它（那才是物理删除）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def handler(self, request):
        self.calls += 1
        return "FRAMEWORK_HANDLER_CALLED"


# ---------------------------------------------------------------------------
# record_agent_write：登记 + 乐观锁
# ---------------------------------------------------------------------------


async def test_record_agent_write_registers_version_snapshot_and_wordcount(session):
    pid = await _make_project(session)
    vid, ids = await _make_volume(session, pid, "第一卷", ["第一章 甲", "第二章 乙"])
    cid = ids[0]

    before = await V.read_content(session, cid)
    new_body = "AI 重写后的正文，字数应当被重新统计。" * 3

    # 模拟 Agent 的 write_file：FileChangeNotify 是在工具**写完之后**才调 record_agent_write
    meta_pre, _ = F.read_chapter(pid, vid, cid)
    F.write_chapter(pid, vid, cid, meta_pre, new_body)

    res = await V.record_agent_write(session, pid, vid, cid, run_id="run-A", pre_content=before["content"])
    await session.commit()

    assert res is not None
    assert res["version"] == before["version"] + 1
    assert res["snapshotted"] is True

    meta, content = F.read_chapter(pid, vid, cid)
    assert content.strip() == new_body.strip()
    assert meta["word_count"] == F.count_words(content)
    assert meta["last_writer"] == "agent"

    n_rev = (
        await session.execute(select(func.count()).select_from(ChapterRevision).where(ChapterRevision.chapter_id == cid))
    ).scalar()
    assert n_rev == 1


async def test_record_agent_write_does_not_duplicate_snapshot_within_run(session):
    """同一 run 内多次落盘只存一次快照（防快照爆炸），但 version 每次都抬。"""
    pid = await _make_project(session)
    vid, ids = await _make_volume(session, pid, "第一卷", ["第一章 甲"])
    cid = ids[0]

    dto = await V.read_content(session, cid)
    meta, _ = F.read_chapter(pid, vid, cid)
    F.write_chapter(pid, vid, cid, meta, "第一稿")
    first = await V.record_agent_write(session, pid, vid, cid, run_id="run-B", pre_content=dto["content"])

    # ★ 必须重读 meta：上一次登记已把抬升后的 version 写回 frontmatter。
    #   若沿用陈旧的 meta（version 仍是旧值），整文件重写会把 version 打回去，
    #   于是本次只从旧值 +1 → 两次写入得到同一个 version（见该文件末尾的说明）。
    meta2, _ = F.read_chapter(pid, vid, cid)
    F.write_chapter(pid, vid, cid, meta2, "第二稿润色")
    second = await V.record_agent_write(session, pid, vid, cid, run_id="run-B", pre_content="第一稿")
    await session.commit()

    assert second["snapshotted"] is False
    assert second["version"] == first["version"] + 1
    n_rev = (
        await session.execute(select(func.count()).select_from(ChapterRevision).where(ChapterRevision.chapter_id == cid))
    ).scalar()
    assert n_rev == 1


async def test_stale_user_save_conflicts_after_agent_write(session):
    """★ 关键行为：AI 直写抬 version 后，用户端陈旧 buffer 保存必须撞 409 / 40901。

    在此之前 AI 写文件不抬 version，用户「保留本地修改」再保存会**静默覆盖掉 AI 的产出**，
    既不报冲突也没有快照可回退。
    """
    pid = await _make_project(session)
    vid, ids = await _make_volume(session, pid, "第一卷", ["第一章 甲"])
    cid = ids[0]

    stale = await V.read_content(session, cid)
    meta, _ = F.read_chapter(pid, vid, cid)
    F.write_chapter(pid, vid, cid, meta, "AI 产出的新正文")
    await V.record_agent_write(session, pid, vid, cid, run_id="run-C", pre_content=stale["content"])
    await session.commit()

    with pytest.raises(VersionConflictError) as ei:
        await V.write_content(session, cid, content="用户本地稿", base_version=stale["version"], source="manual")
    assert int(ei.value.code) == ErrorCode.VERSION_CONFLICT
    # 冲突响应要带最新内容，前端 ConflictBanner 才能做差异对比
    assert (ei.value.data or {}).get("content")
    assert "version" in (ei.value.data or {})

    # 用最新版本保存则照常成功
    fresh = await V.read_content(session, cid)
    dto = await V.write_content(session, cid, content="用户最新稿", base_version=fresh["version"], source="manual")
    await session.commit()
    assert dto["version"] == fresh["version"] + 1


async def test_stale_frontmatter_cannot_reset_version(session):
    """★ 回归：整文件覆盖携带陈旧 frontmatter，不得把 version 打回去。

    Agent 的 `write_file` 是整文件覆盖，模型给的 frontmatter 常来自它早先读到的旧内容。
    若同一轮里已写过一次，覆盖会把磁盘上抬升过的 version **打回旧值**；此时若只从
    文件值 +1，两次写入会得到同一个 version → 乐观锁漏判，用户陈旧 buffer 保存时
    不报 409，静默覆盖 AI 的产出。故登记以 `pre_version` 为下界取 max。
    """
    pid = await _make_project(session)
    vid, ids = await _make_volume(session, pid, "第一卷", ["第一章 甲"])
    cid = ids[0]

    dto = await V.read_content(session, cid)
    stale_meta, _ = F.read_chapter(pid, vid, cid)  # 模型「记得」的那一版 frontmatter

    F.write_chapter(pid, vid, cid, stale_meta, "第一稿")
    first = await V.record_agent_write(
        session, pid, vid, cid, run_id="run-D", pre_content=dto["content"], pre_version=dto["version"]
    )
    await session.commit()
    assert first["version"] == dto["version"] + 1

    # 第二次仍用陈旧 frontmatter 整文件覆盖 → 磁盘 version 被打回旧值（前置条件）
    F.write_chapter(pid, vid, cid, stale_meta, "第二稿")
    assert F.read_chapter_meta(pid, vid, cid).get("version") == dto["version"]

    second = await V.record_agent_write(
        session, pid, vid, cid, run_id="run-D", pre_content="第一稿", pre_version=first["version"]
    )
    await session.commit()
    assert second["version"] == first["version"] + 1, "version 必须单调递增，不得与上一次相同"


# ---------------------------------------------------------------------------
# create_volume 原子性 / rename 不动顺序
# ---------------------------------------------------------------------------


async def test_create_volume_with_chapters_is_atomic_and_rolls_back(session, monkeypatch):
    pid = await _make_project(session)
    vid, ids = await _make_volume(session, pid, "第一卷", ["第一章 甲", "第二章 乙", "第三章 丙"])
    assert len(ids) == 3
    vols_before = len(F.read_volumes(pid))

    original = V.create_chapter
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("模拟第 3 章创建失败")
        return await original(*args, **kwargs)

    monkeypatch.setattr(V, "create_chapter", flaky)
    with pytest.raises(Exception):
        await V.create_volume(session, pid, title="第二卷 应当消失", chapters=["甲", "乙", "丙"])

    vols_after = F.read_volumes(pid)
    assert len(vols_after) == vols_before, "失败后卷登记必须回滚"
    assert not any("第二卷" in str(v.get("title")) for v in vols_after)
    chapters_dir = Path(F.chapter_file(pid, vid, "x")).parent.parent
    assert len(list(chapters_dir.iterdir())) == 1, "回滚后只应剩第一卷的章节目录"
    assert len(F.list_chapter_files(pid, vid)) == 3, "已建章节文件必须被清掉，只留第一卷的 3 章"


async def test_rename_chapter_keeps_sort_order(session):
    pid = await _make_project(session)
    vid, ids = await _make_volume(session, pid, "第一卷", ["第一章 甲", "第二章 乙"])
    before = {c["id"]: c["sort_order"] for c in await V.list_chapters(session, pid, vid)}

    await V.update_chapter_meta(session, ids[0], title="改名后的章")
    await session.commit()

    after = await V.list_chapters(session, pid, vid)
    assert any(c["title"] == "改名后的章" for c in after)
    assert {c["id"]: c["sort_order"] for c in after} == before, "改名不得改动 sort_order"


# ---------------------------------------------------------------------------
# SafeDelete：可恢复删除
# ---------------------------------------------------------------------------


async def test_safe_delete_chapter_goes_to_recycle_not_physical(session):
    from app.agent.harness.sandbox.safe_delete import SafeDeleteMiddleware

    pid = await _make_project(session)
    vid, ids = await _make_volume(session, pid, "第一卷", ["第一章 甲", "第二章 乙", "第三章 丙"])
    vpath = f"/projects/{pid}/chapters/v{vid}/c{ids[0]}.md"

    rec = _Recorder()
    msg = await SafeDeleteMiddleware().awrap_tool_call(_fake_request("delete", {"file_path": vpath}), rec.handler)

    assert rec.calls == 0, "命中回收站分支时不得调用框架的物理删除"
    assert getattr(msg, "status", None) == "success"
    assert "回收站" in str(msg.content)
    assert not F.chapter_file(pid, vid, ids[0]).exists()
    assert any(str(e.get("novel_id")) == pid for e in R.list_recycled_novels())
    assert len(await V.list_chapters(session, pid, vid)) == 2


async def test_safe_delete_other_project_file_is_moved_to_trash(session):
    from app.agent.harness.sandbox.safe_delete import SafeDeleteMiddleware, agent_trash_dir
    from app.core.paths import workspace_dir

    pid = await _make_project(session)
    note = workspace_dir(pid) / "outline" / "notes.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("临时笔记\n", encoding="utf-8")

    rec = _Recorder()
    msg = await SafeDeleteMiddleware().awrap_tool_call(
        _fake_request("delete", {"file_path": f"/projects/{pid}/outline/notes.md"}), rec.handler
    )

    assert rec.calls == 0
    assert not note.exists()
    assert list(agent_trash_dir().rglob("notes.md")), "必须留底，不得物理销毁"
    assert getattr(msg, "status", None) == "success"


async def test_safe_delete_passes_through_non_project_and_non_delete(session):
    from app.agent.harness.sandbox.safe_delete import SafeDeleteMiddleware

    mw = SafeDeleteMiddleware()

    rec = _Recorder()
    await mw.awrap_tool_call(_fake_request("delete", {"file_path": "/conversation_history/x.md"}), rec.handler)
    assert rec.calls == 1, "项目外路径应保持框架原有行为"

    rec2 = _Recorder()
    await mw.awrap_tool_call(_fake_request("write_file", {"file_path": "/projects/p/chapters/v1/c1.md"}), rec2.handler)
    assert rec2.calls == 1, "非 delete 工具不应被拦截"


async def test_safe_delete_handles_backslash_path_variant(session):
    """反斜杠写法同样命中回收站分支（框架 validate_path 会归一化后照常执行）。"""
    from app.agent.harness.sandbox.safe_delete import SafeDeleteMiddleware

    pid = await _make_project(session)
    vid, ids = await _make_volume(session, pid, "第一卷", ["第一章 甲", "第二章 乙"])
    variant = f"\\projects\\{pid}\\chapters\\v{vid}\\c{ids[1]}.md"

    rec = _Recorder()
    await SafeDeleteMiddleware().awrap_tool_call(_fake_request("delete", {"file_path": variant}), rec.handler)

    assert rec.calls == 0
    assert not F.chapter_file(pid, vid, ids[1]).exists()


# ---------------------------------------------------------------------------
# 原子写
# ---------------------------------------------------------------------------


async def test_atomic_write_is_atomic_shaped_and_leaves_no_tmp(session):
    pid = await _make_project(session)
    vid, ids = await _make_volume(session, pid, "第一卷", ["第一章 甲"])

    meta, _ = F.read_chapter(pid, vid, ids[0])
    F.write_chapter(pid, vid, ids[0], meta, "新正文\n第二行\n")

    ws = Path(F.chapter_file(pid, vid, ids[0])).parent.parent.parent
    assert not list(ws.rglob("*.tmp")), "原子写不得留下 .tmp 残留"
    _again_meta, content = F.read_chapter(pid, vid, ids[0])
    assert content.splitlines()[:2] == ["新正文", "第二行"]


async def test_atomic_write_preserves_write_text_newlines(tmp_path):
    """★ 行尾必须与被替换掉的 `Path.write_text` 逐字节一致。

    `atomic_write_text` 若传 `newline=""` 会写成 LF，而 Windows 上 `write_text`
    默认翻译成 CRLF；`record_agent_write` 每次 AI 写入都会重写章节文件，行尾突变
    可能让 deepagents `edit_file` 的字面量匹配失败。
    """
    target = tmp_path / "a.md"
    reference = tmp_path / "b.md"
    atomic_write_text(target, "a\nb\n")
    reference.write_text("a\nb\n", encoding="utf-8")
    assert target.read_bytes() == reference.read_bytes()

    # 覆盖已存在文件同样成立，且父目录会自动创建
    nested = tmp_path / "sub" / "dir" / "c.md"
    atomic_write_text(nested, "x\n")
    assert nested.read_text(encoding="utf-8") == "x\n" or nested.read_bytes() == b"x\r\n"
    assert not list(tmp_path.rglob("*.tmp"))
