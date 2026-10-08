"""Agent.request_overrides 按轮覆盖请求参数 + retry_empty_response 空响应重试"""

from unittest.mock import patch

from agstack.genai.flow.agent import Agent
from agstack.genai.flow.context import FlowContext
from agstack.genai.flow.event import EventType
from tests.test_flow_error_semantics import FakeStreamClient, _collect, _finish_chunk, _run, _text_chunk


class _TurnAwareAgent(Agent):
    def request_overrides(self, context, turn, *, retry=False):
        if retry:
            return {"extra_body": {"enable_thinking": False}, "max_tokens": 64}
        return {"extra_body": {"enable_thinking": turn == 1}, "temperature": 0.1}


class TestRequestOverrides:
    @patch("agstack.genai.flow.agent.get_llm_client")
    def test_overrides_merged_per_turn(self, mock_get_client):
        client = FakeStreamClient([[_text_chunk("hi"), _finish_chunk()]])
        mock_get_client.return_value = client
        agent = _TurnAwareAgent(name="a", model="m")
        _run(_collect(agent.stream(FlowContext(), {"input": "q"})))
        assert len(client.requests) == 1
        assert client.requests[0]["extra_body"] == {"enable_thinking": True}
        assert client.requests[0]["temperature"] == 0.1

    @patch("agstack.genai.flow.agent.get_llm_client")
    def test_empty_response_retried_once_with_retry_overrides(self, mock_get_client):
        client = FakeStreamClient([[_finish_chunk()], [_text_chunk("answer"), _finish_chunk()]])
        mock_get_client.return_value = client
        agent = _TurnAwareAgent(name="a", model="m", retry_empty_response=True)
        ctx = FlowContext()
        events = _run(_collect(agent.stream(ctx, {"input": "q"})))
        assert len(client.requests) == 2
        assert client.requests[1]["extra_body"] == {"enable_thinking": False}
        assert client.requests[1]["max_tokens"] == 64
        assert ctx.outputs["a"]["result"] == "answer"
        assert [e["delta"] for e in events if e["type"] == EventType.TEXT_MESSAGE_CONTENT] == ["answer"]

    @patch("agstack.genai.flow.agent.get_llm_client")
    def test_empty_response_not_retried_by_default(self, mock_get_client):
        client = FakeStreamClient([[_finish_chunk()]])
        mock_get_client.return_value = client
        agent = Agent(name="a", model="m")
        ctx = FlowContext()
        _run(_collect(agent.stream(ctx, {"input": "q"})))
        assert len(client.requests) == 1
        assert ctx.outputs["a"]["result"] == ""
