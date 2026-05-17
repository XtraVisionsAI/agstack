#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

import redis.asyncio as aioredis

from .base import CacheBackend


# INCR + 条件 EXPIRE 的 Lua 脚本，保证原子性
_INCR_WITH_TTL_SCRIPT = """
local v = redis.call('INCR', KEYS[1])
if v == 1 and ARGV[1] ~= '0' then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return v
"""


class RedisCacheBackend(CacheBackend):
    """基于 Redis 的缓存后端实现"""

    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client
        self._incr_script = self._client.register_script(_INCR_WITH_TTL_SCRIPT)

    async def get(self, key: str) -> bytes | None:
        value = await self._client.get(key)
        return value

    async def set(self, key: str, value: bytes, ttl: int | None = None) -> None:
        if ttl is not None:
            await self._client.set(key, value, ex=ttl)
        else:
            await self._client.set(key, value)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def delete_pattern(self, pattern: str) -> None:
        cursor: int = 0
        while True:
            cursor, keys = await self._client.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                await self._client.delete(*keys)
            if cursor == 0:
                break

    async def exists(self, key: str) -> bool:
        result = await self._client.exists(key)
        return bool(result)

    async def incr(self, key: str, ttl: int | None = None) -> int:
        if ttl is not None:
            result = await self._incr_script(keys=[key], args=[ttl])
            return int(result)
        else:
            result = await self._client.incr(key)
            return int(result)

    async def expire(self, key: str, ttl: int) -> None:
        await self._client.expire(key, ttl)

    async def close(self) -> None:
        await self._client.aclose()
