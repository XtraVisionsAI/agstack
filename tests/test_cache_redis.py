#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""RedisCacheBackend 测试（需要真实 Redis）"""

import pytest
import redis.asyncio as aioredis

from agstack.cache import CacheBackend, RedisCacheBackend


pytestmark = [
    pytest.mark.redis,
    pytest.mark.asyncio,
]


@pytest.fixture
async def cache():
    client = aioredis.from_url("redis://localhost:6379/15")
    await client.flushdb()
    backend = RedisCacheBackend(client)
    yield backend
    await client.flushdb()
    await backend.close()


class TestGetSet:
    async def test_set_and_get(self, cache: CacheBackend) -> None:
        await cache.set("key1", b"value1")
        assert await cache.get("key1") == b"value1"

    async def test_get_nonexistent(self, cache: CacheBackend) -> None:
        assert await cache.get("missing") is None

    async def test_set_overwrite(self, cache: CacheBackend) -> None:
        await cache.set("key1", b"v1")
        await cache.set("key1", b"v2")
        assert await cache.get("key1") == b"v2"

    async def test_set_with_ttl(self, cache: CacheBackend) -> None:
        await cache.set("key1", b"value1", ttl=10)
        assert await cache.get("key1") == b"value1"


class TestDelete:
    async def test_delete_existing(self, cache: CacheBackend) -> None:
        await cache.set("key1", b"value1")
        await cache.delete("key1")
        assert await cache.get("key1") is None

    async def test_delete_nonexistent(self, cache: CacheBackend) -> None:
        await cache.delete("missing")


class TestDeletePattern:
    async def test_delete_pattern(self, cache: CacheBackend) -> None:
        await cache.set("user:1:name", b"alice")
        await cache.set("user:2:name", b"bob")
        await cache.set("session:1", b"data")
        await cache.delete_pattern("user:*")
        assert await cache.get("user:1:name") is None
        assert await cache.get("user:2:name") is None
        assert await cache.get("session:1") == b"data"


class TestExists:
    async def test_exists_true(self, cache: CacheBackend) -> None:
        await cache.set("key1", b"value1")
        assert await cache.exists("key1") is True

    async def test_exists_false(self, cache: CacheBackend) -> None:
        assert await cache.exists("missing") is False


class TestIncr:
    async def test_incr_new_key(self, cache: CacheBackend) -> None:
        result = await cache.incr("counter")
        assert result == 1

    async def test_incr_existing_key(self, cache: CacheBackend) -> None:
        await cache.set("counter", b"5")
        result = await cache.incr("counter")
        assert result == 6

    async def test_incr_with_ttl(self, cache: CacheBackend) -> None:
        result = await cache.incr("counter", ttl=60)
        assert result == 1


class TestExpire:
    async def test_expire_sets_ttl(self, cache: CacheBackend) -> None:
        await cache.set("key1", b"value1")
        await cache.expire("key1", 60)
        assert await cache.exists("key1") is True


class TestClose:
    async def test_close(self) -> None:
        client = aioredis.from_url("redis://localhost:6379/15")
        backend = RedisCacheBackend(client)
        await backend.close()
