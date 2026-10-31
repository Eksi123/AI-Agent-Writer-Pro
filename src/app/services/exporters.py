# -*- coding: utf-8 -*-
"""
模块: app.services.exporters
文件名: exporters.py
作者: xionggaoqing
时间: 2026-10-27
说明: 导出：zip 产物 = 正文（markdown/epub/mobi/txt 单选）+ `{小说名称}-大纲.md` 原件。

    手册「三、(3) 导出」与「十一 · EPUB」要点：
    - EPUB 3 自研 `zipfile` 生成器：`mimetype` 必须为**首个条目且 STORE 不压缩**，
      含 `META-INF/container.xml` / `OEBPS/content.opf` / `OEBPS/nav.xhtml` / 各章 xhtml；
    - mobi 由 EPUB 产物经**可选外部工具** calibre `ebook-convert` 转换，未安装则 503 / `50301`；
    - txt 自研纯文本拼接（卷章标题 + 正文按顺序连接）；
    - 异步任务落 `data/exports/`，动作写审计。
"""

from __future__ import annotations

import contextlib
import html
import json
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.core.errors import DependencyUnavailableError, NotFoundError
from app.core.logging import get_logger
from app.core.paths import exports_root, outline_filename, safe_filename
from app.services import files

logger = get_logger(__name__)

ExportFormat = Literal["markdown", "epub", "mobi", "txt"]

_CALIBRE_CANDIDATES = ("ebook-convert", "ebook-convert.exe", "ebook-convert.cmd")
_PROBE_TIMEOUT_SEC = 10


# ---------------------------------------------------------------------------
# calibre 探测
# ---------------------------------------------------------------------------


def find_calibre() -> str | None:
    """探测 calibre `ebook-convert` 可执行文件（带超时）。未安装返回 None。"""
    exe = shutil.which("ebook-convert")
    if exe:
        return exe
    for name in _CALIBRE_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    # Windows 默认安装路径兜底
    for candidate in (
        Path(r"C:\Program Files\Calibre2\ebook-convert.exe"),
        Path(r"C:\Program Files (x86)\Calibre2\ebook-convert.exe"),
    ):
        if candidate.exists():
            return str(candidate)
    return None


def calibre_available() -> bool:
    return find_calibre() is not None


# ---------------------------------------------------------------------------
# 任务模型（进程内；单机单用户）
# ---------------------------------------------------------------------------


@dataclass
class ExportTask:
    task_id: str
    novel_id: str
    title: str
    fmt: str
    status: str = "pending"  # pending|running|done|failed
    progress: int = 0
    file_path: Path | None = None
    error: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )

    def to_dto(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "novel_id": self.novel_id,
            "format": self.fmt,
            "status": self.status,
            "progress": self.progress,
            "download_url": (
                f"/api/v1/novels/{self.novel_id}/export-tasks/{self.task_id}/download"
                if self.status == "done" and self.file_path
                else None
            ),
            "filename": self.file_path.name if self.file_path else None,
            "error": self.error,
            "created_at": self.created_at,
        }


def create_task(store: dict[str, ExportTask], *, novel_id: str, title: str, fmt: str) -> ExportTask:
    task = ExportTask(
        task_id=uuid.uuid4().hex[:12], novel_id=novel_id, title=title, fmt=fmt
    )
    store[task.task_id] = task
    return task


def run_export(store: dict[str, ExportTask], task_id: str, novel_id: str, fmt: str) -> None:
    """后台任务执行体（`BackgroundTasks` 调用，同步实现）。"""
    task = store.get(task_id)
    if task is None:  # pragma: no cover
        return
    task.status = "running"
    try:
        path = build_export(novel_id, fmt, progress=lambda p: setattr(task, "progress", p))
        task.file_path = path
        task.status = "done"
        task.progress = 100
        logger.info("export_done", task_id=task_id, format=fmt, path=str(path))
    except Exception as exc:
        task.status = "failed"
        task.error = f"{type(exc).__name__}: {exc}"
        logger.error("export_failed", task_id=task_id, format=fmt, error=str(exc))


# ---------------------------------------------------------------------------
# 内容采集
# ---------------------------------------------------------------------------


@dataclass
class ExportVolume:
    title: str
    sort_order: int
    chapters: list[dict[str, Any]]


def collect_book(project_id: str) -> tuple[dict[str, Any], list[ExportVolume], str | None]:
    """采集全书内容：`(novel_meta, volumes(含正文), outline_text)`。

    顺序一律按 `sort_order`（大纲派生值），与阅读/EPUB 顺序一致。
    """
    meta = files.read_novel_meta(project_id)
    if not meta:
        raise NotFoundError(f"项目元信息缺失：{project_id}")

    raw_volumes = sorted(
        files.read_volumes(project_id),
        key=lambda v: (int(v.get("sort_order") or 0), str(v.get("title") or "")),
    )
    result: list[ExportVolume] = []
    for vol in raw_volumes:
        vol_id = str(vol.get("id") or "")
        chapters: list[dict[str, Any]] = []
        for path in files.list_chapter_files(project_id, vol_id):
            chapter_id = path.stem[1:]
            try:
                ch_meta, content = files.read_chapter(project_id, vol_id, chapter_id)
            except Exception as exc:  # pragma: no cover
                logger.warning("export_chapter_read_failed", path=str(path), error=str(exc))
                continue
            chapters.append(
                {
                    "id": chapter_id,
                    "title": str(ch_meta.get("title") or chapter_id),
                    "sort_order": int(ch_meta.get("sort_order") or 0),
                    "content": content,
                    "word_count": int(ch_meta.get("word_count") or 0),
                    "status": ch_meta.get("status") or "draft",
                }
            )
        chapters.sort(key=lambda c: (c["sort_order"], c["title"]))
        result.append(
            ExportVolume(
                title=str(vol.get("title") or vol_id),
                sort_order=int(vol.get("sort_order") or 0),
                chapters=chapters,
            )
        )

    # 大纲原件（按 novel.yaml.outline_file 寻址，避免按 title 现推产生漂移）
    outline_text: str | None = None
    outline_rel = str(meta.get("outline_file") or "")
    if outline_rel:
        outline_path = exports_root().parent / "workspaces" / project_id / outline_rel
        if outline_path.exists():
            outline_text = outline_path.read_text(encoding="utf-8")

    return meta, result, outline_text


# ---------------------------------------------------------------------------
# zip 组装
# ---------------------------------------------------------------------------


def build_export(project_id: str, fmt: ExportFormat, *, progress: Any = None) -> Path:
    """生成导出 zip 并返回路径。"""
    meta, vols, outline_text = collect_book(project_id)
    title = str(meta.get("title") or project_id)

    if fmt == "mobi" and not calibre_available():
        raise DependencyUnavailableError(
            "未检测到 calibre，无法转换 mobi。请安装 calibre 后重试，或选择 epub/txt/markdown。"
        )

    def report(p: int) -> None:
        if progress is not None:
            with contextlib.suppress(Exception):  # 进度回调失败不影响导出
                progress(p)

    report(10)
    exports_root().mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    zip_path = exports_root() / f"{safe_filename(title)}-{fmt}-{stamp}.zip"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if fmt == "markdown":
            _write_markdown(zf, title, vols)
        elif fmt == "txt":
            _write_txt(zf, title, meta, vols)
        elif fmt in ("epub", "mobi"):
            _write_epub(zf, title, meta, vols)
        else:  # pragma: no cover - 路由层已限制取值
            raise ValueError(f"不支持的导出格式：{fmt}")
        report(60)

        # 大纲原 markdown（不转换格式）
        if outline_text is not None:
            zf.writestr(outline_filename(title), outline_text)
        report(80)

        # 元信息清单，便于校验与排障
        zf.writestr(
            "export-manifest.json",
            _manifest_json(title, fmt, vols, outline_text is not None),
        )

    if fmt == "mobi":
        _convert_to_mobi(zip_path, title)

    report(100)
    return zip_path


def _manifest_json(title: str, fmt: str, vols: list[ExportVolume], has_outline: bool) -> str:

    payload = {
        "title": title,
        "format": fmt,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "volume_count": len(vols),
        "chapter_count": sum(len(v.chapters) for v in vols),
        "word_count": sum(c["word_count"] for v in vols for c in v.chapters),
        "includes_outline": has_outline,
        "volumes": [
            {
                "title": v.title,
                "sort_order": v.sort_order,
                "chapters": [{"title": c["title"], "sort_order": c["sort_order"]} for c in v.chapters],
            }
            for v in vols
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# markdown / txt
# ---------------------------------------------------------------------------


def _write_markdown(zf: zipfile.ZipFile, title: str, vols: list[ExportVolume]) -> None:
    """markdown：所有章节 md 集合（目录 + 各章独立文件）。"""
    toc_lines = [f"# {title}", "", "## 目录", ""]
    for vi, vol in enumerate(vols, start=1):
        toc_lines.append(f"{vi}. **{vol.title}**")
        for ci, ch in enumerate(vol.chapters, start=1):
            toc_lines.append(f"   {vi}.{ci} {ch['title']}")
    zf.writestr("正文/00-目录.md", "\n".join(toc_lines) + "\n")

    for vi, vol in enumerate(vols, start=1):
        vol_dir = f"正文/{vi:02d}-{safe_filename(vol.title)}"
        zf.writestr(f"{vol_dir}/00-卷首.md", f"# {vol.title}\n")
        for ci, ch in enumerate(vol.chapters, start=1):
            body = ch["content"].strip()
            zf.writestr(
                f"{vol_dir}/{ci:03d}-{safe_filename(ch['title'])}.md",
                f"# {ch['title']}\n\n{body}\n" if body else f"# {ch['title']}\n",
            )


def _write_txt(zf: zipfile.ZipFile, title: str, meta: dict[str, Any], vols: list[ExportVolume]) -> None:
    """txt：整本小说（卷章标题 + 正文顺序连接）。"""
    parts: list[str] = [title, ""]
    author = str(meta.get("author_name") or "").strip()
    if author:
        parts.extend([f"作者：{author}", ""])
    parts.append("=" * 40)

    for vol in vols:
        parts.extend(["", "", vol.title, "-" * 40, ""])
        for ch in vol.chapters:
            parts.extend([ch["title"], ""])
            body = ch["content"].strip()
            if body:
                parts.append(body)
            parts.append("")

    zf.writestr(f"{safe_filename(title)}.txt", "\n".join(parts))


# ---------------------------------------------------------------------------
# EPUB 3
# ---------------------------------------------------------------------------

_MIMETYPE = "application/epub+zip"

_CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CSS = """\
@charset "utf-8";
body { font-family: serif; line-height: 1.8; margin: 1.5em; }
h1 { font-size: 1.6em; margin: 1.2em 0 0.8em; }
h2 { font-size: 1.3em; margin: 1em 0 0.6em; }
p { text-indent: 2em; margin: 0.4em 0; }
.titlepage { text-align: center; margin-top: 4em; }
"""


def _md_paragraphs_to_xhtml(md_text: str) -> str:
    """把章节 markdown 正文转成极简 xhtml（段落 + 基本行内标记）。

    不引入重型依赖；只处理 EPUB 阅读所需的常见结构。
    """
    out: list[str] = []
    for raw_line in md_text.split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{level}>{_inline(html.escape(m.group(2).strip()))}</h{level}>")
            continue
        if re.match(r"^\s*([-*+]|\d+\.)\s+", line):
            content = re.sub(r"^\s*([-*+]|\d+\.)\s+", "", line)
            out.append(f"<p class=\"li\">{_inline(html.escape(content))}</p>")
            continue
        if re.match(r"^\s*(---|\*\*\*|___)\s*$", line):
            out.append("<hr/>")
            continue
        out.append(f"<p>{_inline(html.escape(line.strip()))}</p>")
    return "\n".join(out) or "<p></p>"


def _inline(text: str) -> str:
    """行内标记：**粗体**、*斜体*、`代码`（输入已 html-escape）。"""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+?)`", r"<code>\1</code>", text)
    return text


def _write_epub(zf: zipfile.ZipFile, title: str, meta: dict[str, Any], vols: list[ExportVolume]) -> None:
    """EPUB 3 生成。★`mimetype` 必须是首个条目且 STORE 不压缩。"""
    # ① mimetype：首条目 + 不压缩（EPUB 规范硬性要求）
    info = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o600 << 16
    zf.writestr(info, _MIMETYPE)

    # ② 容器与样式
    zf.writestr("META-INF/container.xml", _CONTAINER_XML)
    zf.writestr("OEBPS/style.css", _CSS)

    author = str(meta.get("author_name") or "").strip() or "佚名"
    lang = "zh-CN"
    book_id = f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest_items: list[str] = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>',
        '    <item id="css" href="style.css" media-type="text/css"/>',
    ]
    spine_items: list[str] = ['    <itemref idref="titlepage"/>']
    nav_entries: list[str] = []

    # ③ 扉页
    zf.writestr(
        "OEBPS/titlepage.xhtml",
        _xhtml_page(
            title,
            f'<div class="titlepage"><h1>{html.escape(title)}</h1>'
            f"<p>{html.escape(author)}</p></div>",
        ),
    )

    # ④ 卷 / 章 xhtml
    counter = 0
    for vol in vols:
        counter += 1
        vol_id_attr = f"vol{counter:03d}"
        vol_href = f"{vol_id_attr}.xhtml"
        manifest_items.append(
            f'    <item id="{vol_id_attr}" href="{vol_href}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'    <itemref idref="{vol_id_attr}"/>')
        nav_entries.append(f'        <li><a href="{vol_href}">{html.escape(vol.title)}</a>')

        child_entries: list[str] = []
        for ch in vol.chapters:
            counter += 1
            ch_id = f"ch{counter:05d}"
            ch_href = f"{ch_id}.xhtml"
            body = _md_paragraphs_to_xhtml(ch["content"])
            zf.writestr(
                f"OEBPS/{ch_href}",
                _xhtml_page(ch["title"], f"<h2>{html.escape(ch['title'])}</h2>\n{body}"),
            )
            manifest_items.append(
                f'    <item id="{ch_id}" href="{ch_href}" media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'    <itemref idref="{ch_id}"/>')
            child_entries.append(
                f'          <li><a href="{ch_href}">{html.escape(ch["title"])}</a></li>'
            )
        # 卷首页本身
        zf.writestr(
            f"OEBPS/{vol_href}",
            _xhtml_page(vol.title, f"<h1>{html.escape(vol.title)}</h1>"),
        )
        if child_entries:
            nav_entries.append("        <ol>")
            nav_entries.extend(child_entries)
            nav_entries.append("        </ol>")
        nav_entries.append("        </li>")

    # ⑤ nav.xhtml（EPUB3 导航文档）
    zf.writestr("OEBPS/nav.xhtml", _nav_xhtml(title, nav_entries))

    # ⑥ content.opf
    zf.writestr(
        "OEBPS/content.opf",
        _content_opf(
            title=title,
            author=author,
            lang=lang,
            book_id=book_id,
            modified=modified,
            manifest_items=manifest_items,
            spine_items=spine_items,
        ),
    )


def _xhtml_page(title: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN" lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
{body}
</body>
</html>
"""


def _nav_xhtml(title: str, entries: list[str]) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN" lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>目录</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>目录</h1>
    <ol>
{chr(10).join(entries)}
    </ol>
  </nav>
</body>
</html>
"""


def _content_opf(
    *,
    title: str,
    author: str,
    lang: str,
    book_id: str,
    modified: str,
    manifest_items: list[str],
    spine_items: list[str],
) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="BookId" xml:lang="{lang}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">{book_id}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:creator>{html.escape(author)}</dc:creator>
    <dc:language>{lang}</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
{chr(10).join(manifest_items)}
  </manifest>
  <spine toc="nav">
{chr(10).join(spine_items)}
  </spine>
</package>
"""


# ---------------------------------------------------------------------------
# mobi（可选外部工具 calibre）
# ---------------------------------------------------------------------------


def _convert_to_mobi(zip_path: Path, title: str) -> None:
    """用 calibre 把 EPUB 转 mobi，产物一并放进 zip。

    失败则抛 `DependencyUnavailableError`（503 / 50301），调用方标记任务 failed。
    """
    exe = find_calibre()
    if exe is None:
        raise DependencyUnavailableError("未检测到 calibre `ebook-convert`，无法生成 mobi")


    with tempfile.TemporaryDirectory(prefix="writer-mobi-") as tmp:
        tmp_dir = Path(tmp)
        epub_path = tmp_dir / f"{safe_filename(title)}.epub"
        mobi_path = tmp_dir / f"{safe_filename(title)}.mobi"

        # 从导出 zip 中还原 epub（zip 内即 epub 结构）
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = [n for n in zf.namelist() if n == "mimetype" or n.startswith(("META-INF/", "OEBPS/"))]
            with zipfile.ZipFile(epub_path, "w", compression=zipfile.ZIP_DEFLATED) as ep:
                mi = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
                mi.compress_type = zipfile.ZIP_STORED
                ep.writestr(mi, zf.read("mimetype"))
                for n in names:
                    if n == "mimetype":
                        continue
                    ep.writestr(n, zf.read(n))

        try:
            proc = subprocess.run(  # noqa: S603 - 白名单可执行文件，参数固定
                [exe, str(epub_path), str(mobi_path)],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DependencyUnavailableError(f"calibre 转换失败：{exc}") from exc

        if proc.returncode != 0 or not mobi_path.exists():
            raise DependencyUnavailableError(
                f"calibre 转换失败（exit={proc.returncode}）：{(proc.stderr or proc.stdout or '')[:300]}"
            )

        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(mobi_path, mobi_path.name)
        logger.info("mobi_converted", calibre=exe, mobi=mobi_path.name)
