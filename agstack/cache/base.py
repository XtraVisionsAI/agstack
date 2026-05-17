#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

from abc import ABC, abstractmethod


class CacheBackend(ABC):
    """键值缓存后端抽象"""

    @abstractmethod
    async def get(self, key: str) -> bytes | None:
        """获取缓存值，不存在或已过期返回 None"""

    @abstractmethod
    async def set(self, key: str, value: bytes, ttl: int | None = None) -> None:
        """设置缓存值，ttl 单位为秒，None 表示永不过期"""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """删除指定 key"""

    @abstractmethod
    async def delete_pattern(self, pattern: str) -> None:
        """删除匹配 glob 通配符的所有 key"""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """判断 key 是否存在且未过期"""

    @abstractmethod
    async def incr(self, key: str, ttl: int | None = None) -> int:
        """原子递增，不存在的 key 从 0 开始；如提供 ttl 且 key 新建则设置过期"""

    @abstractmethod
    async def expire(self, key: str, ttl: int) -> None:
        """为已有 key 设置过期时间（秒）"""

    @abstractmethod
    async def close(self) -> None:
        """释放连接资源"""
