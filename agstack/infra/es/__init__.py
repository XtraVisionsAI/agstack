#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

from elasticsearch.dsl import async_connections

from ...events import EventType, event_bus


async def setup_es(
    hosts: list[str],
    username: str,
    password: str,
    timeout: float = 3,
    sniff_on_start: bool = True,
    sniff_timeout: float = 3,
):
    # 为 elasticsearch-dsl 创建连接
    es = async_connections.connections.create_connection(
        alias="default",
        hosts=hosts,
        verify_certs=False,
        request_timeout=timeout,
        sniff_on_start=sniff_on_start,
        sniff_timeout=sniff_timeout,
        sniff_on_node_failure=True,
        http_auth=(username, password),
    )
    await event_bus.publish(EventType.ES_INITED, {"es": es})


async def shutdown_es():
    try:
        conn = async_connections.get_connection()
        await conn.close()
    except:  # noqa
        pass


__all__ = [
    "setup_es",
    "shutdown_es",
]
