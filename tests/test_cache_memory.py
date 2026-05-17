#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""MemoryCacheBackend 全接口测试"""

import time
from unittest.mock import patch

import pytest

from agstack.cache import MemoryCacheBackend


@pytest.fixture
def cache() -> MemoryCacheBackend:
    return MemoryCacheBackend()


class TestGetSet:
    @pytest.mark.asyncio
    async def test_set_and_get(self, cache: MemoryCacheBackend) -> None:
        await cache.set("key1", b"value1")
        assert await cache.get("key1") == b"value1"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, cache: MemoryCacheBackend) -> None:
        assert await cache.get("missing") is None

    @pytest.mark.asyncio
    async def test_set_overwrite(self, cache: MemoryCacheBackend) -> None:
        await cache.set("key1", b"v1")
        await cache.set("key1", b"v2")
        assert await cache.get("key1") == b"v2"

    @pytest.mark.asyncio
    async def test_set_with_ttl_not_expired(self, cache: MemoryCacheBackend) -> None:
        await cache.set("key1", b"value1", ttl=10)
        assert await cache.get("key1") == b"value1"

    @pytest.mark.asyncio
    async def test_set_with_ttl_expired(self, cache: MemoryCacheBackend) -> None:
        await cache.set("key1", b"value1", ttl=1)
        # 模拟时间流逝
        with patch("agstack.cache.memory.time.monotonic", return_value=time.monotonic() + 2):
            assert await cache.get("key1") is None


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, cache: MemoryCacheBackend) -> None:
        await cache.set("key1", b"value1")
        await cache.delete("key1")
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, cache: MemoryCacheBackend) -> None:
        await cache.delete("missing")  # 不应抛异常


class TestDeletePattern:
    @pytest.mark.asyncio
    async def test_delete_pattern_wildcard(self, cache: MemoryCacheBackend) -> None:
        await cache.set("user:1:name", b"alice")
        await cache.set("user:2:name", b"bob")
        await cache.set("session:1", b"data")
        await cache.delete_pattern("user:*")
        assert await cache.get("user:1:name") is None
        assert await cache.get("user:2:name") is None
        assert await cache.get("session:1") == b"data"

    @pytest.mark.asyncio
    async def test_delete_pattern_question_mark(self, cache: MemoryCacheBackend) -> None:
        await cache.set("a1", b"v1")
        await cache.set("a2", b"v2")
        await cache.set("ab", b"v3")
        await cache.delete_pattern("a?")
        assert await cache.get("a1") is None
        assert await cache.get("a2") is None
        assert await cache.get("ab") is None


class TestExists:
    @pytest.mark.asyncio
    async def test_exists_true(self, cache: MemoryCacheBackend) -> None:
        await cache.set("key1", b"value1")
        assert await cache.exists("key1") is True

    @pytest.mark.asyncio
    async def test_exists_false(self, cache: MemoryCacheBackend) -> None:
        assert await cache.exists("missing") is False

    @pytest.mark.asyncio
    async def test_exists_expired(self, cache: MemoryCacheBackend) -> None:
        await cache.set("key1", b"value1", ttl=1)
        with patch("agstack.cache.memory.time.monotonic", return_value=time.monotonic() + 2):
            assert await cache.exists("key1") is False


class TestIncr:
    @pytest.mark.asyncio
    async def test_incr_new_key(self, cache: MemoryCacheBackend) -> None:
        result = await cache.incr("counter")
        assert result == 1
        assert await cache.get("counter") == b"1"

    @pytest.mark.asyncio
    async def test_incr_existing_key(self, cache: MemoryCacheBackend) -> None:
        await cache.set("counter", b"5")
        result = await cache.incr("counter")
        assert result == 6
        assert await cache.get("counter") == b"6"

    @pytest.mark.asyncio
    async def test_incr_with_ttl_new_key(self, cache: MemoryCacheBackend) -> None:
        result = await cache.incr("counter", ttl=10)
        assert result == 1
        # 验证 ttl 已设置（过期后取不到）
        with patch("agstack.cache.memory.time.monotonic", return_value=time.monotonic() + 11):
            assert await cache.get("counter") is None

    @pytest.mark.asyncio
    async def test_incr_with_ttl_existing_key(self, cache: MemoryCacheBackend) -> None:
        await cache.set("counter", b"3", ttl=100)
        result = await cache.incr("counter", ttl=5)
        assert result == 4
        # 已有 key 不修改过期时间，原 ttl 仍有效
        assert await cache.get("counter") == b"4"


class TestExpire:
    @pytest.mark.asyncio
    async def test_expire_sets_ttl(self, cache: MemoryCacheBackend) -> None:
        await cache.set("key1", b"value1")
        await cache.expire("key1", 1)
        with patch("agstack.cache.memory.time.monotonic", return_value=time.monotonic() + 2):
            assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_expire_nonexistent_key(self, cache: MemoryCacheBackend) -> None:
        await cache.expire("missing", 10)  # 不应抛异常


class TestClose:
    @pytest.mark.asyncio
    async def test_close(self, cache: MemoryCacheBackend) -> None:
        await cache.close()  # 空操作，不应抛异常
