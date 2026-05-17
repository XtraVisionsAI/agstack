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
        async with bus.subscribe("test:ch1") as sub:
            await asyncio.sleep(0.1)
            await bus.publish("test:ch1", b"hello")
            channel, message = await asyncio.wait_for(anext(sub), timeout=3)
            assert channel == "test:ch1"
            assert message == b"hello"

    async def test_multi_channel(self, bus: RedisMessageBus) -> None:
        async with bus.subscribe("test:ch1", "test:ch2") as sub:
            await asyncio.sleep(0.1)
            await bus.publish("test:ch1", b"msg1")
            await bus.publish("test:ch2", b"msg2")
            results = []
            for _ in range(2):
                ch, msg = await asyncio.wait_for(anext(sub), timeout=3)
                results.append((ch, msg))
            channels = [r[0] for r in results]
            assert "test:ch1" in channels
            assert "test:ch2" in channels

    async def test_lazy_start_without_context_manager(self, bus: RedisMessageBus) -> None:
        sub = bus.subscribe("test:ch1")
        await asyncio.sleep(0.1)
        await bus.publish("test:ch1", b"world")
        channel, message = await asyncio.wait_for(anext(sub), timeout=3)
        assert channel == "test:ch1"
        assert message == b"world"
        await sub.close()

    async def test_publish_no_subscribers(self, bus: RedisMessageBus) -> None:
        await bus.publish("test:nobody", b"lost message")


class TestSubscriptionClose:
    async def test_close(self, bus: RedisMessageBus) -> None:
        async with bus.subscribe("test:ch1") as sub:
            await asyncio.sleep(0.1)
        assert sub._closed


class TestBusClose:
    async def test_close(self) -> None:
        client = aioredis.from_url("redis://localhost:6379/15")
        mb = RedisMessageBus(client)
        await mb.close()
