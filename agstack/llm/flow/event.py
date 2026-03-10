#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""AG-UI 事件构造 — 具名构造函数，snake_case 参数，camelCase 输出"""

from enum import StrEnum
from typing import Any
from uuid import uuid4


class EventType(StrEnum):
    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    CUSTOM = "CUSTOM"


# ── 内部工具 ──


def _to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _ev(type: EventType, **kwargs: Any) -> dict[str, Any]:
    return {"type": type, **{_to_camel(k): v for k, v in kwargs.items()}}


# ── Text Message ──


def text_message_start(*, message_id: str, role: str = "assistant") -> dict[str, Any]:
    return _ev(EventType.TEXT_MESSAGE_START, message_id=message_id, role=role)


def text_message_content(*, message_id: str, delta: str) -> dict[str, Any]:
    return _ev(EventType.TEXT_MESSAGE_CONTENT, message_id=message_id, delta=delta)


def text_message_end(*, message_id: str) -> dict[str, Any]:
    return _ev(EventType.TEXT_MESSAGE_END, message_id=message_id)


# ── Tool Call ──


def tool_call_start(*, tool_call_id: str, tool_call_name: str) -> dict[str, Any]:
    return _ev(EventType.TOOL_CALL_START, tool_call_id=tool_call_id, tool_call_name=tool_call_name)


def tool_call_args(*, tool_call_id: str, delta: str) -> dict[str, Any]:
    return _ev(EventType.TOOL_CALL_ARGS, tool_call_id=tool_call_id, delta=delta)


def tool_call_end(*, tool_call_id: str) -> dict[str, Any]:
    return _ev(EventType.TOOL_CALL_END, tool_call_id=tool_call_id)


def tool_call_result(*, tool_call_id: str, content: str, message_id: str | None = None) -> dict[str, Any]:
    return _ev(
        EventType.TOOL_CALL_RESULT,
        message_id=message_id or str(uuid4()),
        tool_call_id=tool_call_id,
        content=content,
    )


# ── Run lifecycle ──


def run_started(*, thread_id: str, run_id: str) -> dict[str, Any]:
    return _ev(EventType.RUN_STARTED, thread_id=thread_id, run_id=run_id)


def run_finished(*, thread_id: str, run_id: str) -> dict[str, Any]:
    return _ev(EventType.RUN_FINISHED, thread_id=thread_id, run_id=run_id)


def run_error(*, message: str, code: str | None = None) -> dict[str, Any]:
    d = _ev(EventType.RUN_ERROR, message=message)
    if code is not None:
        d["code"] = code
    return d


# ── Step ──


def step_started(*, step_name: str) -> dict[str, Any]:
    return _ev(EventType.STEP_STARTED, step_name=step_name)


def step_finished(*, step_name: str) -> dict[str, Any]:
    return _ev(EventType.STEP_FINISHED, step_name=step_name)


# ── State ──


def state_snapshot(*, snapshot: dict[str, Any]) -> dict[str, Any]:
    return _ev(EventType.STATE_SNAPSHOT, snapshot=snapshot)


def state_delta(*, delta: list[Any]) -> dict[str, Any]:
    return _ev(EventType.STATE_DELTA, delta=delta)


# ── Custom ──


def custom(*, name: str, value: Any) -> dict[str, Any]:
    return _ev(EventType.CUSTOM, name=name, value=value)
