# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_naming.py
作者: xionggaoqing
时间: 2026-10-27
说明: 显式命名校验（手册「十一」必测清单 · 显式命名域，错误码 42201）。
"""

from __future__ import annotations

import pytest

from app.core.errors import NamingInvalidError
from app.services.naming import (
    validate_chapter_title,
    validate_novel_title,
    validate_volume_title,
)

#: 应被拒绝的名字（空名 / 默认名 / 纯序号名）
REJECTED = [
    "",
    "   ",
    "\u3000",
    None,
    "未命名小说",
    "未命名",
    "新建小说",
    "无题",
    "UNTITLED",
    "  untitled  ",
    "未命名卷",
    "未命名章节",
    "第 1 卷",
    "第1卷",
    "第一卷",
    "第 1 章",
    "第一章",
    "第三回",
    "第2节",
    "12",
    "卷一",
    "测试",
    "aaa",
]

#: 应通过的名字（合法名）
ACCEPTED = [
    "星海拾荒人",
    "荒原初醒",
    "拾荒少年的清晨",
    "第三章 卷土重来",  # 含序数前缀但有实质名，非纯序号
    "第一卷·风起云涌",
    "Winter is Coming",
    "拾荒者（外传）",
]


@pytest.mark.parametrize("bad", REJECTED)
def test_novel_title_rejected(bad):
    with pytest.raises(NamingInvalidError) as exc:
        validate_novel_title(bad)
    assert exc.value.code == 42201


@pytest.mark.parametrize("bad", REJECTED)
def test_volume_title_rejected(bad):
    with pytest.raises(NamingInvalidError):
        validate_volume_title(bad)


@pytest.mark.parametrize("bad", REJECTED)
def test_chapter_title_rejected(bad):
    with pytest.raises(NamingInvalidError):
        validate_chapter_title(bad)


@pytest.mark.parametrize("good", ACCEPTED)
def test_titles_accepted(good):
    assert validate_novel_title(good)
    assert validate_volume_title(good)
    assert validate_chapter_title(good)


def test_full_width_space_normalized():
    """全角空格归一后仍视为默认名。"""
    with pytest.raises(NamingInvalidError):
        validate_novel_title("未命名\u3000小说")


def test_long_title_rejected():
    with pytest.raises(NamingInvalidError):
        validate_novel_title("名" * 201)


def test_whitespace_trimmed_on_accept():
    assert validate_chapter_title("  拾荒少年的清晨  ") == "拾荒少年的清晨"
