#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.services.naming
文件名: naming.py
作者: xionggaoqing
时间: 2026-10-27
说明: 显式命名校验（手册铁律 6 / 错误码 `42201`）。

    创建小说、卷、章**必须由用户显式命名**，不允许「未命名小说」「第 1 卷」「第 1 章」
    等默认名或纯序号名。前端表单强制必填，API 层同步拒绝。
"""

from __future__ import annotations

import re

from app.core.errors import NamingInvalidError

#: 中文数字 + 阿拉伯数字，用于识别「第X卷 / 第X章 / 第X回 / 第X节」
_CN_NUM = "零一二三四五六七八九十百千万两〇壹贰叁肆伍陆柒捌玖拾佰仟"
_ORDINAL_RE = re.compile(rf"^第\s*[{_CN_NUM}\d]+\s*[卷章回节篇部集话話]\s*$")

#: 明确的默认名 / 占位名（大小写与全半角空格归一后比较）
_DEFAULT_NAMES = {
    "未命名小说",
    "未命名",
    "新建小说",
    "无题",
    "untitled",
    "untitled novel",
    "new novel",
    "未命名卷",
    "新建卷",
    "未命名章节",
    "未命名章",
    "新建章节",
    "新章节",
    "new chapter",
    "new volume",
    "todo",
    "test",
    "测试",
    "aaa",
    "xxx",
}

#: 纯序号名：仅由数字、中文数字、序数词、标点空白构成
_PURE_ORDINAL_RE = re.compile(rf"^[{_CN_NUM}\d\s第卷章回节篇部集话話\.\-·_]+$")


def normalize_for_check(name: str) -> str:
    """归一化用于校验：全角空格转半角、压缩空白、去首尾。"""
    return re.sub(r"\s+", " ", name.replace("\u3000", " ")).strip()


def collapse_whitespace(name: str) -> str:
    """去掉全部空白（含全角空格），用于默认名比对。

    这样「未命名　小说」「未命名 小说」都会命中默认名 `未命名小说`，堵住插空格绕过的漏洞。
    """
    return re.sub(r"\s+", "", name.replace("\u3000", ""))


def validate_novel_title(title: str | None) -> str:
    """校验小说名。返回归一化后的标题；不合法抛 `NamingInvalidError`（42201）。"""
    return _validate(title, kind="小说名")


def validate_volume_title(title: str | None) -> str:
    """校验卷名。"""
    return _validate(title, kind="卷名")


def validate_chapter_title(title: str | None) -> str:
    """校验章名。"""
    return _validate(title, kind="章名")


def _validate(title: str | None, *, kind: str) -> str:
    if title is None:
        raise NamingInvalidError(f"{kind}不能为空")

    name = normalize_for_check(title)
    if not name:
        raise NamingInvalidError(f"{kind}不能为空")

    lowered = name.lower()
    collapsed = collapse_whitespace(lowered)
    if lowered in _DEFAULT_NAMES or collapsed in _DEFAULT_NAMES:
        raise NamingInvalidError(f"{kind}不允许使用默认名「{name}」，请填写具体名称")

    # 纯序号名：如「第 1 卷」「第三章」「12」「卷一」（含空白/全角空格变体）
    if _ORDINAL_RE.match(name) or _PURE_ORDINAL_RE.match(name) or _ORDINAL_RE.match(collapsed):
        raise NamingInvalidError(f"{kind}不允许使用纯序号名「{name}」，请填写具体名称")

    if len(name) > 200:
        raise NamingInvalidError(f"{kind}过长（最多 200 字符）")

    return name
