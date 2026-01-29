#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""统一的执行上下文"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


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

    # Agent 层面
    messages: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    context_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=datetime.now)
    last_agent: str | None = None
    turn_count: int = 0

    # 图执行状态
    node_results: dict[str, Any] = field(default_factory=dict)
    current_node: str | None = None

    # 执行记录（可选）
    execution_records: list[dict[str, Any]] = field(default_factory=list)

    def get_variable(self, key: str, default: Any = None) -> Any:
        """获取变量值"""
        return self.variables.get(key, default)

    def set_variable(self, key: str, value: Any) -> None:
        """设置变量值"""
        self.variables[key] = value

    def update_variables(self, updates: dict[str, Any]) -> None:
        """批量更新变量"""
        self.variables.update(updates)

    def get_scoped_variables(self, scope: str) -> dict[str, Any]:
        """获取特定作用域的变量"""
        return {k: v for k, v in self.variables.items() if k.startswith(f"{scope}.")}

    def add_message(self, role: str, content: str | None = None, **kwargs) -> None:
        """添加消息"""
        message = {"role": role, **kwargs}
        if content is not None:
            message["content"] = content
        self.messages.append(message)

    def add_usage(self, usage: Usage) -> None:
        """累加 token 使用量"""
        self.usage.add(usage)

    def increment_turn(self) -> None:
        """增加轮次计数"""
        self.turn_count += 1

    def clear_messages(self) -> None:
        """清空消息历史"""
        self.messages.clear()
        self.turn_count = 0

    def resolve_reference(self, ref: str) -> Any:
        """解析变量引用 {node@variable.field}"""
        if not isinstance(ref, str) or not ref.startswith("{"):
            return ref

        ref_content = ref[1:-1]  # 移除 {}
        if "@" not in ref_content:
            return self.variables.get(ref_content)

        node_id, var_path = ref_content.split("@", 1)
        result = self.node_results.get(node_id)

        # 支持嵌套字段访问 variable.field.subfield
        for field_name in var_path.split("."):
            if isinstance(result, dict):
                result = result.get(field_name)
            else:
                result = getattr(result, field_name, None)
        return result

    def set_node_result(self, node_id: str, result: Any):
        """设置节点执行结果"""
        self.node_results[node_id] = result

    def add_execution_record(self, task_id: str, status: str, **kwargs) -> None:
        """添加执行记录"""
        record = {"task_id": task_id, "status": status, "timestamp": datetime.now(), **kwargs}
        self.execution_records.append(record)

    def get_execution_records(self, task_id: str | None = None) -> list[dict[str, Any]]:
        """获取执行记录"""
        if task_id is None:
            return self.execution_records
        return [r for r in self.execution_records if r.get("task_id") == task_id]
