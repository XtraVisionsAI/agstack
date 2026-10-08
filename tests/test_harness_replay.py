#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""日志回放重建与读方修复（harness.replay，3.1）+ EventHub 续号 + 3.1 端口签名

- recover：最后一个 phase_marker 决定 phase / state / after；tool_call 无 tool_result 为孤儿（started）；
  pending_tool_calls 里日志没开始记录的为孤儿（unrecorded）；RUN_FINISHED / RUN_ERROR 视为已闭合；
- closers：每孤儿一条合成 tool_result 错误（文案按形态区分）+ 一条 RUN_ERROR；已闭合返回空；
- EventHub.resume 只在本进程没见过该任务时生效；
- SessionLog 端口不再有 shadow，TokenAnchor 以 anchor_ref 定位。
"""

import json
from uuid import uuid4

from agstack.genai.harness import (
    ORPHAN_STARTED,
    ORPHAN_UNRECORDED,
    EventHub,
    LogEvent,
    RecoveryState,
    TokenAnchor,
    attempt,
    closers,
    is_synthetic,
    phase_marker,
    recover,
    tool_call,
    tool_result,
)
from agstack.genai.harness.ports import SessionLog


def _seq(events: list[LogEvent]) -> list[LogEvent]:
    for i, ev in enumerate(events):
        ev.seq = i
    return events


def test_recover_reads_last_marker_and_orphans():
    events = _seq(
        [
            LogEvent(kind="event", metadata={"type": "STEP_STARTED", "stepName": "取材"}),
            phase_marker("flow", {"flow_name": "report"}),
            tool_call("c1", "retrieval", {"q": "x"}),
            tool_result("c1", "ok"),
            phase_marker("render", {"flow_name": "report", "title": "t"}),
            tool_call("c2", "web_fetch"),
            attempt("overflow"),
        ]
    )
    state = recover(events, pending_tool_calls=["c2", "c3"])
    assert state.phase == "render" and state.state == {"flow_name": "report", "title": "t"} and state.marker_seq == 4
    assert state.last_seq == 6 and [e.kind for e in state.after] == ["tool_call", "attempt"]
    assert [(o.call_id, o.kind, o.seq) for o in state.orphan_tool_calls] == [
        ("c2", ORPHAN_STARTED, 5),
        ("c3", ORPHAN_UNRECORDED, None),
    ]
    assert state.attempts == 1 and not state.closed and state.needs_repair


def test_recover_without_marker_and_closed_runs():
    finished = LogEvent(kind="event", metadata={"type": "RUN_FINISHED"})
    events = _seq([tool_call("c1", "t"), tool_result("c1", "r"), finished])
    state = recover(events)
    assert state.phase is None and state.marker_seq is None and len(state.after) == 3
    assert state.orphan_tool_calls == () and state.closed and not state.needs_repair
    assert closers(state) == []
    # 乱序输入按 seq 整理；空日志
    shuffled = [events[2], events[0], events[1]]
    assert recover(shuffled).last_seq == 2
    empty = recover([])
    assert empty == RecoveryState() and empty.last_seq == -1


def test_closers_synthesize_tool_errors_and_run_error():
    events = _seq([phase_marker("flow"), tool_call("c1", "retrieval")])
    state = recover(events, pending_tool_calls=["c9"])
    out = closers(state, reason="启动接管")
    assert [e.kind for e in out] == ["tool_result", "tool_result", "event"]
    first = json.loads(out[0].content or "")
    assert first["orphan"] == ORPHAN_STARTED and "可能已执行" in first["error"]
    assert out[0].metadata["call_id"] == "c1" and out[0].metadata["error"] is True and is_synthetic(out[0])
    second = json.loads(out[1].content or "")
    assert second["orphan"] == ORPHAN_UNRECORDED and "可安全重试" in second["error"]
    tail = out[-1].metadata
    assert tail["type"] == "RUN_ERROR" and tail["code"] == "Interrupted" and tail["message"] == "启动接管"
    assert tail["phase"] == "flow" and tail["orphan_tool_calls"] == 2 and is_synthetic(out[-1])
    # 追加闭合事件后再读：已闭合、无孤儿
    again = recover(events + closers(state))
    assert again.closed and again.orphan_tool_calls == ()


def test_event_hub_resume_only_for_unknown_tasks():
    hub = EventHub()
    task = uuid4()
    assert hub.next_sequence(task) is None
    hub.resume(task, 7)
    assert hub.next_sequence(task) == 7
    hub.resume(task, 2)  # 已有计数不倒拨
    assert hub.next_sequence(task) == 7
    running = uuid4()
    hub.begin(running)
    hub.resume(running, 99)
    assert hub.next_sequence(running) == 0


def test_ports_31_signatures():
    class _Log:
        async def append(self, session_id, events):
            return 0

        async def read(self, session_id, *, after_seq=-1, limit=None):
            return []

        async def latest_anchor(self, session_id):
            return None

    assert isinstance(_Log(), SessionLog)
    assert not hasattr(SessionLog, "shadow")
    anchor = TokenAnchor(anchor_ref="msg-1", prompt_tokens=120, model="m")
    assert anchor.anchor_ref == "msg-1"
    ev = LogEvent(kind="message", shadowed_by="some-uuid")
    assert ev.shadowed_by == "some-uuid"
