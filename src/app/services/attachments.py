# -*- coding: utf-8 -*-
"""
模块: app.services.attachments
文件名: attachments.py
作者: xionggaoqing
时间: 2026-11-05
说明: 添加文件（附件）落盘 + 隐藏上下文指针化（降低每轮注入的 token）。

    「添加文件」的全文原本内联在 hidden_context 里随消息注入——文件一大，
    每次模型调用都要重发全文。改为：
    - 附件全文落盘到工作区 `attachments/{tag}_{n}.md`（虚拟路径
      `/projects/{pid}/attachments/…`，不在读取守卫拦截范围，AI 可自由 read_file）；
    - hidden_context 只注入**指针块**（文件名 / 字数 / 前 500 字摘要 / 全文路径）；
    - 正文短于 `_MIN_POINTERIZE_LEN` 的小附件保持内联（指针块自带摘要与固定开销，
      对短文本反而更长）；
    - 选区润色块（`【选中待润色文本】`开头）维持全文内联不变——选区即操作对象。

    跨 run 不重发维持现状（hidden_context 按 run_id 持有，run 结束即清除）；
    attachments 目录保留供会话内重读，`prune_attachments` 只保留最近 20 批。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.core.paths import attachments_dir, write_text
from app.services import files

logger = get_logger(__name__)

#: hidden_context 里各内容块的分隔符（与前端 composeHiddenContext 约定一致）
BLOCK_SEP = "\n\n---\n\n"

#: 附件块开头：`【标签】` + 换行 + 全文
_ATTACHMENT_HEAD = re.compile(r"^【(?P<label>[^】]{1,120})】\n")

#: 块边界：分隔符后紧跟 `【…】` 块头。
#: 附件正文自带 `---`（Markdown 水平线 / frontmatter）时不会被误切成两块。
_BLOCK_BOUNDARY = re.compile(rf"{re.escape(BLOCK_SEP)}(?=【[^】]{{1,120}}】)")

#: 选区润色块开头（保持内联，不指针化）
_POLISH_HEAD = "【选中待润色文本】"

#: 指针块里的摘要长度
_SUMMARY_LEN = 500

#: 指针化的最小收益门槛：正文短于此长度时**保持内联**。
#: 指针块 = 摘要（≤500 字）+ 固定开销（标签/字数/路径/提示约 180 字），
#: 故正文 ≤680 字时指针块反而比原文更长——小附件直接内联更省 token。
_MIN_POINTERIZE_LEN = 700

#: 单个附件落盘上限（超出截断，指针块注明）
_MAX_SAVE_BYTES = 2 * 1024 * 1024

#: attachments 目录最多保留的批次（按文件名里的 tag 分组）
_KEEP_BATCHES = 20


def rewrite_hidden_context(project_id: str, tag: str, hidden_context: str) -> str:
    """把 hidden_context 里的附件块替换为指针块（纯函数 + 落盘，可单测）。

    Args:
        project_id: 当前项目。
        tag: 本批附件的文件名标记（uuid，批内唯一）。
        hidden_context: 前端组装的隐藏上下文原文。

    Returns:
        指针化后的 hidden_context；无附件块时原样返回。
    """
    text = (hidden_context or "").strip()
    if not text:
        return hidden_context or ""

    out: list[str] = []
    index = 0
    for seg in _split_blocks(text):
        head = _ATTACHMENT_HEAD.match(seg)
        if head is None or seg.startswith(_POLISH_HEAD):
            out.append(seg)
            continue
        label = head.group("label").strip()
        body = seg[head.end():].strip()
        if len(body) <= _MIN_POINTERIZE_LEN:
            out.append(seg)  # 小附件内联更省（指针块本身就有摘要+开销）
            continue
        index += 1
        out.append(_pointer_block(project_id, tag, index, label, body))
    return BLOCK_SEP.join(out)


def _split_blocks(text: str) -> list[str]:
    """按块边界切分（只在「分隔符 + 块头」处切，正文里的 `---` 不切）。"""
    parts: list[str] = []
    start = 0
    for match in _BLOCK_BOUNDARY.finditer(text):
        parts.append(text[start:match.start()])
        start = match.end()
    parts.append(text[start:])
    return parts


def _pointer_block(project_id: str, tag: str, index: int, label: str, body: str) -> str:
    """附件落盘并生成指针块（落盘失败时退回内联，绝不丢用户内容）。"""
    rel = f"attachments/{tag}_{index}.md"
    try:
        _path, truncated = save_attachment(project_id, rel, body)
    except OSError as exc:
        logger.warning("attachment_save_failed", project_id=project_id, label=label, error=str(exc))
        return f"【{label}】\n{body}"

    word_count = files.count_words(body)
    summary = re.sub(r"\s+", " ", body[:_SUMMARY_LEN]).strip()
    lines = [
        f"【附件 {index}】{label}",
        f"字数：{word_count}",
        f"内容摘要（前 {_SUMMARY_LEN} 字）：{summary}",
        f"全文路径：/projects/{project_id}/{rel}"
        + ("（超长已截断保存）" if truncated else ""),
        "（需要全文时用 read_file 读上述路径；不要凭空猜测附件内容）",
    ]
    return "\n".join(lines)


def save_attachment(project_id: str, rel: str, body: str) -> tuple[Path, bool]:
    """附件全文落盘（utf-8）。返回 `(path, truncated)`；超过 `_MAX_SAVE_BYTES` 截断保存。"""
    directory = attachments_dir(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / Path(rel).name
    content = body
    truncated = False
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_SAVE_BYTES:
        content = encoded[:_MAX_SAVE_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    write_text(path, content)
    return path, truncated


def prune_attachments(project_id: str, *, keep: int = _KEEP_BATCHES) -> dict[str, Any]:
    """只保留最近 `keep` 批附件（按文件名 `{tag}_{n}.md` 的 tag 分组，mtime 新者优先）。"""
    directory = attachments_dir(project_id)
    if not directory.exists():
        return {"removed": 0, "kept_batches": 0}
    files_list = [p for p in directory.iterdir() if p.is_file() and p.suffix == ".md"]
    batches: dict[str, list[Path]] = {}
    for p in files_list:
        tag = p.stem.rsplit("_", 1)[0] if "_" in p.stem else p.stem
        batches.setdefault(tag, []).append(p)
    ordered = sorted(batches.items(), key=lambda kv: max(f.stat().st_mtime for f in kv[1]), reverse=True)
    removed = 0
    for _tag, batch in ordered[keep:]:
        for p in batch:
            try:
                p.unlink()
                removed += 1
            except OSError:  # pragma: no cover - 清理失败不阻断
                logger.warning("attachment_prune_failed", path=str(p))
    return {"removed": removed, "kept_batches": min(len(ordered), keep)}


__all__ = [
    "BLOCK_SEP",
    "prune_attachments",
    "rewrite_hidden_context",
    "save_attachment",
]
