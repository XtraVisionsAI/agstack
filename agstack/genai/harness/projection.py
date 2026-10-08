#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""「日志 → 模型历史」投影引擎

会话日志里既有 user / assistant 消息，也有「发生过的事实」事件行（范围变更、产物生命周期）与被遮蔽的旧行。进模型
上下文前经本模块投影：遮蔽行过滤 → 事件行按应用给的规则投影（或丢弃）→ 消息正文按应用给的规则改写（如起草轮
正文替换为一行摘要）→ 附带 system note → 连续同角色合并。

本模块只有机制：行的形状（``role`` / ``content`` / ``metadata`` / ``shadowed_by`` 属性）与 :class:`LogEvent` 同，
应用的 ORM 行对象直接可用；事件体怎么取、哪些事件投影成什么、怎样跨行折叠、怎样改写正文，全由
:class:`Projection` 的回调给。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


#: 事件体提取：``row -> 事件体 | None``（None＝非事件行）
type EventOf = Callable[[Any], dict[str, Any] | None]
#: 单条事件投影：``(事件体, 正文) -> 模型消息 | None``
type ProjectEvent = Callable[[dict[str, Any], str | None], dict[str, Any] | None]
#: 跨行判定构造：用整批行构造「该事件是否跳过」的判定（如同产物已有终态则丢弃其无原话的发起事件）
type EventSkipper = Callable[[Sequence[Any]], Callable[[dict[str, Any]], bool]]
#: 正文改写：``(role, content, metadata) -> content``
type Rewrite = Callable[[str, str, dict[str, Any] | None], str]
#: 附带注记：``(role, metadata) -> 追加在该消息之后的消息列表``
type Notes = Callable[[str, dict[str, Any] | None], Sequence[dict[str, Any]]]
#: 槽位判定：``row -> 是否按对话级别占一个槽位``
type IsDialogueGrade = Callable[[Any], bool]


def is_shadowed(row: Any) -> bool:
    """被重试 / 溢出恢复 / 折叠遮蔽的行不进模型历史"""
    return bool(getattr(row, "shadowed_by", None))


def select_recent(
    rows_newest_first: Sequence[Any],
    limit: int,
    is_dialogue_grade: IsDialogueGrade,
    *,
    attach: IsDialogueGrade | None = None,
) -> list[Any]:
    """从新到旧挑选：对话级别行计满 ``limit`` 即止，途中遇到的非对话级行按 ``attach`` 判定随窗附带；返回时间正序

    :param attach: 非对话级行是否附带（如状态事件附带、空内容消息丢弃）；None＝全部附带
    """
    picked: list[Any] = []
    count = 0
    for row in rows_newest_first:
        if count >= limit:
            break
        if is_dialogue_grade(row):
            count += 1
            picked.append(row)
        elif attach is None or attach(row):
            picked.append(row)
    picked.reverse()
    return picked


def merge_consecutive(
    messages: Sequence[dict[str, Any]], *, roles: tuple[str, ...] = ("user", "assistant"), sep: str = "\n\n"
) -> list[dict[str, Any]]:
    """连续同角色的 ``roles`` 消息合并为一条（其余角色不合并），对严格交替的后端也稳"""
    merged: list[dict[str, Any]] = []
    for msg in messages:
        prev = merged[-1] if merged else None
        if prev is not None and msg["role"] in roles and prev["role"] == msg["role"]:
            prev["content"] = f"{prev['content']}{sep}{msg['content']}"
            continue
        merged.append(dict(msg))
    return merged


@dataclass(frozen=True)
class Projection:
    """投影规则（应用给值）

    :param message_roles: 直接作为模型消息的角色（有正文才进）
    :param event_of: 事件体提取；None＝没有事件行
    :param project_event: 事件投影；None＝事件行一律丢弃
    :param event_skipper: 跨行判定构造
    :param rewrite: 消息正文改写
    :param notes: 消息之后附带的注记
    :param merge: 是否合并连续同角色
    """

    message_roles: tuple[str, ...] = ("user", "assistant")
    event_of: EventOf | None = None
    project_event: ProjectEvent | None = None
    event_skipper: EventSkipper | None = None
    rewrite: Rewrite | None = None
    notes: Notes | None = None
    merge: bool = True

    def project(self, rows: Sequence[Any]) -> list[dict[str, Any]]:
        """日志行（时间正序）→ 模型上下文消息列表"""
        skip = self.event_skipper(rows) if self.event_skipper else None
        out: list[dict[str, Any]] = []
        for row in rows:
            if is_shadowed(row):
                continue
            role = getattr(row, "role", None)
            content = getattr(row, "content", None)
            metadata = getattr(row, "metadata", None)
            metadata = metadata if isinstance(metadata, dict) else None
            evt = self.event_of(row) if self.event_of else None
            if evt is not None:
                if skip is not None and skip(evt):
                    continue
                msg = self.project_event(evt, content) if self.project_event else None
                if msg is not None:
                    out.append(msg)
                continue
            if role not in self.message_roles or not content:
                continue
            text = str(content)
            if self.rewrite:
                text = self.rewrite(role, text, metadata)
            out.append({"role": role, "content": text})
            if self.notes:
                out.extend(dict(n) for n in self.notes(role, metadata))
        return merge_consecutive(out) if self.merge else out


__all__ = [
    "EventOf",
    "EventSkipper",
    "IsDialogueGrade",
    "Notes",
    "ProjectEvent",
    "Projection",
    "Rewrite",
    "is_shadowed",
    "merge_consecutive",
    "select_recent",
]
