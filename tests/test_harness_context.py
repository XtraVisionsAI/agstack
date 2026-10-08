#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""agstack.genai.harness.context（3.0）：压缩引擎验收用例——内存 HistorySource / SummaryStore，token 以字符数计"""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from agstack.genai.harness import context as hc
from agstack.genai.harness.context import (
    ContextEngine,
    ContextPolicy,
    SummaryRecord,
    anchored_history_tokens,
    fill_to_budget,
    messages_tokens,
    schedule_once,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _row(role: str, content: str, rid: int):
    return SimpleNamespace(id=rid, role=role, content=content)


def _rows(n: int, start: int = 0):
    return [_row("user" if i % 2 == 0 else "assistant", f"m{i}", start + i) for i in range(n)]


def _project(rows):
    return [{"role": r.role, "content": r.content} for r in rows]


@dataclass
class _Source:
    rows: list[Any]
    fetch_factor: int = 3

    async def recent(self, limit: int):
        newest_first = list(reversed(self.rows))[: limit * self.fetch_factor]
        return list(reversed(newest_first[:limit])), newest_first

    async def after(self, ref):
        ids = [r.id for r in self.rows]
        return self.rows[ids.index(ref) + 1 :] if ref in ids else []

    async def all_rows(self):
        return list(self.rows)

    async def count(self):
        return len(self.rows)

    def project(self, rows):
        return _project(rows)

    def ref_of(self, row):
        return row.id


@dataclass
class _Store:
    record: SummaryRecord | None = None
    saved: list[SummaryRecord] = field(default_factory=list)

    async def latest(self):
        return self.record

    async def save(self, record):
        self.saved.append(record)
        self.record = record


def _summarizer(calls: list, *, content: str | None = "S"):
    async def summarize(messages, previous):
        calls.append((len(messages), previous))
        return content

    return summarize


def _engine(source, store, *, window=1000, calls=None, anchor=None, policy=None, key="t"):
    return ContextEngine(
        source=source,
        summaries=store,
        summarize=_summarizer(calls if calls is not None else []),
        count=len,
        context_length=window,
        model="m",
        anchor=anchor,
        policy=policy or ContextPolicy(),
        session_key=key,
    )


def test_fill_to_budget_prefers_user_messages_and_keeps_order():
    msgs = [
        {"role": "user", "content": "u1" * 5},
        {"role": "assistant", "content": "a1" * 20},
        {"role": "user", "content": "u2" * 5},
        {"role": "assistant", "content": "a2" * 20},
    ]
    picked = fill_to_budget(msgs, 40, len)  # user 预算 24：两条 user 各 14 → 只装最新一条；剩余装不下任何 assistant
    assert [m["content"] for m in picked] == ["u2" * 5]
    picked = fill_to_budget(msgs, 200, len)
    assert [m["content"] for m in picked] == [m["content"] for m in msgs]
    assert fill_to_budget(msgs, 0, len) == []
    assert messages_tokens(msgs, len) == sum(len(m["content"]) + 4 for m in msgs)


def test_anchored_history_tokens_paths():
    rows = _rows(4)
    tail = _rows(2, start=4)
    window = rows + tail
    fetched = list(reversed(window))
    fallback = messages_tokens(_project(window), len)

    def tokens(anchor, selected=window, fetched_newest_first=fetched):
        return anchored_history_tokens(
            anchor=anchor,
            model="m",
            selected=selected,
            fetched_newest_first=fetched_newest_first,
            ref_of=lambda r: r.id,
            project=_project,
            count=len,
            fallback=messages_tokens(_project(selected), len),
        )

    anchor = {"first_id": rows[0].id, "last_id": rows[-1].id, "history_tokens": 100, "model": "m"}
    assert tokens(anchor) == 100 + 12  # 尾部两条各 2+4
    assert tokens({**anchor, "model": "other"}) == fallback
    assert tokens({**anchor, "last_id": 99}) == fallback
    assert tokens({**anchor, "first_id": rows[1].id}) == fallback  # 锚点覆盖不全
    slid = window[2:]
    assert tokens(anchor, selected=slid) == 100  # 滑出两条 −12，尾部 +12
    assert tokens(anchor, selected=slid, fetched_newest_first=list(reversed(slid))) == messages_tokens(
        _project(slid), len
    )
    assert tokens(None) == fallback


def test_fast_path_records_span():
    source, store = _Source(_rows(4)), _Store()
    engine = _engine(source, store)
    history = _run(engine.build_history(reserved_tokens=0))
    assert [m["content"] for m in history] == ["m0", "m1", "m2", "m3"]
    assert engine.history_span == {"first_id": "0", "last_id": "3"}
    assert _run(_engine(source, store, window=1).build_history()) == []


def test_budget_shortage_without_summary_fills_and_schedules_refresh():
    scheduled: list = []
    policy = ContextPolicy(summary_min_messages=4, schedule=lambda key, factory: scheduled.append(key))
    source, store = _Source(_rows(6)), _Store()
    engine = _engine(source, store, window=30, policy=policy)  # 预算 22
    history = _run(engine.build_history())
    assert engine.history_span is None and history and len(history) < 6
    assert scheduled == ["t"]
    # 消息数不足阈值：不投递
    scheduled.clear()
    engine = _engine(_Source(_rows(3)), store, window=12, policy=policy)
    _run(engine.build_history())
    assert scheduled == []


def test_summary_consumed_with_tail_and_stale_summary_schedules_incremental():
    scheduled: list = []
    policy = ContextPolicy(
        incremental_threshold=2, render=lambda c: f"R({c})", schedule=lambda k, f: scheduled.append(k)
    )
    rows = _rows(8)
    source = _Source(rows)
    store = _Store(SummaryRecord(content="sum", start_ref=0, end_ref=3, message_count=4))
    engine = _engine(source, store, window=60, policy=policy)  # 预算 45：8 条 × 6 = 48 超预算
    history = _run(engine.build_history())
    assert history[0] == {"role": "system", "content": "[对话历史摘要]\nR(sum)"}
    assert [m["content"] for m in history[1:]] == ["m4", "m5", "m6", "m7"]
    assert scheduled == ["t"]  # 摘要之后 4 条新消息 ≥ 2 → 投递增量刷新
    # 摘要本身超预算 → 只回摘要
    tight = _engine(source, store, window=12, policy=policy)
    assert len(_run(tight.build_history())) == 1


def test_refresh_summary_full_and_incremental_coverage():
    calls: list = []
    policy = ContextPolicy(summary_min_messages=6, incremental_threshold=3, keep_recent=2)
    source, store = _Source(_rows(8)), _Store()
    engine = _engine(source, store, calls=calls, policy=policy)
    record = _run(engine.refresh_summary())
    assert record is not None and (record.start_ref, record.end_ref, record.message_count) == (0, 5, 6)
    assert calls == [(6, None)]
    # 新增 3 条 → 增量：以旧摘要为底，覆盖点留出最近 2 条
    source.rows.extend(_rows(3, start=8))
    record = _run(engine.refresh_summary())
    assert record is not None and (record.start_ref, record.end_ref, record.message_count) == (0, 8, 11)
    assert calls[-1] == (5, "S")  # 旧摘要 end_ref=5 之后的 5 条
    assert len(store.saved) == 2
    # 新消息不足阈值 → 不刷新；摘要失败 → 不落库
    assert _run(engine.refresh_summary()) is None
    failing = _engine(_Source(_rows(8)), _Store(), policy=policy)
    failing.summarize = _summarizer([], content=None)
    assert _run(failing.refresh_summary()) is None


def test_schedule_once_dedups_per_key():
    async def main():
        ran: list = []

        async def job():
            await asyncio.sleep(0)
            ran.append(1)

        schedule_once("k", job)
        schedule_once("k", job)  # 在途去重
        assert "k" in hc.pending_refreshes()
        await asyncio.gather(*hc.pending_refreshes().values())
        assert ran == [1] and "k" not in hc.pending_refreshes()

    _run(main())
    schedule_once("x", lambda: asyncio.sleep(0))  # 无事件循环：静默放弃
