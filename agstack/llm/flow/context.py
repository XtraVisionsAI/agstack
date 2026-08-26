#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""统一的执行上下文"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID


if TYPE_CHECKING:
    from .trace import FlowTrace


@dataclass
class Usage:
    """Token 使用统计"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "Usage") -> None:
        """累加使用量"""
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens


@dataclass
class FlowContext:
    """统一的执行上下文"""

    # Flow 层面
    flow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: UUID | None = None
    kb_ids: list[UUID] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)

    # Agent 层面 — 按 agent name 隔离的消息
    messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # 预加载的对话历史（只读，所有 agent 共享）
    history: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    context_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=datetime.now)
    last_agent: str | None = None
    turn_count: int = 0

    # 事件 ID（应用层可注入）
    thread_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None

    # 图执行状态
    outputs: dict[str, Any] = field(default_factory=dict)
    current_node: str | None = None

    # 执行记录（可选）
    execution_records: list[dict[str, Any]] = field(default_factory=list)

    # 结构化执行轨迹
    trace: "FlowTrace" = field(default=None, repr=False)  # type: ignore[assignment]

    # 业务自定义事件缓冲（tool 内部通过 context 注入，agent 循环中 flush）
    pending_custom_events: list[dict[str, Any]] = field(default_factory=list)

    # 协作式取消信号（不参与序列化，恢复即未取消）
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def __post_init__(self) -> None:
        if self.trace is None:
            from .trace import FlowTrace

            self.trace = FlowTrace()

    def get_variable(self, key: str, default: Any = None) -> Any:
        """获取变量值"""
        return self.variables.get(key, default)

    def set_variable(self, key: str, value: Any) -> None:
        """设置变量值"""
        self.variables[key] = value

    def pop_variable(self, key: str, default: Any = None) -> Any:
        """取出并移除变量"""
        return self.variables.pop(key, default)

    def cancel(self) -> None:
        """请求取消执行（幂等）

        协作式取消：引擎在下一个检查点（节点执行前、agent 轮次开始、
        tool_call 执行前）停止，不强杀在途的工具或 LLM 调用。
        """
        self._cancel_event.set()

    @property
    def is_cancelled(self) -> bool:
        """是否已请求取消（长 I/O 工具可自查以提前返回）"""
        return self._cancel_event.is_set()

    def update_variables(self, updates: dict[str, Any]) -> None:
        """批量更新变量"""
        self.variables.update(updates)

    def get_scoped_variables(self, scope: str) -> dict[str, Any]:
        """获取特定作用域的变量"""
        return {k: v for k, v in self.variables.items() if k.startswith(f"{scope}.")}

    def add_message(self, agent_name: str, role: str, content: str | None = None, **kwargs) -> None:
        """添加消息到指定 agent 的消息列表"""
        message = {"role": role, **kwargs}
        if content is not None:
            message["content"] = content
        self.messages.setdefault(agent_name, []).append(message)

    def get_messages(self, agent_name: str) -> list[dict[str, Any]]:
        """获取指定 agent 的消息列表"""
        return self.messages.get(agent_name, [])

    def get_last_output(self, agent_name: str) -> str | None:
        """获取指定 agent 最后一条 assistant 消息的内容"""
        agent_messages = self.messages.get(agent_name, [])
        for msg in reversed(agent_messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return None

    @property
    def all_messages(self) -> list[dict[str, Any]]:
        """扁平视图，合并所有 agent 消息（调试/日志用）"""
        result: list[dict[str, Any]] = []
        for agent_msgs in self.messages.values():
            result.extend(agent_msgs)
        return result

    def add_usage(self, usage: Usage) -> None:
        """累加 token 使用量"""
        self.usage.add(usage)

    def increment_turn(self) -> None:
        """增加轮次计数"""
        self.turn_count += 1

    def clear_messages(self, agent_name: str | None = None) -> None:
        """清空指定 agent 或全部消息历史"""
        if agent_name is not None:
            self.messages.pop(agent_name, None)
        else:
            self.messages.clear()
            self.turn_count = 0

    def resolve_reference(self, ref: Any) -> Any:
        """解析变量引用

        $o.node_id.field.subfield  → context.outputs["node_id"]["field"]["subfield"]
        $v.key                     → context.variables["key"]
        其他字符串                  → 原样返回（字面值）
        """
        if not isinstance(ref, str):
            return ref
        if ref.startswith("$o."):
            parts = ref[3:].split(".")
            result = self.outputs.get(parts[0])
            for part in parts[1:]:
                if isinstance(result, dict):
                    result = result.get(part)
                else:
                    result = getattr(result, part, None)
            return result
        if ref.startswith("$v."):
            return self.variables.get(ref[3:])
        return ref

    def set_output(self, node_id: str, result: Any):
        """设置节点输出"""
        self.outputs[node_id] = result

    def add_execution_record(self, task_id: str, status: str, **kwargs) -> None:
        """添加执行记录"""
        record = {"task_id": task_id, "status": status, "timestamp": datetime.now(), **kwargs}
        self.execution_records.append(record)

    def get_execution_records(self, task_id: str | None = None) -> list[dict[str, Any]]:
        """获取执行记录"""
        if task_id is None:
            return self.execution_records
        return [r for r in self.execution_records if r.get("task_id") == task_id]

    def pop_execution_records(self) -> list[dict[str, Any]]:
        """取出并清空待发射的执行记录"""
        records = self.execution_records
        self.execution_records = []
        return records

    def pop_pending_custom_events(self) -> list[dict[str, Any]]:
        """取出并清空待发射的业务自定义事件"""
        events = self.pending_custom_events
        self.pending_custom_events = []
        return events
