#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

from contextlib import contextmanager
from dataclasses import dataclass

from nebula3.Config import Config
from nebula3.data.ResultSet import ResultSet
from nebula3.gclient.net import ConnectionPool

from ...events import EventType, event_bus


@dataclass
class _KGContext:
    pool: ConnectionPool
    space: str
    username: str
    password: str


_context: _KGContext | None = None


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
    global _context

    config = Config()
    config.max_connection_pool_size = max_size
    config.timeout = timeout
    config.idle_time = idle_time

    pool = ConnectionPool()
    if not pool.init(hosts, config):
        raise RuntimeError("Failed to initialize NebulaGraph connection pool")

    # 验证连接，space 不存在时自动创建
    session = pool.get_session(username, password)
    try:
        session.execute(f"CREATE SPACE IF NOT EXISTS `{space}` (vid_type=FIXED_STRING(64))")
        result = session.execute(f"USE `{space}`")
        if not result.is_succeeded():
            raise RuntimeError(f"Failed to use space '{space}': {result.error_msg()}")
    finally:
        session.release()

    _context = _KGContext(pool=pool, space=space, username=username, password=password)
    await event_bus.publish(EventType.KG_INITED, {"pool": pool})


async def shutdown_kg():
    """关闭 NebulaGraph 连接池"""
    global _context

    if _context:
        _context.pool.close()
    _context = None


def get_pool() -> ConnectionPool:
    """获取连接池"""
    if _context is None:
        raise RuntimeError("NebulaGraph connection pool not initialized, please call 'setup_kg' first")
    return _context.pool


def get_space() -> str:
    """获取当前空间名"""
    if _context is None:
        raise RuntimeError("NebulaGraph not initialized, please call 'setup_kg' first")
    return _context.space


@contextmanager
def use_session():
    """获取会话的上下文管理器，自动释放连接"""
    if _context is None:
        raise RuntimeError("NebulaGraph not initialized, please call 'setup_kg' first")
    session = _context.pool.get_session(_context.username, _context.password)
    try:
        yield session
    finally:
        session.release()


def execute(query: str) -> ResultSet:
    """执行 nGQL 查询"""
    with use_session() as session:
        result = session.execute(query)
        if not result.is_succeeded():
            raise RuntimeError(f"Query failed: {result.error_msg()}")
        return result


__all__ = [
    "setup_kg",
    "shutdown_kg",
    "get_pool",
    "get_space",
    "use_session",
    "execute",
]
