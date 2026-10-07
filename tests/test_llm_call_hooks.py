#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""LLM 调用钩子链（F6）验收用例

织入 LLMClient.chat：before_call 按注册顺序改写消息（抛异常 fail closed），after_call 逆序观察（抛异常 fail open）；
流式路径 after_call 收到 StreamSummary；hook_meta 由 client 弹出不透传后端；chat_sync 不穿链。
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from agstack.llm.client import LLMClient
from agstack.llm.flow.registry import registry
from agstack.llm.hooks import CallMeta, LLMCallHook, StreamSummary, clear_llm_hooks, register_llm_hook


@pytest.fixture(autouse=True)
def _isolated():
    clear_llm_hooks()
    yield
    clear_llm_hooks()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeCompletions:
    def __init__(self, response=None, chunks=None):
        self.response = response
        self.chunks = chunks or []
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):

            async def _gen():
                for c in self.chunks:
                    yield c

            return _gen()
        return self.response


def _client(response=None, chunks=None) -> tuple[LLMClient, _FakeCompletions]:
    client = LLMClient("http://fake", "key")
    fake = _FakeCompletions(response, chunks)
    client._async_client = SimpleNamespace(chat=SimpleNamespace(completions=fake))  # type: ignore[assignment]
    return client, fake


def _resp(content="ok"):
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


def _chunk(finish=None, usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish, delta=SimpleNamespace(content="x"))], usage=usage
    )


class Recorder(LLMCallHook):
    def __init__(self, tag: str, log: list):
        self.tag, self.log = tag, log

    async def before_call(self, messages, tools, meta):
        self.log.append(("before", self.tag, meta.kind))
        return [*messages, {"role": "system", "content": f"injected-by-{self.tag}"}]

    async def after_call(self, response, meta):
        self.log.append(("after", self.tag, type(response).__name__))


def test_before_in_order_after_reversed_and_messages_rewritten():
    log: list = []
    register_llm_hook(Recorder("a", log))
    register_llm_hook(Recorder("b", log))
    client, fake = _client(response=_resp())
    _run(client.chat([{"role": "user", "content": "hi"}], "m", hook_meta={"agent": "x"}))
    assert [e[:2] for e in log] == [("before", "a"), ("before", "b"), ("after", "b"), ("after", "a")]
    sent = fake.calls[0]["messages"]
    assert [m["content"] for m in sent[1:]] == ["injected-by-a", "injected-by-b"]
    assert "hook_meta" not in fake.calls[0]


def test_prepend_takes_chain_head():
    log: list = []
    register_llm_hook(Recorder("a", log))
    register_llm_hook(Recorder("p", log), prepend=True)
    client, _ = _client(response=_resp())
    _run(client.chat([{"role": "user", "content": "hi"}], "m"))
    assert [e[1] for e in log if e[0] == "before"] == ["p", "a"]
    assert [e[1] for e in log if e[0] == "after"] == ["a", "p"]


def test_before_exception_fails_closed_and_backend_not_called():
    class Boom(LLMCallHook):
        async def before_call(self, messages, tools, meta):
            raise RuntimeError("compress failed")

    register_llm_hook(Boom())
    client, fake = _client(response=_resp())
    with pytest.raises(RuntimeError):
        _run(client.chat([{"role": "user", "content": "hi"}], "m"))
    assert fake.calls == []


def test_after_exception_fails_open():
    class Boom(LLMCallHook):
        async def after_call(self, response, meta):
            raise RuntimeError("observer bug")

    register_llm_hook(Boom())
    client, _ = _client(response=_resp("fine"))
    resp = _run(client.chat([{"role": "user", "content": "hi"}], "m"))
    assert resp.choices[0].message.content == "fine"


def test_stream_after_call_receives_summary_with_usage_and_finish_reason():
    seen: list = []

    class Obs(LLMCallHook):
        async def after_call(self, response, meta):
            seen.append((response, meta))

    register_llm_hook(Obs())
    usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10)
    client, fake = _client(chunks=[_chunk(), _chunk(finish="stop", usage=usage)])

    async def _consume():
        stream = await client.chat(
            [{"role": "user", "content": "hi"}], "m", stream=True, tools=[{"t": 1}], hook_meta={"turn": 2}
        )
        return [c async for c in stream]

    chunks = _run(_consume())
    assert len(chunks) == 2
    summary, meta = seen[0]
    assert isinstance(summary, StreamSummary) and summary.finish_reason == "stop"
    assert summary.usage is not None and summary.usage.total_tokens == 10
    assert isinstance(meta, CallMeta) and meta.stream and meta.kind == "chat_stream" and meta.extra == {"turn": 2}
    assert "hook_meta" not in fake.calls[0]


def test_before_receives_tools_and_meta():
    seen: list = []

    class Obs(LLMCallHook):
        async def before_call(self, messages, tools, meta):
            seen.append((tools, meta))
            return messages

    register_llm_hook(Obs())
    client, _ = _client(response=_resp())
    _run(client.chat([{"role": "user", "content": "hi"}], "qwen", tools=[{"name": "t"}], usage_kind="vision"))
    tools, meta = seen[0]
    assert tools == [{"name": "t"}] and meta.model == "qwen" and meta.kind == "vision" and not meta.stream


def test_registry_forwards_registration():
    log: list = []
    registry.register_llm_hook(Recorder("r", log))
    client, _ = _client(response=_resp())
    _run(client.chat([{"role": "user", "content": "hi"}], "m"))
    assert log and log[0][1] == "r"
    registry.clear_llm_hooks()
    log.clear()
    _run(client.chat([{"role": "user", "content": "hi"}], "m"))
    assert log == []


def test_bad_before_return_type_rejected():
    class Bad(LLMCallHook):
        async def before_call(self, messages, tools, meta):  # type: ignore[override]
            return None

    register_llm_hook(Bad())
    client, _ = _client(response=_resp())
    with pytest.raises(TypeError):
        _run(client.chat([{"role": "user", "content": "hi"}], "m"))
