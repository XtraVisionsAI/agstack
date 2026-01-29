#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

from elasticsearch.dsl import async_connections


async def setup_es(
    host: str, username: str, password: str, timeout: float = 3, sniff_on_start: bool = True, sniff_timeout: float = 3
):
    # 为 elasticsearch-dsl 创建连接
    async_connections.connections.create_connection(
        hosts=host,
        verify_certs=False,
        request_timeout=timeout,
        sniff_on_start=sniff_on_start,
        sniff_timeout=sniff_timeout,
        sniff_on_node_failure=True,
        http_auth=(username, password),
    )


async def shotdown_es():
    await async_connections.get_connection().close()
