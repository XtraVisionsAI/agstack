#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""agstack.genai.harness.events / projection / tokens（2.4）验收用例"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from agstack.genai.harness.events import EventHub, TaskSnapshot, filter_user_event
from agstack.genai.harness.metering import (
    CalibratedCounter,
    anchored_estimate,
    calibration_from_samples,
    calibration_sample,
    clamp_ratio,
)
from agstack.genai.harness.ports import TokenAnchor
from agstack.genai.harness.projection import Projection, is_shadowed, merge_consecutive, select_recent


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── events ──


class _Store:
    def __init__(self):
        self.rows: dict = {}

    async def persist(self, task_id, seq, evt):
        self.rows.setdefault(task_id, []).append((seq, evt))

    async def replay(self, task_id, after_seq):
        return [e for s, e in self.rows.get(task_id, []) if s >= after_seq]

    async def is_finished(self, task_id):
        return False


def test_hub_publish_sequences_persists_snapshots_and_notifies():
    store = _Store()
    hub = EventHub(persist=store.persist, replay=store.replay, is_finished=store.is_finished)
    tid = uuid4()
    hub.begin(tid)
    q = hub.subscribe(tid)

    async def go():
        s0 = await hub.publish(tid, {"type": "TEXT_MESSAGE_START", "messageId": "m1"})
        s1 = await hub.publish(tid, {"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"}, notify=False)
        s2 = await hub.publish(tid, {"type": "STEP_STARTED", "stepName": "检索"})
        s3 = await hub.publish(tid, {"type": "STEP_FINISHED", "stepName": "检索"})
        return s0, s1, s2, s3

    assert _run(go()) == (0, 1, 2, 3)
    assert [s for s, _ in store.rows[tid]] == [0, 1, 2, 3] and all("_ts" in e for _, e in store.rows[tid])
    snap = hub.snapshot(tid)
    assert snap is not None and snap.message_id == "m1" and snap.text_content == "hi" and snap.last_sequence == 3
    assert snap.steps == [{"label": "检索", "status": "finished"}]
    assert q.qsize() == 3  # notify=False 的那条不推
    hub.end(tid)
    assert hub.snapshot(tid) is None
    # 进程级共享：另一个实例看到同一订阅者
    EventHub().notify(tid, {"type": "CUSTOM"})
    assert q.qsize() == 4
    hub.unsubscribe(tid, q)


def test_hub_stream_replays_then_waits_live_until_terminal():
    store = _Store()
    hub = EventHub(persist=store.persist, replay=store.replay, is_finished=store.is_finished)
    tid = uuid4()
    hub.begin(tid)

    async def go():
        await hub.publish(tid, {"type": "TEXT_MESSAGE_CONTENT", "delta": "a"})
        await hub.publish(tid, {"type": "TEXT_MESSAGE_CONTENT", "delta": "b"})
        got = []

        async def consume():
            async for evt in hub.stream(tid, after_seq=1):
                got.append(evt["type"] + ":" + str(evt.get("delta", "")))

        task = asyncio.ensure_future(consume())
        await asyncio.sleep(0)
        await hub.publish(tid, {"type": "TEXT_MESSAGE_CONTENT", "delta": "c"})
        await hub.publish(tid, {"type": "RUN_FINISHED"})
        await asyncio.wait_for(task, 1)
        return got

    assert _run(go()) == ["TEXT_MESSAGE_CONTENT:b", "TEXT_MESSAGE_CONTENT:c", "RUN_FINISHED:"]
    assert not EventHub._subscribers.get(tid)


def test_hub_stream_stops_on_replayed_terminal_or_finished_task():
    store = _Store()
    tid = uuid4()
    hub = EventHub(persist=store.persist, replay=store.replay, is_finished=store.is_finished)
    hub.begin(tid)

    async def go():
        await hub.publish(tid, {"type": "RUN_ERROR", "message": "x"})
        return [e["type"] async for e in hub.stream(tid)]

    assert _run(go()) == ["RUN_ERROR"]

    async def finished(_):
        return True

    hub2 = EventHub(replay=store.replay, is_finished=finished)
    tid2 = uuid4()

    async def go2():
        return [e async for e in hub2.stream(tid2)]

    assert _run(go2()) == []


def test_snapshot_terminal_status():
    snap = TaskSnapshot()
    snap.apply({"type": "CUSTOM", "name": "n", "value": 1}, 0)
    snap.apply({"type": "RUN_ERROR"}, 1)
    assert snap.status == "failed" and snap.custom_events == [{"name": "n", "value": 1}]
    assert snap.as_dict()["last_sequence"] == 1


def test_filter_user_event_rules():
    assert filter_user_event({"type": "TEXT_MESSAGE_CONTENT", "delta": "x"}) is None
    assert filter_user_event({"type": "TEXT_MESSAGE_CONTENT", "delta": "x", "_echo": True, "_n": 1}) == {
        "type": "TEXT_MESSAGE_CONTENT",
        "delta": "x",
    }
    assert filter_user_event({"type": "STEP_STARTED", "stepId": "s"}) is None
    assert filter_user_event({"type": "STEP_STARTED", "stepId": "s", "_label": "取材"}) == {
        "type": "STEP_STARTED",
        "stepId": "s",
        "stepName": "取材",
    }
    labels = {"retrieval": "检索知识库"}
    evt = {"type": "TOOL_CALL_START", "toolCallId": "c", "toolCallName": "retrieval", "_label": "工具"}
    shown = filter_user_event(evt, tool_label=labels.get)
    assert shown is not None and shown["toolCallName"] == "检索知识库"
    shown = filter_user_event({**evt, "toolCallName": "other"}, tool_label=labels.get)
    assert shown is not None and shown["toolCallName"] == "工具"
    assert filter_user_event({"type": "TOOL_CALL_END", "toolCallId": "c"}) is None
    assert filter_user_event({"type": "TOOL_CALL_END", "toolCallId": "c", "_label": "x"}) == {
        "type": "TOOL_CALL_END",
        "toolCallId": "c",
    }
    custom = {"type": "CUSTOM", "name": "n", "value": {}}
    assert filter_user_event(custom) is custom
    assert filter_user_event({"type": "TOOL_CALL_ARGS"}) is None and filter_user_event({"type": "RUN_FINISHED"})


# ── projection ──


def _row(role, content=None, metadata=None, shadowed_by=None):
    return SimpleNamespace(role=role, content=content, metadata=metadata, shadowed_by=shadowed_by)


def test_select_recent_counts_only_dialogue_grade():
    rows = [_row("event", "e3"), _row("assistant", "a2"), _row("event", "e2"), _row("user", "u2"), _row("user", "u1")]
    picked = select_recent(rows, 2, lambda r: r.role in ("user", "assistant"))
    assert [r.content for r in picked] == ["u2", "e2", "a2", "e3"]


def test_merge_consecutive_only_listed_roles():
    msgs = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}, {"role": "system", "content": "n"}]
    msgs += [{"role": "system", "content": "m"}]
    merged = merge_consecutive(msgs)
    assert [m["content"] for m in merged] == ["a\n\nb", "n", "m"]


def test_projection_pipeline():
    def event_of(row):
        return row.metadata.get("event") if row.role == "event" and isinstance(row.metadata, dict) else None

    def skipper(rows):
        done = {e["id"] for r in rows if (e := event_of(r)) and e["kind"] == "ready"}
        return lambda evt: evt["kind"] == "launched" and evt["id"] in done

    def project_event(evt, content):
        return {"role": "assistant", "content": content} if evt["kind"] == "ready" else None

    proj = Projection(
        event_of=event_of,
        project_event=project_event,
        event_skipper=skipper,
        rewrite=lambda role, text, meta: f"[摘要:{text}]" if meta and meta.get("draft") else text,
        notes=lambda role, meta: [{"role": "system", "content": "note"}] if meta and meta.get("op") else [],
    )
    rows = [
        _row("user", "q1"),
        _row("assistant", "old", shadowed_by="x"),
        _row("assistant", "a1", {"op": 1}),
        _row("event", "launched x", {"event": {"kind": "launched", "id": "x"}}),
        _row("event", "ready x", {"event": {"kind": "ready", "id": "x"}}),
        _row("assistant", "draft body", {"draft": True}),
        _row("tool", "ignored"),
        _row("user", None),
    ]
    assert proj.project(rows) == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "system", "content": "note"},
        {"role": "assistant", "content": "ready x\n\n[摘要:draft body]"},
    ]
    assert is_shadowed(rows[1]) and not is_shadowed(rows[0])
    assert Projection(merge=False).project([_row("user", "a"), _row("user", "b")]) == [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
    ]


# ── tokens ──


def test_clamp_and_samples():
    assert clamp_ratio(100, 120) == 1.2 and clamp_ratio(100, 10) == 0.8 and clamp_ratio(100, 1000) == 1.5
    assert clamp_ratio(0, 10) == 1.0 and clamp_ratio(10, 0) == 1.0
    samples = [None, {"model": "other", "estimated": 1, "actual": 9}, {"model": "m", "estimated": 100, "actual": 90}]
    assert calibration_from_samples(samples, "m") == 0.9
    assert calibration_from_samples(samples, "none") == 1.0
    assert calibration_from_samples([{"model": "m", "estimated": "x", "actual": 1}], "m") == 1.0
    assert calibration_from_samples([{"model": "m", "estimated": 1, "actual": 100}], "m", bounds=(0.5, 3.0)) == 3.0
    assert calibration_sample(10, 12, "m") == {"estimated": 10, "actual": 12, "model": "m"}
    assert calibration_sample(0, 12, "m") is None


def test_calibrated_counter_and_anchor():
    counter = CalibratedCounter(count_tokens=lambda t, m: len(t), model="m", ratio=1.5)
    assert counter("abcd") == 6
    assert counter.messages([{"content": "ab"}, {"content": None}], overhead_per_message=1) == (3 + 1) + (0 + 1)
    assert anchored_estimate(None, 10, fallback_tokens=99) == 99
    assert anchored_estimate(TokenAnchor(seq=5, prompt_tokens=1000, model="m"), 10, fallback_tokens=99) == 1010
    assert anchored_estimate(TokenAnchor(seq=5, prompt_tokens=1000, model="m"), -3, fallback_tokens=99) == 1000
