#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

from typing import Any

from sqlobjects.database import close_db, init_db


async def setup_db(
    username: str,
    password: str,
    host: str,
    database: str,
    echo: bool = False,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: int = 30,
    pool_recycle: int = 3600,
    **engine_kwargs: Any,
):
    url = f"postgresql+asyncpg://{username}:{password}@{host}/{database}"
    await init_db(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        **engine_kwargs,
    )


async def shutdown_db():
    await close_db()
