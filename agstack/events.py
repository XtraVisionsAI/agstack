#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""全局事件总线系统"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    """事件类型定义"""

    # Infra 生命周期
    DB_CONNECTED = "db.connected"
    ES_CONNECTED = "es.connected"
    MQ_CONNECTED = "mq.connected"
    KG_CONNECTED = "kg.connected"

    # 组件生命周期
    COMPONENT_REGISTERED = "component.registered"
    COMPONENT_CREATED = "component.created"

    # Flow 执行
    FLOW_STARTED = "flow.started"
    FLOW_COMPLETED = "flow.completed"
    FLOW_FAILED = "flow.failed"

    # 插件生命周期
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_UNLOADED = "plugin.unloaded"


@dataclass
class Event:
    """事件数据结构"""

    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


class EventBus:
    """全局事件总线"""

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}

    def subscribe(self, event_type: EventType, handler: Callable) -> None:
        """订阅事件"""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: Callable) -> None:
        """取消订阅"""
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass

    async def publish(self, event_type: EventType, data: dict[str, Any] | None = None) -> None:
        """发布事件"""
        event = Event(type=event_type, data=data or {})
        handlers = self._handlers.get(event_type, [])

        if not handlers:
            return

        # 并发执行所有处理器
        tasks = []
        for handler in handlers:
            if asyncio.iscoroutinefunction(handler):
                tasks.append(asyncio.create_task(handler(event)))
            else:
                # 同步函数包装为协程
                tasks.append(asyncio.create_task(asyncio.to_thread(handler, event)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_handlers(self, event_type: EventType) -> list[Callable]:
        """获取事件处理器"""
        return self._handlers.get(event_type, []).copy()

    def clear(self, event_type: EventType | None = None) -> None:
        """清除处理器"""
        if event_type is None:
            self._handlers.clear()
        else:
            self._handlers.pop(event_type, None)


# 全局事件总线实例
event_bus = EventBus()
