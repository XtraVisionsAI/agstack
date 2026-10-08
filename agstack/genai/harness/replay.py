#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""日志回放重建与崩溃接管的读方修复（3.1）

F1「FlowContext 序列化」换思路：不序列化活对象，序列化产生它的事件——长作业在阶段边界写 :func:`phase_marker`
（阶段名 + 可 JSON 的恢复状态），失败的尝试写 :func:`attempt`（留痕、不进模型历史）。进程崩溃后，读方从该运行的
日志（``SessionLog.read``）用 :func:`recover` 得到 :class:`RecoveryState`：最后一个阶段标记及其状态、标记之后的事件、
以及「已记录开始但没有结果」的孤儿工具调用；再用 :func:`closers` 合成闭合事件（每个孤儿一条 ``tool_result`` 错误、
末尾一条 ``RUN_ERROR``）追加回日志，运行即以可解释的终态收口。

借 dsh 的读方修复纪律：**写方不截断半途运行、不回写**，修复只发生在读方且以普通事件追加；合成的工具错误文案区分
「已记录开始无结果（工具可能已执行，按幂等性决定是否重试）」与「未记录开始（工具未执行，可安全重试）」——后者由
应用从检查点状态里给出待执行的 call id（``pending_tool_calls``）。本模块只做机制：阅读与合成都是纯函数，
存取经端口由应用完成。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .ports import LogEvent


#: 孤儿工具调用的两种形态
ORPHAN_STARTED = "started"  # 已记录开始、无结果
ORPHAN_UNRECORDED = "unrecorded"  # 检查点里待执行、日志里没有开始记录

#: 合成事件的标记键（metadata）
SYNTHETIC_FLAG = "synthetic"

#: 合成工具错误的缺省文案
MESSAGE_STARTED = "进程中断：该工具调用已记录开始但没有结果，工具可能已执行；请按其幂等性决定是否重试。"
MESSAGE_UNRECORDED = "进程中断：该工具调用未记录开始，工具未执行，可安全重试。"
#: 合成终止事件的缺省原因
REASON_INTERRUPTED = "进程中断，运行未完成"


def phase_marker(phase: str, state: Mapping[str, Any] | None = None) -> LogEvent:
    """阶段标记：进入阶段 ``phase``，``state`` 为从该阶段继续所需的可 JSON 状态（应用自定）"""
    return LogEvent(kind="phase_marker", metadata={"phase": phase, "state": dict(state or {})})


def attempt(reason: str, *, detail: Mapping[str, Any] | None = None, content: str | None = None) -> LogEvent:
    """失败的尝试（重试前的那一次 / 被取消的半截输出）：留痕、不进模型历史"""
    return LogEvent(kind="attempt", content=content, metadata={"reason": reason, **dict(detail or {})})


def tool_call(call_id: str, name: str, arguments: Mapping[str, Any] | None = None) -> LogEvent:
    """工具调用开始（应用把自己的事件行映射为此形态供 :func:`recover` 阅读）"""
    return LogEvent(kind="tool_call", metadata={"call_id": call_id, "name": name, "arguments": dict(arguments or {})})


def tool_result(call_id: str, content: str, *, error: bool = False, **extra: Any) -> LogEvent:
    """工具调用结果"""
    metadata: dict[str, Any] = {"call_id": call_id, "error": error, **extra}
    return LogEvent(kind="tool_result", role="tool", content=content, metadata=metadata)


@dataclass(frozen=True, slots=True)
class OrphanCall:
    """没有结果的工具调用"""

    call_id: str
    name: str | None
    seq: int | None
    kind: str  # ORPHAN_STARTED / ORPHAN_UNRECORDED


@dataclass(frozen=True, slots=True)
class RecoveryState:
    """一次运行的日志阅读结果

    :param phase: 最后一个阶段标记的阶段名（无标记为 None）
    :param state: 该标记携带的恢复状态
    :param marker_seq: 该标记的序号
    :param last_seq: 日志末 seq（空日志为 -1）
    :param after: 标记之后的事件（无标记即全部）
    :param orphan_tool_calls: 孤儿工具调用
    :param attempts: 日志里的失败尝试条数
    :param closed: 日志是否已有终止事件（``RUN_FINISHED`` / ``RUN_ERROR``），已闭合的运行不需要修复
    """

    phase: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    marker_seq: int | None = None
    last_seq: int = -1
    after: tuple[LogEvent, ...] = ()
    orphan_tool_calls: tuple[OrphanCall, ...] = ()
    attempts: int = 0
    closed: bool = False

    @property
    def needs_repair(self) -> bool:
        """未闭合即需要读方修复（有无孤儿都要补终止事件）"""
        return not self.closed


def _event_type(ev: LogEvent) -> str:
    return str(ev.metadata.get("type") or "")


def _ordered(events: Iterable[LogEvent]) -> list[LogEvent]:
    """全部带 seq 时按 seq 排序；有未分配序号的事件（如刚合成未落库的闭合事件）则信任给定顺序"""
    items = list(events)
    if all(e.seq is not None for e in items):
        items.sort(key=lambda e: e.seq or 0)
    return items


def recover(events: Iterable[LogEvent], *, pending_tool_calls: Iterable[str] = ()) -> RecoveryState:
    """阅读一次运行的日志（升序或任意序，按 seq 整理）

    :param pending_tool_calls: 应用从检查点状态得知「应当执行」的 call id；日志里没有开始记录的记为
        :data:`ORPHAN_UNRECORDED`
    """
    ordered = _ordered(events)
    last_seq = max((e.seq for e in ordered if e.seq is not None), default=-1)
    marker_index: int | None = None
    for i, ev in enumerate(ordered):
        if ev.kind == "phase_marker":
            marker_index = i
    phase: str | None = None
    state: dict[str, Any] = {}
    marker_seq: int | None = None
    if marker_index is not None:
        marker = ordered[marker_index]
        phase = str(marker.metadata.get("phase") or "") or None
        raw_state = marker.metadata.get("state")
        state = dict(raw_state) if isinstance(raw_state, Mapping) else {}
        marker_seq = marker.seq
    after = tuple(ordered[marker_index + 1 :]) if marker_index is not None else tuple(ordered)

    started: dict[str, OrphanCall] = {}
    for ev in ordered:
        if ev.kind == "tool_call":
            call_id = str(ev.metadata.get("call_id") or "")
            if call_id:
                name = ev.metadata.get("name")
                started[call_id] = OrphanCall(call_id, str(name) if name else None, ev.seq, ORPHAN_STARTED)
        elif ev.kind == "tool_result":
            started.pop(str(ev.metadata.get("call_id") or ""), None)
    orphans = list(started.values())
    for call_id in pending_tool_calls:
        if call_id and call_id not in started and not any(o.call_id == call_id for o in orphans):
            orphans.append(OrphanCall(str(call_id), None, None, ORPHAN_UNRECORDED))
    orphans.sort(key=lambda o: (o.seq is None, o.seq if o.seq is not None else 0))

    closed = any(ev.kind == "event" and _event_type(ev) in ("RUN_FINISHED", "RUN_ERROR") for ev in ordered)
    attempts = sum(1 for ev in ordered if ev.kind == "attempt")
    return RecoveryState(
        phase=phase,
        state=state,
        marker_seq=marker_seq,
        last_seq=last_seq,
        after=after,
        orphan_tool_calls=tuple(orphans),
        attempts=attempts,
        closed=closed,
    )


def closers(
    state: RecoveryState,
    *,
    reason: str = REASON_INTERRUPTED,
    message_started: str = MESSAGE_STARTED,
    message_unrecorded: str = MESSAGE_UNRECORDED,
    code: str = "Interrupted",
) -> list[LogEvent]:
    """合成闭合事件：每个孤儿一条 ``tool_result`` 错误（文案按形态区分）+ 一条 ``RUN_ERROR``；已闭合的运行返回空"""
    if state.closed:
        return []
    out: list[LogEvent] = []
    for orphan in state.orphan_tool_calls:
        message = message_unrecorded if orphan.kind == ORPHAN_UNRECORDED else message_started
        out.append(
            tool_result(
                orphan.call_id,
                json.dumps({"error": message, "orphan": orphan.kind}, ensure_ascii=False),
                error=True,
                name=orphan.name,
                orphan=orphan.kind,
                **{SYNTHETIC_FLAG: True},
            )
        )
    out.append(
        LogEvent(
            kind="event",
            metadata={
                "type": "RUN_ERROR",
                "code": code,
                "message": reason,
                "phase": state.phase,
                "orphan_tool_calls": len(state.orphan_tool_calls),
                SYNTHETIC_FLAG: True,
            },
        )
    )
    return out


def is_synthetic(ev: LogEvent) -> bool:
    """是否读方合成的事件"""
    return bool(ev.metadata.get(SYNTHETIC_FLAG))


__all__ = [
    "MESSAGE_STARTED",
    "MESSAGE_UNRECORDED",
    "ORPHAN_STARTED",
    "ORPHAN_UNRECORDED",
    "REASON_INTERRUPTED",
    "SYNTHETIC_FLAG",
    "OrphanCall",
    "RecoveryState",
    "attempt",
    "closers",
    "is_synthetic",
    "phase_marker",
    "recover",
    "tool_call",
    "tool_result",
]
