#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

import time
from fnmatch import fnmatch

from .base import CacheBackend


class MemoryCacheBackend(CacheBackend):
    """基于内存的缓存后端实现"""

    def __init__(self) -> None:
        # key -> (value, 过期时间戳 | None)
        self._store: dict[str, tuple[bytes, float | None]] = {}

    def _is_expired(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return True
        _, expires_at = entry
        if expires_at is not None and time.monotonic() >= expires_at:
            del self._store[key]
            return True
        return False

    async def get(self, key: str) -> bytes | None:
        if self._is_expired(key):
            return None
        return self._store[key][0]

    async def set(self, key: str, value: bytes, ttl: int | None = None) -> None:
        expires_at = time.monotonic() + ttl if ttl is not None else None
        self._store[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def delete_pattern(self, pattern: str) -> None:
        keys_to_delete = [k for k in self._store if fnmatch(k, pattern)]
        for k in keys_to_delete:
            del self._store[k]

    async def exists(self, key: str) -> bool:
        return not self._is_expired(key)

    async def incr(self, key: str, ttl: int | None = None) -> int:
        if self._is_expired(key):
            # key 不存在，从 0 开始递增
            value = 1
            expires_at = time.monotonic() + ttl if ttl is not None else None
            self._store[key] = (str(value).encode(), expires_at)
        else:
            current_bytes, expires_at = self._store[key]
            value = int(current_bytes.decode()) + 1
            # 已有 key 不修改过期时间
            self._store[key] = (str(value).encode(), expires_at)
        return value

    async def expire(self, key: str, ttl: int) -> None:
        entry = self._store.get(key)
        if entry is not None:
            value, _ = entry
            self._store[key] = (value, time.monotonic() + ttl)

    async def close(self) -> None:
        pass
