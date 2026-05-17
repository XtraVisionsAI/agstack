#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

import asyncio
from collections.abc import AsyncIterator

from .base import MessageBus, Subscription


class MemorySubscription(Subscription):
    """内存消息总线的订阅实现"""

    def __init__(self, bus: "MemoryMessageBus", channels: tuple[str, ...]) -> None:
        self._bus = bus
        self._channels = channels
        self._queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        # 注册到 bus
        for ch in self._channels:
            self._bus._subscribers.setdefault(ch, []).append(self._queue)

    def __aiter__(self) -> AsyncIterator[tuple[str, bytes]]:
        return self

    async def __anext__(self) -> tuple[str, bytes]:
        try:
            return await self._queue.get()
        except asyncio.CancelledError:
            raise StopAsyncIteration from None

    async def close(self) -> None:
        for ch in self._channels:
            queues = self._bus._subscribers.get(ch)
            if queues and self._queue in queues:
                queues.remove(self._queue)
                if not queues:
                    del self._bus._subscribers[ch]


class MemoryMessageBus(MessageBus):
    """基于内存的发布/订阅消息总线实现"""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[tuple[str, bytes]]]] = {}

    async def publish(self, channel: str, message: bytes) -> None:
        queues = self._subscribers.get(channel)
        if queues:
            for queue in queues:
                queue.put_nowait((channel, message))

    def subscribe(self, *channels: str) -> Subscription:
        return MemorySubscription(self, channels)

    async def close(self) -> None:
        self._subscribers.clear()
