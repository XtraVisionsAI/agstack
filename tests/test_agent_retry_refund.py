#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""模型请求瞬时故障重发（harness.retry，3.4）+ 守卫对执行失败调用的退账

- is_transient_error：沿异常链按类名 / 状态码 / 超时文本判定；参数错误等非瞬时故障不算；
- Agent.stream：请求在放出任何内容前因瞬时故障失败 → agent_retry CUSTOM 事件 + 执行记录 → 等待后同轮重发；
  每次运行至多 max_retries 次；非瞬时故障、未配置策略、已放出文字的均按原路报错；与溢出恢复分开计数；
- GuardedToolCalls：执行失败（``{"error": ...}``）的调用退还一次签名与次数，同参重试不再被判重复、不占工具族上限；
  同一签名只退一次。
"""

from dataclasses import replace
from unittest.mock import patch

import pytest

from agstack.genai.flow.agent import Agent
from agstack.genai.flow.context import FlowContext
from agstack.genai.flow.event import EventType
from agstack.genai.flow.exceptions import FlowError
from agstack.genai.flow.guards import (
    GUARD_DUPLICATE,
    GUARD_REFUND,
    AgentGuards,
    GuardedToolCalls,
    GuardState,
    failed_tool_error,
    note_tool_call,
    refund_tool_call,
)
from agstack.genai.flow.registry import registry
from agstack.genai.flow.tool import Tool
from agstack.genai.harness import RETRY_EVENT, RETRY_RECORD, OverflowPolicy, TransientRetry, is_transient_error
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


class _FailingClient(FakeStreamClient):
    """前若干次请求依次抛给定异常，之后按预编程轮次返回"""

    def __init__(self, turns, errors: list[Exception]):
        super().__init__(turns)
        self.errors = list(errors)

    async def chat(self, stream: bool = True, **kwargs):
        if self.errors:
            self.requests.append(kwargs)
            raise self.errors.pop(0)
        return await super().chat(stream=stream, **kwargs)


class _MidStreamClient(FakeStreamClient):
    """首次请求放出一段文字后断开"""

    async def chat(self, stream: bool = True, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:

            async def _gen():
                yield _text_chunk("半截")
                raise TimeoutError("read timed out")

            return _gen()
        return await super().chat(stream=stream, **kwargs)


_FAST = TransientRetry(max_retries=1, delay=0)


# ── 判定 ──


class TestClassify:
    def test_types_status_and_cause_chain(self):
        assert is_transient_error(TimeoutError("x"))
        assert is_transient_error(RuntimeError("HTTP 503 Service Unavailable"))
        assert is_transient_error(RuntimeError("429 Too Many Requests"))
        assert is_transient_error(RuntimeError("Request timed out"))
        try:
            try:
                raise ConnectionResetError("peer")
            except ConnectionResetError as inner:
                raise FlowError("WRAPPED", 500, {}) from inner
        except FlowError as wrapped:
            assert is_transient_error(wrapped)
        assert not is_transient_error(ValueError("invalid tool arguments"))
        assert not is_transient_error(RuntimeError("This model's maximum context length is 1000 tokens"))

    def test_policy_counts_and_backoff(self):
        policy = TransientRetry(max_retries=2, delay=0.5)
        assert policy.should_retry(TimeoutError(), 0) and policy.should_retry(TimeoutError(), 1)
        assert not policy.should_retry(TimeoutError(), 2) and not policy.should_retry(ValueError(), 0)
        assert policy.backoff(0) == 0.5 and policy.backoff(1) == 1.0


# ── Agent 循环 ──


@patch("agstack.genai.flow.agent.get_llm_client")
def test_resends_once_before_any_output(mock_client):
    client = _FailingClient([[_text_chunk("ok"), _finish_chunk()]], [TimeoutError("read timed out")])
    mock_client.return_value = client
    agent = Agent(name="a", retry=_FAST)
    ctx = FlowContext()
    events = _run(_collect(agent.stream(ctx, {"input": "q"})))
    assert len(client.requests) == 2 and ctx.outputs["a"]["result"] == "ok"
    custom = [e for e in events if e["type"] == EventType.CUSTOM and e.get("name") == RETRY_EVENT]
    assert len(custom) == 1 and custom[0]["value"]["attempt"] == 1 and custom[0]["value"]["error"] == "TimeoutError"
    record = next(r for r in ctx.execution_records if r["tool_name"] == RETRY_RECORD)
    assert record["tool_args"]["agent"] == "a" and record["tool_args"]["turn"] == 1


@patch("agstack.genai.flow.agent.get_llm_client")
def test_gives_up_after_max_retries(mock_client):
    client = _FailingClient([[_text_chunk("ok"), _finish_chunk()]], [TimeoutError("t1"), TimeoutError("t2")])
    mock_client.return_value = client
    with pytest.raises(FlowError):
        _run(_collect(Agent(name="a", retry=_FAST).stream(FlowContext(), {"input": "q"})))
    assert len(client.requests) == 2


@patch("agstack.genai.flow.agent.get_llm_client")
def test_no_retry_for_non_transient_or_without_policy(mock_client):
    bad = _FailingClient([[_text_chunk("ok"), _finish_chunk()]], [ValueError("bad request")])
    mock_client.return_value = bad
    with pytest.raises(FlowError):
        _run(_collect(Agent(name="a", retry=_FAST).stream(FlowContext(), {"input": "q"})))
    assert len(bad.requests) == 1
    plain = _FailingClient([[_text_chunk("ok"), _finish_chunk()]], [TimeoutError("t")])
    mock_client.return_value = plain
    with pytest.raises(FlowError):
        _run(_collect(Agent(name="a").stream(FlowContext(), {"input": "q"})))
    assert len(plain.requests) == 1


@patch("agstack.genai.flow.agent.get_llm_client")
def test_no_retry_after_text_streamed(mock_client):
    client = _MidStreamClient([[_text_chunk("ok"), _finish_chunk()]])
    mock_client.return_value = client
    with pytest.raises(FlowError):
        _run(_collect(Agent(name="a", retry=_FAST).stream(FlowContext(), {"input": "q"})))
    assert len(client.requests) == 1


@patch("agstack.genai.flow.agent.get_llm_client")
def test_retry_and_overflow_counted_separately(mock_client):
    """一次瞬断 + 一次溢出：各用各的配额，都能恢复"""
    calls: list[str] = []

    async def compact(context, kind):
        calls.append(kind)
        return True

    client = _FailingClient(
        [[_text_chunk("ok"), _finish_chunk()]],
        [TimeoutError("t"), RuntimeError("This model's maximum context length is 1000 tokens")],
    )
    mock_client.return_value = client
    agent = Agent(name="a", retry=_FAST, overflow=OverflowPolicy(compact=compact, max_recoveries=1))
    ctx = FlowContext()
    _run(_collect(agent.stream(ctx, {"input": "q"})))
    assert len(client.requests) == 3 and calls == ["error"] and ctx.outputs["a"]["result"] == "ok"


def test_class_attribute_default():
    class _Retrying(Agent):
        retry = _FAST

    assert _Retrying(name="a").retry is _FAST and Agent(name="b").retry is None


# ── 守卫退账 ──


def test_failed_tool_error_only_matches_pure_error_payload():
    assert failed_tool_error('{"error": "boom"}') == "boom"
    assert failed_tool_error('{"error": "boom"}\n(hint)') == "boom"
    assert failed_tool_error('{"error": "KB_FIRST", "hint": "x"}') is None
    assert failed_tool_error('{"text": "r"}') is None
    assert failed_tool_error("plain text") is None


def test_refund_tool_call_once_per_signature():
    guards = AgentGuards(capped_family=("search",), cap=2)
    state = GuardState()
    note_tool_call(guards, state, "search", '{"q": 1}', "c1")
    assert refund_tool_call(guards, state, "search", '{"q": 1}', "c1")
    assert state.family_calls == 0 and state.calls["search"] == 0 and not state.seen_calls
    note_tool_call(guards, state, "search", '{"q": 1}', "c2")
    assert not refund_tool_call(guards, state, "search", '{"q": 1}', "c2")  # 同签名只退一次
    assert state.family_calls == 1
    state.reset()
    assert not state.refunded


class _GuardedAgent(GuardedToolCalls, Agent):
    def __init__(self, guards: AgentGuards, **kwargs):
        self.guards = guards
        self.guard = GuardState()
        super().__init__(name="g", instructions="x", **kwargs)


def _flaky_tool(failures: dict) -> Tool:
    def fn(context, inputs):
        if failures["left"] > 0:
            failures["left"] -= 1
            raise RuntimeError("backend timeout")
        return {"text": "hit"}

    return Tool("search", "d", fn)


@patch("agstack.genai.flow.agent.get_llm_client")
def test_failed_call_refunded_so_identical_retry_runs(mock_client):
    turn = [_tool_call_chunk("c1", "search", '{"q":1}'), _finish_chunk("tool_calls")]
    retry = [_tool_call_chunk("c2", "search", '{"q":1}'), _finish_chunk("tool_calls")]
    done = [_text_chunk("done"), _finish_chunk()]
    mock_client.return_value = FakeStreamClient([turn, retry, done])
    agent = _GuardedAgent(AgentGuards(capped_family=("search",), cap=1), tools=[_flaky_tool({"left": 1})], max_turns=5)
    ctx = FlowContext()
    _run(_collect(agent.stream(ctx, {"input": "q"})))
    tool_msgs = [m["content"] for m in ctx.get_messages("g") if m["role"] == "tool"]
    assert failed_tool_error(tool_msgs[0]) and tool_msgs[1] == '{"text": "hit"}'
    names = [r["tool_name"] for r in ctx.execution_records]
    assert names.count(GUARD_REFUND) == 1 and GUARD_DUPLICATE not in names
    assert agent.guard.family_calls == 1  # 失败那次已退，成功那次记账；上限 1 未被失败调用占掉


@patch("agstack.genai.flow.agent.get_llm_client")
def test_refund_can_be_disabled(mock_client):
    turn = [_tool_call_chunk("c1", "search", '{"q":1}'), _finish_chunk("tool_calls")]
    retry = [_tool_call_chunk("c2", "search", '{"q":1}'), _finish_chunk("tool_calls")]
    done = [_text_chunk("done"), _finish_chunk()]
    mock_client.return_value = FakeStreamClient([turn, retry, done])
    guards = replace(AgentGuards(capped_family=("search",), cap=2), refund_failed=False)
    agent = _GuardedAgent(guards, tools=[_flaky_tool({"left": 1})], max_turns=5)
    ctx = FlowContext()
    _run(_collect(agent.stream(ctx, {"input": "q"})))
    names = [r["tool_name"] for r in ctx.execution_records]
    assert GUARD_REFUND not in names and names.count(GUARD_DUPLICATE) == 1
