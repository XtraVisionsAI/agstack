#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

import asyncio
import time
from functools import wraps
from logging import Logger

from sqlobjects.session import ctx_session, has_session


def autoretry(
    logger: Logger,
    retries: int = 3,
    delay: float = 1.0,
    backoff: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """
    自动重试装饰器，在函数或协程发生异常时自动重试。

    :param logger: 用于记录重试尝试和失败的日志记录器实例。
    :param retries: 最大重试次数。默认为 3。
    :param delay: 初始重试延迟时间（秒）。默认为 1.0。
    :param backoff: 退避倍数。1.0 表示固定延迟，2.0 表示指数退避。默认为 1.0。
    :param exceptions: 可重试的异常类型元组。默认为 (Exception,)。
    """
    retries = max(1, retries)
    delay = max(0, delay)
    backoff = max(1.0, backoff)

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            awaitable = asyncio.iscoroutinefunction(func)
            for i in range(retries + 1):
                try:
                    return await func(*args, **kwargs) if awaitable else func(*args, **kwargs)
                except exceptions as e:
                    if i == retries:
                        logger.error(f"'{func.__name__}' failed after {retries} retries: {e}")
                        raise

                    wait_time = delay * (backoff**i)
                    logger.warning(f"'{func.__name__}' failed, retry {i + 1}/{retries} in {wait_time:.1f}s: {e}")
                    await asyncio.sleep(wait_time) if awaitable else time.sleep(wait_time)

        return wrapper

    return decorator


def with_session(func, dbname: str | None = None):
    """装饰器：确保方法在 session 上下文中执行，支持嵌套调用"""

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        # 如果已经在 session 上下文中，直接执行
        if has_session(dbname):
            return await func(self, *args, **kwargs)

        # 否则创建新的 session 上下文
        async with ctx_session(dbname):
            return await func(self, *args, **kwargs)

    return wrapper
