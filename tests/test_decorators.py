#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""``with_session`` 返回协程函数：可直接交给 ``asyncio.run`` / ``asyncio.create_task``（类型与运行两层）。"""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any, assert_type

import pytest

import agstack.decorators as decorators
from agstack.decorators import with_session


@with_session
async def _double(x: int) -> int:
    return x * 2


def test_with_session_declares_coroutine() -> None:
    coro = _double(1)
    # 类型层：pyright 校验返回值是 Coroutine（此前声明为 Awaitable，交给 asyncio.run 会报错）
    _ = assert_type(coro, Coroutine[Any, Any, int])
    coro.close()


def test_with_session_runs_under_asyncio(monkeypatch: pytest.MonkeyPatch) -> None:
    # 走"已在 session 上下文"分支，避免依赖真实数据库
    monkeypatch.setattr(decorators, "has_session", lambda dbname=None: True)

    async def main() -> int:
        return await asyncio.create_task(_double(5))

    assert _run_in_thread(lambda: asyncio.run(_double(21))) == 42
    assert _run_in_thread(lambda: asyncio.run(main())) == 10


def _run_in_thread(fn: Callable[[], int]) -> int:
    """在独立线程里跑 ``asyncio.run``：它收尾时会清空当前线程的事件循环，不能污染主线程上其它测试用的 loop。"""
    result: list[int] = []
    t = threading.Thread(target=lambda: result.append(fn()))
    t.start()
    t.join()
    return result[0]
