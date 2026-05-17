#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

import asyncio
from collections.abc import AsyncIterator
from typing import Self

import redis.asyncio as aioredis
from redis.asyncio.client import PubSub

from .base import MessageBus, Subscription


class RedisSubscription(Subscription):
    """Redis Pub/Sub 的订阅实现"""

    def __init__(self, pubsub: PubSub, channels: tuple[str, ...]) -> None:
        self._pubsub = pubsub
        self._channels = channels
        self._task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self._started = False
        self._closed = False

    async def _start(self) -> None:
        if self._started:
            return
        self._started = True
        await self._pubsub.subscribe(*self._channels)
        self._task = asyncio.create_task(self._listen())

    async def __aenter__(self) -> Self:
        await self._start()
        return self

    async def _listen(self) -> None:
        try:
            async for msg in self._pubsub.listen():
                if self._closed:
                    break
                if msg["type"] == "message":
                    channel = msg["channel"]
                    if isinstance(channel, bytes):
                        channel = channel.decode()
                    data = msg["data"]
                    if isinstance(data, str):
                        data = data.encode()
                    self._queue.put_nowait((channel, data))
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def __aiter__(self) -> AsyncIterator[tuple[str, bytes]]:
        return self

    async def __anext__(self) -> tuple[str, bytes]:
        if not self._started:
            await self._start()
        if self._closed and self._queue.empty():
            raise StopAsyncIteration
        try:
            return await self._queue.get()
        except asyncio.CancelledError:
            raise StopAsyncIteration from None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._pubsub.unsubscribe(*self._channels)
        await self._pubsub.aclose()


class RedisMessageBus(MessageBus):
    """基于 Redis Pub/Sub 的消息总线实现"""

    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    async def publish(self, channel: str, message: bytes) -> None:
        await self._client.publish(channel, message)

    def subscribe(self, *channels: str) -> RedisSubscription:
        pubsub = self._client.pubsub()
        return RedisSubscription(pubsub, channels)

    async def close(self) -> None:
        await self._client.aclose()
