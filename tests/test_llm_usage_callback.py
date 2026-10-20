#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""LLM usage 回调钩子测试（纯内存，不依赖真实推理后端）"""

from types import SimpleNamespace

import pytest

from agstack.genai.llm import client as llm_client
from agstack.genai.llm.client import UsageEvent, _emit_usage, set_usage_callback


@pytest.fixture(autouse=True)
def _reset_callback():
    """每个用例结束后注销全局回调，避免用例间串扰"""
    yield
    set_usage_callback(None)


def test_emit_with_object_usage():
    """openai 响应对象形式的 usage 正常提取三个分量"""
    events: list[UsageEvent] = []
    set_usage_callback(events.append)

    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    _emit_usage("qwen3", "chat", usage, 1200)

    assert events == [
        UsageEvent(
            model="qwen3", kind="chat", prompt_tokens=100, completion_tokens=50, total_tokens=150, duration_ms=1200
        )
    ]


def test_emit_with_dict_usage():
    """rerank 的 JSON 字典形式 usage 同样可提取，缺失字段记 0"""
    events: list[UsageEvent] = []
    set_usage_callback(events.append)

    _emit_usage("bge-reranker", "rerank", {"total_tokens": 320}, 80)

    assert events[0].total_tokens == 320
    assert events[0].prompt_tokens == 0
    assert events[0].completion_tokens == 0


def test_emit_with_none_usage():
    """后端未返回 usage 时仍触发事件，各分量为 0（保留 model/duration 供监控）"""
    events: list[UsageEvent] = []
    set_usage_callback(events.append)

    _emit_usage("qwen3", "chat_stream", None, 500)

    assert events[0].total_tokens == 0
    assert events[0].model == "qwen3"
    assert events[0].duration_ms == 500


def test_callback_exception_swallowed():
    """回调抛异常不得影响主调用链"""

    def _bad_callback(_evt: UsageEvent) -> None:
        raise RuntimeError("boom")

    set_usage_callback(_bad_callback)
    _emit_usage("qwen3", "chat", None, 10)  # 不应抛出


def test_no_callback_noop():
    """未注册回调时静默跳过"""
    assert llm_client._usage_callback is None
    _emit_usage("qwen3", "chat", None, 10)  # 不应抛出


def test_unregister_callback():
    """传 None 注销回调后不再触发"""
    events: list[UsageEvent] = []
    set_usage_callback(events.append)
    set_usage_callback(None)

    _emit_usage("qwen3", "chat", None, 10)

    assert events == []


# ---------------------------------------------------------------------------
# 调用链路：流式中断兜底与 rerank 无 usage 上报
# ---------------------------------------------------------------------------


def _chunk(content: str | None = None, usage=None, finish_reason: str | None = None):
    delta = SimpleNamespace(content=content, reasoning_content=None, tool_calls=None)
    choices = [SimpleNamespace(delta=delta, finish_reason=finish_reason)] if usage is None else []
    return SimpleNamespace(choices=choices, usage=usage)


class _FakeStream:
    def __init__(self, chunks, error: Exception | None = None):
        self._chunks = list(chunks)
        self._error = error

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        if self._error is not None:
            raise self._error
        raise StopAsyncIteration


def _client_with_stream(chunks=None, *, create_error: Exception | None = None, stream_error: Exception | None = None):
    client = llm_client.LLMClient(base_url="http://localhost:1/v1", api_key="x")

    async def _create(**_kwargs):
        if create_error is not None:
            raise create_error
        return _FakeStream(chunks or [], stream_error)

    client._async_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))  # type: ignore[assignment]
    return client


_MESSAGES: list = [{"role": "system", "content": "你是助手。" * 50}, {"role": "user", "content": "你好"}]


@pytest.mark.asyncio
async def test_stream_completed_reports_actual_usage_once():
    """流正常结束：按末尾 usage 块实报，恰好一次"""
    events: list[UsageEvent] = []
    set_usage_callback(events.append)
    usage = SimpleNamespace(prompt_tokens=120, completion_tokens=8, total_tokens=128)
    client = _client_with_stream([_chunk("你好"), _chunk(finish_reason="stop"), _chunk(usage=usage)])

    stream = await client.chat(_MESSAGES, model="qwen3", stream=True)
    async for _ in stream:
        pass

    assert len(events) == 1
    assert (events[0].prompt_tokens, events[0].completion_tokens, events[0].estimated) == (120, 8, False)
    assert events[0].kind == "chat_stream"


@pytest.mark.asyncio
async def test_stream_closed_early_reports_estimate():
    """消费方提前关闭（用户停止 / 取消）：未收到 usage 块，按分词器估算上报"""
    events: list[UsageEvent] = []
    set_usage_callback(events.append)
    client = _client_with_stream([_chunk("第一段输出"), _chunk("第二段输出"), _chunk(finish_reason="stop")])

    stream = await client.chat(_MESSAGES, model="qwen3", stream=True)
    async for _ in stream:
        break
    await stream.aclose()  # type: ignore[attr-defined]

    assert len(events) == 1
    evt = events[0]
    assert evt.estimated is True
    assert evt.prompt_tokens > 50  # 长 system prompt 计入
    assert evt.completion_tokens > 0  # 已产出的第一段计入
    assert evt.total_tokens == evt.prompt_tokens + evt.completion_tokens


@pytest.mark.asyncio
async def test_stream_closed_after_usage_reports_actual():
    """已收到 usage 块后才被关闭：按实报上报，不估算"""
    events: list[UsageEvent] = []
    set_usage_callback(events.append)
    usage = SimpleNamespace(prompt_tokens=200, completion_tokens=9, total_tokens=209)
    client = _client_with_stream([_chunk("a"), _chunk(usage=usage), _chunk("尾巴")])

    stream = await client.chat(_MESSAGES, model="qwen3", stream=True)
    async for chunk in stream:
        if chunk.usage:
            break
    await stream.aclose()  # type: ignore[attr-defined]

    assert len(events) == 1
    assert (events[0].prompt_tokens, events[0].estimated) == (200, False)


@pytest.mark.asyncio
async def test_stream_error_midway_reports_estimate():
    """流中途异常：抛 LLMError 之余仍上报估算"""
    events: list[UsageEvent] = []
    set_usage_callback(events.append)
    client = _client_with_stream([_chunk("部分输出")], stream_error=RuntimeError("connection reset"))

    stream = await client.chat(_MESSAGES, model="qwen3", stream=True)
    with pytest.raises(llm_client.LLMError):
        async for _ in stream:
            pass

    assert len(events) == 1
    assert events[0].estimated is True and events[0].completion_tokens > 0


@pytest.mark.asyncio
async def test_stream_create_failure_not_billed():
    """建流前即失败（请求未被受理）：不上报，与非流式失败口径一致"""
    events: list[UsageEvent] = []
    set_usage_callback(events.append)
    client = _client_with_stream(create_error=RuntimeError("refused"))

    stream = await client.chat(_MESSAGES, model="qwen3", stream=True)
    with pytest.raises(llm_client.LLMError):
        async for _ in stream:
            pass

    assert events == []


@pytest.mark.asyncio
async def test_rerank_without_usage_still_reported():
    """后端 rerank 响应不带 usage：仍上报一条 0 token 事件（按次计价的次数依据）"""
    events: list[UsageEvent] = []
    set_usage_callback(events.append)
    client = llm_client.LLMClient(base_url="http://localhost:1/v1", api_key="x")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"index": 0, "relevance_score": 0.9, "document": {"text": "a"}}]}

    async def _post(*_args, **_kwargs):
        return _Resp()

    client._async_http_client = SimpleNamespace(post=_post)  # type: ignore[assignment]

    results = await client.rerank("q", ["a"], model="bge-reranker", top_n=1)

    assert results == [(0, 0.9, "a")]
    assert len(events) == 1
    assert (events[0].kind, events[0].total_tokens) == ("rerank", 0)
