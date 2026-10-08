#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""AgentGuards（agstack.genai.flow.guards）验收用例

机制在库、策略在应用：受上限工具族 / 自定义规则 / 折叠渲染 / 提示 / 文案全由 AgentGuards 给；
守卫动作写与 Tool 管线同形的执行记录；buffer_plan_text 把调工具前的过程话语记为计划不放流。
"""

from dataclasses import replace
from unittest.mock import patch

import pytest

from agstack.genai.flow.agent import Agent
from agstack.genai.flow.context import FlowContext
from agstack.genai.flow.event import EventType
from agstack.genai.flow.guards import (
    GUARD_DUPLICATE,
    PLAN_RECORD,
    AgentGuards,
    GuardedToolCalls,
    GuardState,
    buffer_plan_text,
    call_signature,
    check_tool_call,
    fold_tool_messages,
    note_tool_call,
)
from agstack.genai.flow.registry import registry
from agstack.genai.flow.tool import Tool
from tests.test_flow_error_semantics import (
    FakeStreamClient,
    _collect,
    _finish_chunk,
    _run,
    _text_chunk,
    _tool_call_chunk,
)


@pytest.fixture(autouse=True)
def _clean_hooks():
    registry.clear_tool_hooks()
    yield
    registry.clear_tool_hooks()


def _kb_first(state: GuardState, name: str, arguments: str):
    if name == "web" and state.calls_of("search") == 0:
        return "guard_kb_first", {"error": "KB_FIRST"}
    return None


_BASE = AgentGuards(capped_family=("search",), cap=2, checks=(_kb_first,), cap_kind="guard_search_cap")


def _guards(**over) -> AgentGuards:
    return replace(_BASE, **over)


class _GuardedAgent(GuardedToolCalls, Agent):
    def __init__(self, guards: AgentGuards, **kwargs):
        self.guards = guards
        self.guard = GuardState()
        super().__init__(name="g", instructions="x", **kwargs)


# ── 纯函数 ──


def test_signature_normalizes_argument_order():
    assert call_signature("t", '{"a": 1, "b": 2}') == call_signature("t", '{"b":2,"a":1}')
    assert call_signature("t", "not json") == "t:" + '"not json"'


def test_check_order_duplicate_cap_custom():
    g, s = _guards(), GuardState()
    assert check_tool_call(g, s, "web", "{}") == ("guard_kb_first", {"error": "KB_FIRST"})
    note_tool_call(g, s, "search", '{"q":1}', "c1")
    assert check_tool_call(g, s, "web", "{}") is None
    hit = check_tool_call(g, s, "search", '{"q": 1}')  # 同参不同写法＝重复
    assert hit is not None and hit[0] == GUARD_DUPLICATE and hit[1]["previous_tool_call_id"] == "c1"
    note_tool_call(g, s, "search", '{"q":2}', "c2")
    hit = check_tool_call(g, s, "search", '{"q":3}')
    assert hit is not None and hit[0] == "guard_search_cap"
    assert hit[1]["error"] == "CALL_BUDGET_EXHAUSTED" and "2 次" in hit[1]["hint"]
    # 按次运行覆盖上限
    s.cap = 5
    assert check_tool_call(g, s, "search", '{"q":3}') is None
    assert s.family_calls == 2 and s.calls == {"search": 2}


def test_fold_keeps_latest_and_uses_renderer():
    ctx = FlowContext()
    for i in range(4):
        ctx.add_message("g", "tool", content="x" * 100, tool_call_id=f"c{i}")
    n = fold_tool_messages(
        ctx, "g", budget_tokens=250, model="m", count_tokens=lambda t, m: len(t), render=lambda c, t: f"<{len(t)}>"
    )
    msgs = ctx.get_messages("g")
    assert n == 2 and msgs[0]["content"] == "<100>" and msgs[1]["content"] == "<100>" and msgs[0]["_folded"]
    assert msgs[2]["content"] == "x" * 100 and msgs[3]["content"] == "x" * 100
    # 再跑一次：已折叠的不重复处理，预算已满足
    assert fold_tool_messages(ctx, "g", budget_tokens=250, model="m", count_tokens=lambda t, m: len(t)) == 0


# ── mixin 接入 Agent 循环 ──


def _tool(name: str, text: str = "r") -> Tool:
    return Tool(name, "d", lambda c, i: {"text": text})


@patch("agstack.genai.flow.agent.get_llm_client")
def test_mixin_blocks_duplicate_and_records_trace(mock_client):
    turn1 = [_tool_call_chunk("c1", "search", '{"q":1}'), _finish_chunk("tool_calls")]
    turn2 = [_tool_call_chunk("c2", "search", '{"q":1}'), _finish_chunk("tool_calls")]
    turn3 = [_text_chunk("done"), _finish_chunk()]
    mock_client.return_value = FakeStreamClient([turn1, turn2, turn3])
    hints: list[str] = []

    def hint(state, name, content):
        hints.append(name)
        return f"(hint {state.family_calls})"

    agent = _GuardedAgent(_guards(hint=hint), tools=[_tool("search")], max_turns=5)
    ctx = FlowContext()
    events = _run(_collect(agent.stream(ctx, {"input": "q"})))
    results = [e for e in events if e["type"] == EventType.TOOL_CALL_RESULT]
    assert len(results) == 2 and "previous_tool_call_id" in results[1]["content"]
    tool_msgs = [m for m in ctx.get_messages("g") if m["role"] == "tool"]
    assert tool_msgs[0]["content"].endswith("(hint 1)") and hints == ["search"]  # 拦截的不附提示
    records = [r["tool_name"] for r in ctx.execution_records]
    assert records.count(GUARD_DUPLICATE) == 1 and records.count("search") == 1
    guard_rec = next(r for r in ctx.execution_records if r["tool_name"] == GUARD_DUPLICATE)
    assert guard_rec["success"] and guard_rec["tool_args"]["tool"] == "search" and guard_rec["duration_ms"] == 0


@patch("agstack.genai.flow.agent.get_llm_client")
def test_mixin_folds_when_over_budget(mock_client):
    turns = [[_tool_call_chunk(f"c{i}", "t", f'{{"i":{i}}}'), _finish_chunk("tool_calls")] for i in range(3)]
    turns.append([_text_chunk("done"), _finish_chunk()])
    mock_client.return_value = FakeStreamClient(turns)
    guards = _guards(
        capped_family=(), checks=(), count_tokens=lambda t, m: len(t), fold_budget_ratio=4, fold_renderer=None
    )
    agent = _GuardedAgent(guards, tools=[_tool("t", "y" * 200)], max_turns=6)
    ctx = FlowContext()
    ctx.set_variable("context_length", 1200)  # 预算 300：第三条结果写回后累计 >300，折叠最早一条
    _run(_collect(agent.stream(ctx, {"input": "q"})))
    tool_msgs = [m for m in ctx.get_messages("g") if m["role"] == "tool"]
    assert tool_msgs[0].get("_folded") and "已折叠" in tool_msgs[0]["content"] and not tool_msgs[-1].get("_folded")
    assert any(r["tool_name"] == "guard_fold_results" for r in ctx.execution_records)


@patch("agstack.genai.flow.agent.get_llm_client")
def test_mixin_without_counter_never_folds(mock_client):
    turns = [[_tool_call_chunk(f"c{i}", "t", f'{{"i":{i}}}'), _finish_chunk("tool_calls")] for i in range(3)]
    turns.append([_text_chunk("done"), _finish_chunk()])
    mock_client.return_value = FakeStreamClient(turns)
    agent = _GuardedAgent(AgentGuards(), tools=[_tool("t", "y" * 200)], max_turns=6)
    ctx = FlowContext()
    ctx.set_variable("context_length", 100)
    _run(_collect(agent.stream(ctx, {"input": "q"})))
    assert not any(m.get("_folded") for m in ctx.get_messages("g"))


# ── buffer_plan_text ──


@patch("agstack.genai.flow.agent.get_llm_client")
def test_plan_text_recorded_not_streamed(mock_client):
    turn1 = [_text_chunk("先查一下"), _tool_call_chunk("c1", "t", "{}"), _finish_chunk("tool_calls")]
    turn2 = [_text_chunk("答案"), _finish_chunk()]
    mock_client.return_value = FakeStreamClient([turn1, turn2])
    agent = Agent(name="a", instructions="x", tools=[_tool("t")], max_turns=3)
    ctx = FlowContext()
    events = _run(_collect(buffer_plan_text(agent.stream(ctx, {"input": "q"}), ctx, agent_name="a")))
    deltas = [e["delta"] for e in events if e["type"] == EventType.TEXT_MESSAGE_CONTENT]
    assert deltas == ["答案"]
    plan = next(r for r in ctx.execution_records if r["tool_name"] == PLAN_RECORD)
    assert plan["result"] == "先查一下" and plan["tool_args"] == {"next_tool": "t", "streamed": False}


@patch("agstack.genai.flow.agent.get_llm_client")
def test_plan_text_streams_past_buffer_threshold(mock_client):
    long = "很长的开场" * 10
    turn1 = [_text_chunk(long), _text_chunk("尾"), _tool_call_chunk("c1", "t", "{}"), _finish_chunk("tool_calls")]
    turn2 = [_text_chunk("答案"), _finish_chunk()]
    mock_client.return_value = FakeStreamClient([turn1, turn2])
    agent = Agent(name="a", instructions="x", tools=[_tool("t")], max_turns=3)
    ctx = FlowContext()
    events = _run(_collect(buffer_plan_text(agent.stream(ctx, {"input": "q"}), ctx, agent_name="a", buffer_chars=10)))
    deltas = [e["delta"] for e in events if e["type"] == EventType.TEXT_MESSAGE_CONTENT]
    assert deltas == [long, "尾", "答案"]
    plan = next(r for r in ctx.execution_records if r["tool_name"] == PLAN_RECORD)
    assert plan["tool_args"]["streamed"] is True and plan["summary"].startswith("（已展示给用户）")


@patch("agstack.genai.flow.agent.get_llm_client")
def test_plan_text_closing_line_on_truncation(mock_client):
    turn = [_text_chunk("再查"), _tool_call_chunk("c1", "t", "{}"), _finish_chunk("tool_calls")]
    mock_client.return_value = FakeStreamClient([turn])
    agent = Agent(name="a", instructions="x", tools=[_tool("t")], max_turns=2)
    ctx = FlowContext()
    events = _run(
        _collect(
            buffer_plan_text(
                agent.stream(ctx, {"input": "q"}),
                ctx,
                agent_name="a",
                closing_line=lambda msgs: "找到了资料但没整理完" if any(m["role"] == "tool" for m in msgs) else None,
            )
        )
    )
    deltas = [e["delta"] for e in events if e["type"] == EventType.TEXT_MESSAGE_CONTENT]
    assert deltas == ["找到了资料但没整理完"]
    out = ctx.outputs["a"]
    assert out["truncated"] and out["result"] == "找到了资料但没整理完"


@patch("agstack.genai.flow.agent.get_llm_client")
def test_plan_text_empty_final_uses_empty_line(mock_client):
    mock_client.return_value = FakeStreamClient([[_finish_chunk()]])
    agent = Agent(name="a", instructions="x", max_turns=2)
    ctx = FlowContext()
    events = _run(_collect(buffer_plan_text(agent.stream(ctx, {"input": "q"}), ctx, agent_name="a", empty_line="空")))
    deltas = [e["delta"] for e in events if e["type"] == EventType.TEXT_MESSAGE_CONTENT]
    assert deltas == ["空"] and ctx.outputs["a"]["result"] == "空"
