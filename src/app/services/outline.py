# -*- coding: utf-8 -*-
"""
模块: app.services.outline
文件名: outline.py
作者: xionggaoqing
时间: 2026-10-27
说明: 大纲单文件：模板常量、`parse_outline` 严格层级解析、`normalize_title` 归一化。

    大纲降级为「参考资料」（AI 生成正文的重要依据），**不再回填卷章 sort_order**；
    卷章结构与顺序由 `services/volumes.py` 直接维护（插入即顺延、删空即删卷）。

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
# {title}_大纲


- 备注：大纲是小说创作的重要参考资料，且AI 辅助生成正文时以各章「简介」为依据。 **强烈建议保持标题层级**（`#` 为卷，`##` 为章）便于AI阅读与检索
- 备注2：附录部分不是AI生成正文的必须要求，但附录存在时也会参考
- 备注3：当小说章节和剧情变化时，建议同步修改大纲文件，也可由AI辅助更新

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

#: H1：允许尾部 #（如 `# Title #`），允许无空格（`#Title`），捕获标题文本
_H1 = re.compile(r"^#{1}(?!#)\s*(.*?)(?:\s+#+)?\s*$")
#: H2：同上
_H2 = re.compile(r"^#{2}(?!#)\s*(.*?)(?:\s+#+)?\s*$")
#: 附录标题匹配：去掉 markdown 格式后以「附录」开头
_APPENDIX = re.compile(r"^附录")

#: 简介行：宽容匹配多种格式
#: - 列表符号：`-` `*` `+` `•`（全角圆点）`·`（中间点）或无符号
#: - 数字列表：`1.` `1)` `1）` `(1)` `①` 等
#: - 关键词：`简介`，支持 `**简介**` `_简介_` `***简介***` 等 markdown 格式
#: - 分隔符：`：` `:` `-` `—` 或直接空格，允许多个分隔符
#: - 空内容也匹配（`- 简介：` 捕获空串）
#: - 示例：`- 简介：xxx` `• **简介**：xxx` `1. 简介：xxx` `简介 xxx` `- 简介：`
_INTRO_LINE = re.compile(
    r"^\s*"
    r"(?:"
    r"[-*+•·]\s*"                          # 符号列表
    r"|\d+[.)]\s*"                        # 数字列表 1. 1)
    r"|\d+）\s*"                           # 中文数字列表 1）
    r"|\(\d+\)\s*"                      # 括号数字 (1)
    r"|[①-⑳]\s*"                          # 带圈数字
    r")?"
    r"\*{0,3}_?简介_?\*{0,3}"             # 关键词（支持粗/斜/粗斜体）
    r"\s*"
    r"(?:[:：\-—]\s*)*"                    # 分隔符（可选，允许多个）
    r"(.*)$"                                 # 内容（可为空）
)
#: 衔接要点行：同上，额外支持短别名「衔接」「过渡」「承上启下」
_LINK_LINE = re.compile(
    r"^\s*"
    r"(?:"
    r"[-*+•·]\s*"
    r"|\d+[.)]\s*"
    r"|\d+）\s*"
    r"|\(\d+\)\s*"
    r"|[①-⑳]\s*"
    r")?"
    r"\*{0,3}_?(?:衔接要点|衔接|过渡|承上启下)_?\*{0,3}"
    r"\s*"
    r"(?:[:：\-—]\s*)*"
    r"(.*)$"
)

_CN_NUM = "零一二三四五六七八九十百千万两〇壹贰叁肆伍陆柒捌玖拾佰仟"
#: 序数前缀：支持 `第X卷` `X、` `X.` `X卷` `X 标题` 等多种格式（`第` 可选）
#: 最后一项 `[{_CN_NUM}\d]{1,2}\s+` 匹配短数字+空格（如 `2 蟠桃会` `二 蟠桃会`），
#: 限定 1-2 位避免误删标题中的年份等数字（如 `2024 年`）。
_ORDINAL_PREFIX = re.compile(
    rf"^(?:第\s*)?[{_CN_NUM}\d]+\s*(?:[卷章回节篇部集话話]|[、.])\s*"
    rf"|^[{_CN_NUM}\d]{{1,2}}\s+"
)
_PUNCT = re.compile(r"[·、,，.。:：\-—_~!！?？'\"\u201c\u201d\u2018\u2019()（）\[\]【】{}｛｝]")
#: 去除 markdown 格式标记：`**bold**` `*italic*` `_italic_` `~~strike~~` `__bold__`
_MD_FORMAT = re.compile(r"\*{1,3}|_{1,2}|~{2}")
#: 去除尾部括号标注：`（上）` `(上)` `【上】` `[上]`（含 ASCII 括号）
_TRAILING_ANNOT = re.compile(r"[\[（【(][^\]）】)]*[\]）】)]\s*$")


def normalize_title(name: str) -> str:
    """归一化卷/章名用于比较（手册「四」）：

    去首尾空白 → 全角空格转半角 → 去尾部括号标注 → 去 markdown 格式 →
    去序数词前缀（需在去空白之前，否则 `2 标题` 的空格丢失后无法匹配） →
    压缩连续空白 → 去标点。
    """
    s = name.replace("\u3000", " ").strip()
    # 去尾部括号标注：（上）(上)【上】[上]
    s = _TRAILING_ANNOT.sub("", s)
    # 去 markdown 格式：**bold** *italic* _italic_ ~~strike~~
    s = _MD_FORMAT.sub("", s)
    # 去序数前缀（必须在去空白之前）
    s = _ORDINAL_PREFIX.sub("", s)
    s = re.sub(r"\s+", "", s)
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


def _clean_title(raw: str) -> str:
    """清理标题行：去首尾空白、去尾部 #、去 markdown 格式。"""
    s = raw.strip()
    # 去尾部 #（如 `Title #` 或 `Title ##`）
    s = re.sub(r"\s+#+\s*$", "", s)
    # 去 markdown 格式：**bold** *italic* _italic_ ~~strike~~
    s = _MD_FORMAT.sub("", s)
    return s.strip()


def parse_outline(text: str) -> OutlineParseResult:
    """解析大纲单文件。全程只读、不写文件、不自动插入占位小节。"""
    result = OutlineParseResult()
    # 去 BOM
    if text.startswith("\ufeff"):
        text = text[1:]
    # 统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    in_code_fence = False
    fence_depth = 0  # 支持嵌套代码围栏
    current_vol: VolumeSection | None = None
    current_ch: ChapterSection | None = None
    current_app: AppendixSection | None = None
    h1_seen = False

    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()

        # 代码围栏内的 # 不作标题解析（支持 ``` 和 ~~~，支持带语言标记如 ```python）
        fence_match = re.match(r"^\s*(`{3,}|~{3,})", stripped)
        if fence_match:
            fence_char = fence_match.group(1)[0]
            if not in_code_fence:
                in_code_fence = True
                _fence_char = fence_char
            elif fence_char == _fence_char:
                in_code_fence = False
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
            title = _clean_title(m1.group(1))
            current_ch = None
            current_app = None
            if not title:
                result.warnings.append(f"L{idx}: H1 标题为空")
                current_vol = None
                continue
            # 附录检测：去掉 markdown 格式后以「附录」开头
            clean_for_check = _MD_FORMAT.sub("", title).strip()
            if _APPENDIX.match(clean_for_check):
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
            title = _clean_title(m2.group(1))
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
