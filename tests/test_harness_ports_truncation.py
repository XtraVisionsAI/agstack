#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""agstack.llm.harness：端口注册面与截断设施"""

import pytest

from agstack.llm import client as llm_client
from agstack.llm.harness import LogEvent, clamp_results, clear_ports, get_ports, register_ports, truncate_middle
from agstack.llm.harness.ports import LOG_KINDS, SHADOW_KINDS, SessionLog, SpillStore


@pytest.fixture(autouse=True)
def _isolated():
    clear_ports()
    yield
    clear_ports()


class _Spill:
    async def save_text(self, owner, source, suggested_name, content):
        raise NotImplementedError

    async def read_text(self, locator, *, offset=0, limit=None):
        raise NotImplementedError


class _Log:
    async def append(self, session_id, events):
        return 0

    async def read(self, session_id, *, after_seq=0, limit=None):
        return []

    async def shadow(self, session_id, target_seqs, by_seq, kind):
        return None

    async def latest_anchor(self, session_id):
        return None


def test_register_ports_partial_and_usage_sink_wired_to_client():
    seen = []

    def sink(event):
        seen.append(event)

    register_ports(spill=_Spill(), usage=sink)
    ports = get_ports()
    assert isinstance(ports.spill, SpillStore) and ports.session_log is None
    assert llm_client._usage_callback is sink
    # 只覆盖传入项
    register_ports(session_log=_Log())
    assert isinstance(get_ports().session_log, SessionLog) and get_ports().spill is not None
    clear_ports()
    assert get_ports().spill is None and llm_client._usage_callback is None


def test_log_event_defaults_and_kinds():
    ev = LogEvent(kind="message", role="user", content="hi")
    assert ev.seq is None and ev.shadowed_by is None and ev.metadata == {}
    assert {"message", "tool_result", "summary", "fold", "attempt", "phase_marker"} <= LOG_KINDS
    assert SHADOW_KINDS == {"retry", "overflow_recovery", "fold"}


def _chars(text: str) -> int:
    return len(text)


def test_truncate_middle_keeps_head_tail_with_marker():
    text = "A" * 1000 + "\n" + "B" * 1000
    out = truncate_middle(text, 400, _chars)
    assert out.startswith("A") and out.endswith("B") and "已截断中段" in out and "2001 tokens" in out
    assert truncate_middle("short", 400, _chars) == "short"


def test_clamp_results_truncates_items_then_drops_low_relevance():
    results = [
        {"content": "x" * 300, "relevance_score": 0.9},
        {"content": "y" * 300, "relevance_score": 0.1},
        {"content": "z" * 300, "relevance_score": 0.5},
        {"other": 1},
    ]
    # 三条各截到约 285 字符（225 保留 + 标注），合计约 855；合计上限 700：丢最低相关度的 y 后达标
    out, dropped = clamp_results(results, per_item_max=250, total_max=700, count_tokens=_chars, tool="t")
    assert dropped == 1 and all("y" not in (i.get("content") or "") for i in out if isinstance(i, dict))
    assert all(
        len(i["content"]) < 300 and "已截断中段" in i["content"] for i in out if isinstance(i, dict) and "content" in i
    )
    assert {"other": 1} in out
    assert clamp_results([], per_item_max=1, total_max=1, count_tokens=_chars) == ([], 0)
