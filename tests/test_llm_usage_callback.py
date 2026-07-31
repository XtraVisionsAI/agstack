#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""LLM usage 回调钩子测试（纯内存，不依赖真实推理后端）"""

from types import SimpleNamespace

import pytest

from agstack.llm import client as llm_client
from agstack.llm.client import UsageEvent, _emit_usage, set_usage_callback


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
