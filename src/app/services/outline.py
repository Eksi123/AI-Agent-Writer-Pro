#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.services.outline
文件名: outline.py
作者: xionggaoqing
时间: 2026-10-27
说明: 大纲单文件：模板常量、`parse_outline` 严格层级解析、`normalize_title` 归一化、
    `sync_order_from_outline`（卷章 `sort_order` 的**唯一写入口**）。

    手册「四、故事知识库」要点：
    - 标题层级判定必须严格：H1 用 `^#(?!#)\\s+(.*)$`，H2 用 `^##(?!#)\\s+(.*)$`，H3 及更深忽略。
      宽松的 `^#\\s*第?.*卷` 会把 `## 第三章 卷土重来` 误判为卷节。
    - H1 中匹配 `^附录` 的为附录节；只有卷节下的 H2 才是章简介小节。
    - 卷/章名**归一化后完全相等**才算命中。
    - 解析全程只读、不写文件，不自动插入占位小节。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 项目创建时由服务层写入的模板（服务层常量，见手册「四」）
OUTLINE_TEMPLATE = """\
# {title} · 大纲

> 本文件是知识库之纲，也是 AI 生成正文的**必要前提**。
> 请保持标题层级：`#` 为卷（或附录），`##` 为章。卷章顺序由本文件小节的先后决定。

# 第一卷 卷名（请改为具体卷名）

## 第一章 章名（请改为具体章名）

- 简介：（必填）本章剧情要点，AI 生成正文时以此为依据。
- 衔接要点：（建议填写）与上一章的承接、伏笔、人物状态变化。

# 附录A 世界观

- 世界背景：
- 力量体系：
- 重要势力：

# 附录B 人物角色

- 姓名｜身份｜性格｜目标｜关键关系：

# 附录C 战力层次

- 层次划分与判定标准：

# 附录D 写作要求

- 视角与人称：
- 文风与节奏：
- 禁忌与雷区：
"""

# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

_H1 = re.compile(r"^#(?!#)\s+(.*)$")
_H2 = re.compile(r"^##(?!#)\s+(.*)$")
_APPENDIX = re.compile(r"^附录")

_INTRO_LINE = re.compile(r"^\s*[-*+]\s*简介\s*[:：]\s*(.*)$")
_LINK_LINE = re.compile(r"^\s*[-*+]\s*衔接要点\s*[:：]\s*(.*)$")

_CN_NUM = "零一二三四五六七八九十百千万两〇壹贰叁肆伍陆柒捌玖拾佰仟"
_ORDINAL_PREFIX = re.compile(rf"^第\s*[{_CN_NUM}\d]+\s*[卷章回节篇部集话話]\s*")
_PUNCT = re.compile(r"[·、,，.。:：\-—_~!！?？'\"“”‘’()（）\[\]【】]")


def normalize_title(name: str) -> str:
    """归一化卷/章名用于比较（手册「四」）：

    去首尾空白 → 全角空格转半角 → 压缩连续空白 → 去序数词前缀 → 去标点。
    """
    s = name.replace("\u3000", " ").strip()
    s = re.sub(r"\s+", "", s)
    s = _ORDINAL_PREFIX.sub("", s)
    s = _PUNCT.sub("", s)
    return s.strip()


@dataclass
class ChapterSection:
    """大纲中的一个章简介小节。"""

    title: str
    normalized: str
    line_no: int
    intro: str = ""
    link_note: str = ""

    @property
    def has_section(self) -> bool:
        return True

    @property
    def intro_empty(self) -> bool:
        return not self.intro.strip()


@dataclass
class VolumeSection:
    """大纲中的一个卷节（H1）。"""

    title: str
    normalized: str
    line_no: int
    chapters: list[ChapterSection] = field(default_factory=list)


@dataclass
class AppendixSection:
    """附录节（H1 且以「附录」开头），其下 H2 不参与章匹配。"""

    title: str
    line_no: int
    lines: list[str] = field(default_factory=list)


@dataclass
class OutlineParseResult:
    volumes: list[VolumeSection] = field(default_factory=list)
    appendices: list[AppendixSection] = field(default_factory=list)
    #: 格式问题（含错误行号），不阻断
    warnings: list[str] = field(default_factory=list)

    def find_chapter(self, volume_title: str, chapter_title: str) -> ChapterSection | None:
        """按归一化「卷名+章名」完全一致定位章简介小节。"""
        vn, cn = normalize_title(volume_title), normalize_title(chapter_title)
        for vol in self.volumes:
            if vol.normalized != vn:
                continue
            for ch in vol.chapters:
                if ch.normalized == cn:
                    return ch
        return None

    def find_volume(self, volume_title: str) -> VolumeSection | None:
        vn = normalize_title(volume_title)
        return next((v for v in self.volumes if v.normalized == vn), None)


def parse_outline(text: str) -> OutlineParseResult:
    """解析大纲单文件。全程只读、不写文件、不自动插入占位小节。"""
    result = OutlineParseResult()
    lines = text.split("\n")

    in_code_fence = False
    current_vol: VolumeSection | None = None
    current_ch: ChapterSection | None = None
    current_app: AppendixSection | None = None
    h1_seen = False

    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()

        # 代码围栏内的 # 不作标题解析
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            if current_app is not None:
                current_app.lines.append(raw)
            continue
        if in_code_fence:
            if current_app is not None:
                current_app.lines.append(raw)
            continue

        m1 = _H1.match(raw)
        if m1:
            h1_seen = True
            title = m1.group(1).strip()
            current_ch = None
            current_app = None
            if not title:
                result.warnings.append(f"L{idx}: H1 标题为空")
                current_vol = None
                continue
            if _APPENDIX.match(title):
                current_vol = None
                current_app = AppendixSection(title=title, line_no=idx)
                result.appendices.append(current_app)
            else:
                current_vol = VolumeSection(
                    title=title, normalized=normalize_title(title), line_no=idx
                )
                result.volumes.append(current_vol)
            continue

        m2 = _H2.match(raw)
        if m2:
            title = m2.group(1).strip()
            if current_app is not None:
                # 附录节下的 H2 视为附录子条目，不参与章匹配
                current_app.lines.append(raw)
                current_ch = None
                continue
            if current_vol is None:
                result.warnings.append(f"L{idx}: 章小节「{title}」不在任何卷节之下，已忽略")
                current_ch = None
                continue
            if not title:
                result.warnings.append(f"L{idx}: H2 标题为空")
                current_ch = None
                continue
            current_ch = ChapterSection(
                title=title, normalized=normalize_title(title), line_no=idx
            )
            current_vol.chapters.append(current_ch)
            continue

        # 正文行：归属到章简介 / 附录
        m_intro = _INTRO_LINE.match(raw)
        m_link = _LINK_LINE.match(raw)
        if current_ch is not None:
            if m_intro:
                current_ch.intro = (current_ch.intro + m_intro.group(1)).strip()
            elif m_link:
                current_ch.link_note = (current_ch.link_note + m_link.group(1)).strip()
        elif current_app is not None:
            current_app.lines.append(raw)

    if not h1_seen:
        result.warnings.append("L1: 大纲中没有任何 H1（卷节或附录节），请检查标题层级")

    return result


def render_template(title: str) -> str:
    """项目创建时写入的大纲模板内容。"""
    return OUTLINE_TEMPLATE.format(title=title)
