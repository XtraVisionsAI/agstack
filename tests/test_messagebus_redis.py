#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""RedisMessageBus 测试（需要真实 Redis）"""

import asyncio

import pytest
import redis.asyncio as aioredis

from agstack.messagebus.redis import RedisMessageBus


pytestmark = [
    pytest.mark.redis,
    pytest.mark.asyncio,
]


@pytest.fixture
async def bus():
    client = aioredis.from_url("redis://localhost:6379/15")
    mb = RedisMessageBus(client)
    yield mb
    await mb.close()


class TestPublishSubscribe:
    async def test_single_channel(self, bus: RedisMessageBus) -> None:
        sub = bus.subscribe("test:ch1")
        await sub._start()
        async with sub:
            await asyncio.sleep(0.1)
            await bus.publish("test:ch1", b"hello")
            channel, message = await asyncio.wait_for(sub.__anext__(), timeout=3)
            assert channel == "test:ch1"
            assert message == b"hello"

    async def test_multi_channel(self, bus: RedisMessageBus) -> None:
        sub = bus.subscribe("test:ch1", "test:ch2")
        await sub._start()
        async with sub:
            await asyncio.sleep(0.1)
            await bus.publish("test:ch1", b"msg1")
            await bus.publish("test:ch2", b"msg2")
            results = []
            for _ in range(2):
                ch, msg = await asyncio.wait_for(sub.__anext__(), timeout=3)
                results.append((ch, msg))
            channels = [r[0] for r in results]
            assert "test:ch1" in channels
            assert "test:ch2" in channels

    async def test_publish_no_subscribers(self, bus: RedisMessageBus) -> None:
        await bus.publish("test:nobody", b"lost message")


class TestSubscriptionClose:
    async def test_close(self, bus: RedisMessageBus) -> None:
        sub = bus.subscribe("test:ch1")
        await sub._start()
        await asyncio.sleep(0.1)
        await sub.close()


class TestBusClose:
    async def test_close(self) -> None:
        client = aioredis.from_url("redis://localhost:6379/15")
        mb = RedisMessageBus(client)
        await mb.close()
