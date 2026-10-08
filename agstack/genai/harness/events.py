#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""执行事件：进程内发布 / 订阅 + 单调序号 + 快照 + 重放（EventHub），以及「flow 事件 → 用户可见事件」的过滤规则表

应用把 flow / Agent 吐出的 AG-UI 事件经 :func:`filter_user_event` 过滤后交 :class:`EventHub.publish`：
每条事件得到任务内单调序号、经应用注入的 ``persist`` 落库、更新内存快照（页面刷新恢复）、推给实时订阅者；
SSE 重连时 :meth:`EventHub.stream` 先经 ``replay`` 重放已落库事件再接实时队列。

持久化不走端口：``persist`` / ``replay`` / ``is_finished`` 是应用在构造 hub 时给的三个协程，表与模型由应用定；
hub 的订阅者 / 快照 / 序号是**进程级**状态（类属性），同一进程内任意实例共享——与「每个请求 new 一个 hub」的
用法兼容，跨进程的实时推送不在此解决。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ..flow.event import EventType


#: 结束一次任务事件流的事件类型
TERMINAL_EVENT_TYPES = frozenset({EventType.RUN_FINISHED.value, EventType.RUN_ERROR.value})

type Persist = Callable[[UUID, int, dict[str, Any]], Awaitable[None]]
type Replay = Callable[[UUID, int], Awaitable[Sequence[dict[str, Any]]]]
type IsFinished = Callable[[UUID], Awaitable[bool]]
type ToolLabel = Callable[[str], str | None]


@dataclass
class TaskSnapshot:
    """前端 UI 状态的服务端镜像（内存中维护，页面刷新时一次性下发）"""

    message_id: str = ""
    text_content: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    custom_events: list[dict[str, Any]] = field(default_factory=list)
    last_sequence: int = 0
    status: str = "running"

    def apply(self, evt: dict[str, Any], seq: int) -> None:
        self.last_sequence = seq
        etype = evt.get("type")
        if etype == EventType.TEXT_MESSAGE_START:
            self.message_id = evt.get("messageId", "")
        elif etype == EventType.TEXT_MESSAGE_CONTENT:
            self.text_content += evt.get("delta", "")
        elif etype == EventType.STEP_STARTED:
            label = evt.get("stepName")
            if label:
                self.steps.append({"label": label, "status": "started"})
        elif etype == EventType.STEP_FINISHED:
            label = evt.get("stepName")
            for s in reversed(self.steps):
                if s.get("label") == label and s["status"] == "started":
                    s["status"] = "finished"
                    break
        elif etype == EventType.CUSTOM:
            self.custom_events.append({"name": evt.get("name"), "value": evt.get("value")})
        elif etype in TERMINAL_EVENT_TYPES:
            self.status = "completed" if etype == EventType.RUN_FINISHED else "failed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "text_content": self.text_content,
            "steps": self.steps,
            "custom_events": self.custom_events,
            "last_sequence": self.last_sequence,
            "status": self.status,
        }


class EventHub:
    """任务事件的进程内枢纽

    :param persist: ``(task_id, seq, event) -> None`` 落库；None 为不落库（仅实时）
    :param replay: ``(task_id, after_seq) -> events`` 重放已落库事件（序号 ≥ after_seq，升序）
    :param is_finished: ``(task_id) -> bool`` 重放完毕后判断任务是否已结束（避免永远等不到终止事件）
    """

    _subscribers: dict[UUID, list[asyncio.Queue[dict[str, Any]]]] = {}  # noqa: RUF012
    _snapshots: dict[UUID, TaskSnapshot] = {}  # noqa: RUF012
    _sequences: dict[UUID, int] = {}  # noqa: RUF012

    def __init__(
        self, *, persist: Persist | None = None, replay: Replay | None = None, is_finished: IsFinished | None = None
    ) -> None:
        self._persist = persist
        self._replay = replay
        self._is_finished = is_finished

    # ── 订阅 ──

    def subscribe(self, task_id: UUID) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.setdefault(task_id, []).append(queue)
        return queue

    def unsubscribe(self, task_id: UUID, queue: asyncio.Queue[dict[str, Any]]) -> None:
        if task_id in self._subscribers:
            self._subscribers[task_id] = [q for q in self._subscribers[task_id] if q is not queue]
            if not self._subscribers[task_id]:
                del self._subscribers[task_id]

    def notify(self, task_id: UUID, evt: dict[str, Any]) -> None:
        """仅推给订阅者（不落库、不计序号）"""
        for queue in self._subscribers.get(task_id, []):
            queue.put_nowait(evt)

    # ── 生命周期 ──

    def begin(self, task_id: UUID) -> None:
        """任务开始：序号归零、建快照"""
        self._sequences[task_id] = 0
        self._snapshots[task_id] = TaskSnapshot()

    def end(self, task_id: UUID) -> None:
        """任务结束：丢快照（序号保留到进程结束无妨）"""
        self._snapshots.pop(task_id, None)

    def resume(self, task_id: UUID, next_seq: int) -> None:
        """本进程未开始过的任务续上序号（崩溃接管 / 跨进程补写；3.1）

        只在本进程没有该任务计数时生效：已在跑的任务不被外部读到的旧值倒拨。不建快照（接管不是在跑）。
        """
        if task_id not in self._sequences:
            self._sequences[task_id] = max(int(next_seq), 0)

    def next_sequence(self, task_id: UUID) -> int | None:
        """本进程将分配给该任务的下一个序号；本进程没见过该任务为 None"""
        return self._sequences.get(task_id)

    def snapshot(self, task_id: UUID) -> TaskSnapshot | None:
        return self._snapshots.get(task_id)

    # ── 发布 ──

    async def publish(self, task_id: UUID, evt: dict[str, Any], *, notify: bool = True) -> int:
        """分配序号 → 落库 → 更新快照 → 通知订阅者；返回该事件的序号

        未带 ``_ts`` 的事件补毫秒时间戳。
        """
        seq = self._sequences.get(task_id, 0)
        self._sequences[task_id] = seq + 1
        if "_ts" not in evt:
            evt = {**evt, "_ts": int(time.time() * 1000)}
        if self._persist is not None:
            await self._persist(task_id, seq, evt)
        snapshot = self._snapshots.get(task_id)
        if snapshot is not None:
            snapshot.apply(evt, seq)
        if notify:
            self.notify(task_id, evt)
        return seq

    # ── 重放 + 实时 ──

    async def stream(self, task_id: UUID, after_seq: int = 0) -> AsyncIterator[dict[str, Any]]:
        """重放已落库事件，再实时等待新事件；遇终止事件或任务已结束即止"""
        queue = self.subscribe(task_id)
        try:
            if self._replay is not None:
                for evt in await self._replay(task_id, after_seq):
                    yield evt
                    if evt.get("type") in TERMINAL_EVENT_TYPES:
                        return
            if self._is_finished is not None and await self._is_finished(task_id):
                return
            while True:
                evt = await queue.get()
                yield evt
                if evt.get("type") in TERMINAL_EVENT_TYPES:
                    return
        finally:
            self.unsubscribe(task_id, queue)


# ── 过滤规则表 ──


def _strip_private(evt: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in evt.items() if not k.startswith("_")}
    out["type"] = evt["type"]
    return out


def filter_user_event(evt: dict[str, Any], *, tool_label: ToolLabel | None = None) -> dict[str, Any] | None:
    """flow / Agent 事件 → 用户可见事件；None 为不转发

    规则（与 flow 节点的 ``_echo`` / ``_label`` 约定对应）：

    - TEXT_MESSAGE_*：仅 ``_echo`` 的节点转发，去掉 ``_`` 前缀字段；
    - STEP_STARTED / STEP_FINISHED：有 ``_label`` 才转发，``stepName`` 取 label；
    - TOOL_CALL_START：有 ``_label`` 才转发，``toolCallName`` 优先取 ``tool_label(原名)``，否则 label；
    - TOOL_CALL_END：有 ``_label`` 才转发；
    - CUSTOM / RUN_FINISHED / RUN_ERROR：原样转发；
    - 其余（TOOL_CALL_ARGS / TOOL_CALL_RESULT / STATE_*）不转发，由 trace 记录。
    """
    etype = evt.get("type", "")
    if etype in (EventType.TEXT_MESSAGE_START, EventType.TEXT_MESSAGE_CONTENT, EventType.TEXT_MESSAGE_END):
        return _strip_private(evt) if evt.get("_echo") else None
    if etype in (EventType.STEP_STARTED, EventType.STEP_FINISHED):
        label = evt.get("_label")
        return {"type": etype, "stepId": evt.get("stepId", ""), "stepName": label} if label else None
    if etype == EventType.TOOL_CALL_START:
        label = evt.get("_label")
        if not label:
            return None
        raw_name = evt.get("toolCallName", "")
        display = (tool_label(raw_name) if tool_label and raw_name else None) or label
        return {"type": etype, "toolCallId": evt.get("toolCallId", ""), "toolCallName": display}
    if etype == EventType.TOOL_CALL_END:
        return {"type": etype, "toolCallId": evt.get("toolCallId", "")} if evt.get("_label") else None
    if etype in (EventType.CUSTOM, EventType.RUN_FINISHED, EventType.RUN_ERROR):
        return evt
    return None


__all__ = [
    "TERMINAL_EVENT_TYPES",
    "EventHub",
    "IsFinished",
    "Persist",
    "Replay",
    "TaskSnapshot",
    "ToolLabel",
    "filter_user_event",
]
