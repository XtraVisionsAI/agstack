#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Self


class Subscription(ABC):
    """订阅句柄，支持 async context manager 和 async iteration"""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def __aiter__(self) -> AsyncIterator[tuple[str, bytes]]:
        return self

    @abstractmethod
    async def __anext__(self) -> tuple[str, bytes]:
        """返回 (channel, message)，无消息时阻塞等待"""

    @abstractmethod
    async def close(self) -> None:
        """取消订阅并释放资源"""


class MessageBus(ABC):
    """发布/订阅消息总线"""

    @abstractmethod
    async def publish(self, channel: str, message: bytes) -> None:
        """发布消息到指定 channel，无订阅者时消息丢弃"""

    @abstractmethod
    def subscribe(self, *channels: str) -> Subscription:
        """订阅一个或多个 channel，返回 Subscription 句柄"""

    @abstractmethod
    async def close(self) -> None:
        """释放连接资源"""
