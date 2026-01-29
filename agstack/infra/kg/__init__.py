#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

from ...events import EventType, event_bus


_pool: ConnectionPool | None = None
_space: str | None = None


async def setup_kg(
    hosts: list[tuple[str, int]],
    username: str,
    password: str,
    space: str,
    max_size: int = 10,
    timeout: int = 0,
    idle_time: int = 0,
):
    """初始化 NebulaGraph 连接池"""
    global _pool, _space

    config = Config()
    config.max_connection_pool_size = max_size
    config.timeout = timeout
    config.idle_time = idle_time

    _pool = ConnectionPool()
    if not _pool.init(hosts, config):
        raise RuntimeError("Failed to initialize NebulaGraph connection pool")

    # 验证连接
    session = _pool.get_session(username, password)
    try:
        result = session.execute(f"USE {space}")
        if not result.is_succeeded():
            raise RuntimeError(f"Failed to use space '{space}': {result.error_msg()}")
        _space = space
    finally:
        session.release()

    await event_bus.publish(EventType.KG_CONNECTED)


async def shutdown_kg():
    """关闭 NebulaGraph 连接池"""
    global _pool, _space

    if _pool:
        _pool.close()
    _pool = None
    _space = None


def get_pool() -> ConnectionPool:
    """获取连接池"""
    if _pool is None:
        raise RuntimeError("NebulaGraph connection pool not initialized, please call 'setup_kg' first")
    return _pool


def get_space() -> str:
    """获取当前空间名"""
    if _space is None:
        raise RuntimeError("NebulaGraph space not set")
    return _space


__all__ = [
    "setup_kg",
    "shutdown_kg",
    "get_pool",
    "get_space",
]
