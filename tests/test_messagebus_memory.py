#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""MemoryMessageBus 全接口测试"""

import asyncio

import pytest

from agstack.messagebus import MemoryMessageBus


@pytest.fixture
def bus() -> MemoryMessageBus:
    return MemoryMessageBus()


class TestPublishSubscribe:
    @pytest.mark.asyncio
    async def test_single_channel(self, bus: MemoryMessageBus) -> None:
        sub = bus.subscribe("ch1")
        async with sub:
            await bus.publish("ch1", b"hello")
            channel, message = await asyncio.wait_for(sub.__anext__(), timeout=1)
            assert channel == "ch1"
            assert message == b"hello"

    @pytest.mark.asyncio
    async def test_multi_channel(self, bus: MemoryMessageBus) -> None:
        sub = bus.subscribe("ch1", "ch2")
        async with sub:
            await bus.publish("ch1", b"msg1")
            await bus.publish("ch2", b"msg2")
            results = []
            for _ in range(2):
                ch, msg = await asyncio.wait_for(sub.__anext__(), timeout=1)
                results.append((ch, msg))
            assert ("ch1", b"msg1") in results
            assert ("ch2", b"msg2") in results

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, bus: MemoryMessageBus) -> None:
        sub1 = bus.subscribe("ch1")
        sub2 = bus.subscribe("ch1")
        async with sub1, sub2:
            await bus.publish("ch1", b"broadcast")
            ch1, msg1 = await asyncio.wait_for(sub1.__anext__(), timeout=1)
            ch2, msg2 = await asyncio.wait_for(sub2.__anext__(), timeout=1)
            assert msg1 == b"broadcast"
            assert msg2 == b"broadcast"

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self, bus: MemoryMessageBus) -> None:
        # 无订阅者时 publish 不应抛异常
        await bus.publish("ch1", b"nobody listening")

    @pytest.mark.asyncio
    async def test_subscription_close_removes_queue(self, bus: MemoryMessageBus) -> None:
        sub = bus.subscribe("ch1")
        async with sub:
            pass
        # close 后不应再收到消息
        assert "ch1" not in bus._subscribers or len(bus._subscribers.get("ch1", [])) == 0


class TestClose:
    @pytest.mark.asyncio
    async def test_bus_close_clears_subscribers(self, bus: MemoryMessageBus) -> None:
        bus.subscribe("ch1")
        bus.subscribe("ch2")
        await bus.close()
        assert len(bus._subscribers) == 0
