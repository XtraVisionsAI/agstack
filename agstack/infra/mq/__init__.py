#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

from asyncio import get_event_loop

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractConnection, AbstractQueue

from ...events import EventType, event_bus


_connection: AbstractConnection | None = None
_channels: dict[str, AbstractChannel] = {}
_dlx_setup: set[str] = set()  # 记录已设置死信队列的队列名


async def setup_mq(host: str, port: int, username: str, password: str):
    """初始化消息队列连接"""
    global _connection

    connection_url = f"amqp://{username}:{password}@{host}:{port}/"
    _connection = await aio_pika.connect_robust(connection_url, loop=get_event_loop())

    await event_bus.publish(EventType.MQ_CONNECTED)


async def shutdown_mq():
    """关闭消息队列连接"""
    global _connection

    if _connection and not _connection.is_closed:
        await _connection.close()
    _connection = None


async def use_channel(
    name: str, durable: bool = True, prefetch_count: int = 1, with_dlq: bool = False, **queue_args: str
) -> AbstractChannel:
    """获取或创建消息队列通道

    :param name: 队列名称
    :param durable: 是否持久化
    :param prefetch_count: 预取数量
    :param with_dlq: 是否配置死信队列
    :param queue_args: 额外的队列参数
    :return: 消息通道
    """
    channel = _channels.get(name)

    if channel is None:
        if _connection is None:
            raise RuntimeError("Message queue connection not initialized, please call 'init_mq' first")

        channel = await _connection.channel()
        await channel.set_qos(prefetch_count=prefetch_count)

        # 如果需要死信队列且未设置过
        if with_dlq and name not in _dlx_setup:
            await _setup_dlq(channel, name)
            _dlx_setup.add(name)

            # 添加死信队列配置到队列参数
            dlq_args = {
                "x-dead-letter-exchange": f"{name}-dlx",
                "x-dead-letter-routing-key": f"{name}-dlq",
            }
            queue_args = {**queue_args, **dlq_args} if queue_args else dlq_args

        await channel.declare_queue(name, durable=durable, arguments=queue_args if queue_args else None)  # type: ignore
        _channels[name] = channel

    return channel


async def _setup_dlq(channel: AbstractChannel, queue_name: str):
    """为指定队列设置死信队列

    :param channel: 消息通道
    :param queue_name: 主队列名称
    """
    # 死信交换机
    dlx = await channel.declare_exchange(f"{queue_name}-dlx", type=aio_pika.ExchangeType.DIRECT, durable=True)

    # 死信队列
    dlq = await channel.declare_queue(f"{queue_name}-dlq", durable=True)
    await dlq.bind(dlx, routing_key=f"{queue_name}-dlq")


async def use_queue(
    name: str, durable: bool = True, prefetch_count: int = 1, with_dlq: bool = False, **queue_args
) -> AbstractQueue:
    """获取或创建消息队列

    :param name: 队列名称
    :param durable: 是否持久化
    :param prefetch_count: 预取数量
    :param with_dlq: 是否配置死信队列
    :param queue_args: 额外的队列参数
    :return: 消息队列
    """
    channel = await use_channel(name, durable, prefetch_count, with_dlq, **queue_args)
    queue = await channel.get_queue(name)

    return queue


__all__ = ["setup_mq", "shutdown_mq", "use_channel", "use_queue"]
