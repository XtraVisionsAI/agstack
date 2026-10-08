#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""上下文溢出三态判定 + Agent 循环内一次 compact-and-retry（harness.overflow，3.0）

- classify_overflow：错误文本（限流排除）→ error；finish_reason=length 且无输出且输入 ≥ 99% 窗口 → length；
  实报 prompt_tokens > 窗口 → silent；缺窗口 / 缺实报 → None；
- Agent.stream：报错态 / 静默态 / length 态命中且本次未放出内容 → agent_overflow CUSTOM 事件 → compact → 同轮重发；
  每次运行最多 max_recoveries 次；compact 无成效或未配置策略 → 按原路报错；流式文字已出不恢复。
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agstack.genai.flow.agent import Agent
from agstack.genai.flow.context import FlowContext
from agstack.genai.flow.event import EventType
from agstack.genai.flow.exceptions import FlowError
from agstack.genai.harness import OVERFLOW_ERROR, OVERFLOW_LENGTH, OVERFLOW_SILENT, OverflowPolicy, classify_overflow
from tests.test_flow_error_semantics import FakeStreamClient, _collect, _finish_chunk, _run, _text_chunk


def _usage_finish(reason: str, prompt: int, completion: int):
    chunk = _finish_chunk(reason)
    chunk.usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion)
    return chunk


class _RaisingClient(FakeStreamClient):
    """前 ``fail_times`` 次请求抛给定异常，之后按预编程轮次返回"""

    def __init__(self, turns, *, fail_times: int, error: Exception):
        super().__init__(turns)
        self.fail_times = fail_times
        self.error = error

    async def chat(self, stream: bool = True, **kwargs):
        if len(self.requests) < self.fail_times:
            self.requests.append(kwargs)
            raise self.error
        return await super().chat(stream=stream, **kwargs)


def _policy(calls: list, *, effective: bool = True, window: int | None = 1000, max_recoveries: int = 1):
    async def compact(context: FlowContext, kind: str) -> bool:
        calls.append(kind)
        if effective:
            # 应用侧压缩：丢掉共享历史的前半
            del context.history[: max(len(context.history) // 2, 1)]
        return effective

    return OverflowPolicy(compact=compact, context_length=window, max_recoveries=max_recoveries)


def _history(n: int):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"h{i}"} for i in range(n)]


class TestClassify:
    def test_error_patterns_and_rate_limit_exclusion(self):
        assert (
            classify_overflow(error=RuntimeError("This model's maximum context length is 32768 tokens"))
            == OVERFLOW_ERROR
        )
        assert classify_overflow(error="prompt is too long: 210000 tokens > 200000 maximum") == OVERFLOW_ERROR
        assert classify_overflow(error="Input is too long for requested model.") == OVERFLOW_ERROR
        assert classify_overflow(error="Error code: 400 - context_length_exceeded") == OVERFLOW_ERROR
        assert classify_overflow(error="Rate limit reached for tokens, please retry") is None
        assert classify_overflow(error="429 Too Many Requests: token limit") is None
        assert classify_overflow(error="connection reset by peer") is None

    def test_usage_states(self):
        assert (
            classify_overflow(finish_reason="length", prompt_tokens=995, completion_tokens=0, context_length=1000)
            == OVERFLOW_LENGTH
        )
        assert (
            classify_overflow(finish_reason="length", prompt_tokens=995, completion_tokens=300, context_length=1000)
            is None
        )
        assert (
            classify_overflow(finish_reason="length", prompt_tokens=500, completion_tokens=0, context_length=1000)
            is None
        )
        assert (
            classify_overflow(finish_reason="stop", prompt_tokens=1200, completion_tokens=20, context_length=1000)
            == OVERFLOW_SILENT
        )
        assert (
            classify_overflow(finish_reason="stop", prompt_tokens=800, completion_tokens=20, context_length=1000)
            is None
        )
        assert (
            classify_overflow(finish_reason="stop", prompt_tokens=1200, completion_tokens=20, context_length=None)
            is None
        )
        assert classify_overflow(finish_reason="stop", prompt_tokens=None, context_length=1000) is None

    def test_policy_window_resolver(self):
        async def compact(context, kind):
            return True

        ctx = FlowContext(variables={"context_length": "4096"})
        assert (
            OverflowPolicy(compact=compact, context_length=lambda c: c.get_variable("context_length")).window(ctx)
            == 4096
        )
        assert OverflowPolicy(compact=compact).window(ctx) is None


class TestAgentRecovery:
    @patch("agstack.genai.flow.agent.get_llm_client")
    def test_error_state_compacts_and_retries_once(self, mock_get_client):
        client = _RaisingClient(
            [[_text_chunk("answer"), _finish_chunk()]],
            fail_times=1,
            error=RuntimeError("maximum context length exceeded"),
        )
        mock_get_client.return_value = client
        calls: list = []
        agent = Agent(name="a", model="m", overflow=_policy(calls))
        ctx = FlowContext()
        ctx.history = _history(6)
        events = _run(_collect(agent.stream(ctx, {"input": "q"})))
        assert calls == [OVERFLOW_ERROR]
        assert len(client.requests) == 2
        # 第二次请求用的是压缩后的历史：system + 3 条历史 + 1 条 user
        assert len(client.requests[1]["messages"]) == 1 + 3 + 1
        custom = [e for e in events if e["type"] == EventType.CUSTOM and e["name"] == "agent_overflow"]
        assert custom and custom[0]["value"] == {"agentName": "a", "kind": OVERFLOW_ERROR, "turn": 1}
        assert ctx.outputs["a"]["result"] == "answer"
        assert not [e for e in events if e["type"] == EventType.RUN_ERROR]

    @patch("agstack.genai.flow.agent.get_llm_client")
    def test_length_state_recovers_and_silent_state_recovers(self, mock_get_client):
        for first in (_usage_finish("length", 995, 0), _usage_finish("stop", 1200, 0)):
            client = FakeStreamClient([[first], [_text_chunk("ok"), _finish_chunk()]])
            mock_get_client.return_value = client
            calls: list = []
            agent = Agent(name="a", model="m", overflow=_policy(calls))
            ctx = FlowContext()
            ctx.history = _history(4)
            _run(_collect(agent.stream(ctx, {"input": "q"})))
            assert len(calls) == 1 and len(client.requests) == 2
            assert ctx.outputs["a"]["result"] == "ok"

    @patch("agstack.genai.flow.agent.get_llm_client")
    def test_second_overflow_is_not_recovered(self, mock_get_client):
        client = _RaisingClient(
            [[_text_chunk("x"), _finish_chunk()]], fail_times=2, error=RuntimeError("too many tokens")
        )
        mock_get_client.return_value = client
        calls: list = []
        agent = Agent(name="a", model="m", overflow=_policy(calls))
        ctx = FlowContext()
        ctx.history = _history(4)
        with pytest.raises(FlowError):
            _run(_collect(agent.stream(ctx, {"input": "q"})))
        assert calls == [OVERFLOW_ERROR] and len(client.requests) == 2

    @patch("agstack.genai.flow.agent.get_llm_client")
    def test_ineffective_compact_or_no_policy_raises_as_before(self, mock_get_client):
        for agent in (
            Agent(name="a", model="m", overflow=_policy([], effective=False)),
            Agent(name="a", model="m"),
        ):
            client = _RaisingClient([[_finish_chunk()]], fail_times=1, error=RuntimeError("context window exceeded"))
            mock_get_client.return_value = client
            with pytest.raises(FlowError):
                _run(_collect(agent.stream(FlowContext(), {"input": "q"})))
            assert len(client.requests) == 1

    @patch("agstack.genai.flow.agent.get_llm_client")
    def test_non_overflow_error_and_streamed_text_are_not_recovered(self, mock_get_client):
        calls: list = []
        # 限流不是溢出
        client = _RaisingClient([[_finish_chunk()]], fail_times=1, error=RuntimeError("rate limit exceeded"))
        mock_get_client.return_value = client
        with pytest.raises(FlowError):
            _run(_collect(Agent(name="a", model="m", overflow=_policy(calls)).stream(FlowContext(), {"input": "q"})))
        # 静默态但已有文字放出：不回收
        client = FakeStreamClient([[_text_chunk("partial"), _usage_finish("stop", 1200, 5)]])
        mock_get_client.return_value = client
        ctx = FlowContext()
        _run(_collect(Agent(name="a", model="m", overflow=_policy(calls)).stream(ctx, {"input": "q"})))
        assert calls == [] and len(client.requests) == 1 and ctx.outputs["a"]["result"] == "partial"

    @patch("agstack.genai.flow.agent.get_llm_client")
    def test_class_attribute_default_policy(self, mock_get_client):
        calls: list = []

        class _MyAgent(Agent):
            overflow = _policy(calls)

        client = _RaisingClient(
            [[_text_chunk("y"), _finish_chunk()]], fail_times=1, error=RuntimeError("input length exceeds")
        )
        mock_get_client.return_value = client
        ctx = FlowContext()
        ctx.history = _history(2)
        _run(_collect(_MyAgent(name="a", model="m").stream(ctx, {"input": "q"})))
        assert calls == [OVERFLOW_ERROR] and ctx.outputs["a"]["result"] == "y"
