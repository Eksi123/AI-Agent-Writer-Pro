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

#: 书名应被拒绝的名字（v1028 规则：空 / 全阿拉伯数字 / 含 #$% 等特殊字符）
NOVEL_REJECTED = [
    "",
    "   ",
    "\u3000",
    None,
    "12",
    "123456",
    "123 456",  # 去空白后仍全是阿拉伯数字
    "#星海",
    "星海$拾荒",
    "星海%拾荒",
    "星海&拾荒",
    "星海*拾荒",
    "星海+拾荒",
    "星海=拾荒",
    "星海<拾荒",
    "星海>拾荒",
    "星海@拾荒",
    "星海^拾荒",
    "星海|拾荒",
    "星海~拾荒",
]

#: v1029：卷名/章名的命名规则**与书名一致**（需求 5-(1.5)），故共用同一份拒绝清单
REJECTED = list(NOVEL_REJECTED)

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

#: v1028：书名允许默认名/序号名/标点（规则只禁空、全数字、特殊字符）
NOVEL_ACCEPTED = [
    *ACCEPTED,
    "未命名小说",
    "第 1 卷",
    "测试",
    "aaa",
    "拾荒者：黎明",  # 冒号等标点允许
    "星海·拾荒人",
    "Winter-is-Coming",  # 连字符允许
    "第3章",
]


@pytest.mark.parametrize("bad", NOVEL_REJECTED)
def test_novel_title_rejected(bad):
    with pytest.raises(NamingInvalidError) as exc:
        validate_novel_title(bad)
    assert exc.value.code == 42201


@pytest.mark.parametrize("good", NOVEL_ACCEPTED)
def test_novel_title_accepted(good):
    assert validate_novel_title(good)


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


@pytest.mark.parametrize("good", NOVEL_ACCEPTED)
def test_volume_chapter_accepted(good):
    """v1029：卷名/章名与书名同规则，书名可接受的卷章名也应接受。"""
    assert validate_volume_title(good)
    assert validate_chapter_title(good)


def test_full_width_space_normalized():
    """全角空格归一为半角后保留（v1029：默认名对卷章不再拒绝，与书名同规则）。"""
    assert validate_volume_title("未命名\u3000卷") == "未命名 卷"
    # 空/全空白仍拒绝
    with pytest.raises(NamingInvalidError):
        validate_volume_title("\u3000")


def test_long_title_rejected():
    with pytest.raises(NamingInvalidError):
        validate_novel_title("名" * 201)


def test_whitespace_trimmed_on_accept():
    assert validate_chapter_title("  拾荒少年的清晨  ") == "拾荒少年的清晨"
