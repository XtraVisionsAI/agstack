#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""结构化执行轨迹 — 与事件流独立，flow 执行完成后一次性可用"""

import time
from dataclasses import dataclass, field
from typing import Any

from .context import Usage


@dataclass
class NodeTrace:
    """单次节点执行记录"""

    node_id: str
    node_type: str
    label: str | None = None

    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: Any = None
    error: str | None = None

    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    duration_ms: int | None = None

    usage: Usage | None = None

    parent_id: str | None = None
    iteration_index: int | None = None
    execution_index: int = 0

    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] | None = None


@dataclass
class EdgeTrace:
    """实际遍历的边"""

    source: str
    target: str | None = None
    condition: str | None = None
    condition_value: Any = None
    satisfied: bool = True
    timestamp: float = field(default_factory=time.time)


@dataclass
class FlowTrace:
    """完整执行轨迹"""

    nodes: list[NodeTrace] = field(default_factory=list)
    edges: list[EdgeTrace] = field(default_factory=list)
    total_usage: Usage = field(default_factory=Usage)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None

    _namespace_stack: list[str] = field(default_factory=list, repr=False)
    _active_nodes: dict[str, NodeTrace] = field(default_factory=dict, repr=False)
    _execution_counter: int = field(default=0, repr=False)

    def push_namespace(self, parent_id: str) -> None:
        self._namespace_stack.append(parent_id)

    def pop_namespace(self) -> str:
        return self._namespace_stack.pop()

    def _qualify_id(self, node_id: str) -> str:
        if self._namespace_stack:
            return "::".join(self._namespace_stack) + "::" + node_id
        return node_id

    def record_node_start(
        self,
        node_id: str,
        node_type: str,
        inputs: dict[str, Any] | None = None,
        *,
        label: str | None = None,
        parent_id: str | None = None,
        iteration_index: int | None = None,
    ) -> NodeTrace:
        qid = self._qualify_id(node_id)
        self._execution_counter += 1
        trace = NodeTrace(
            node_id=qid,
            node_type=node_type,
            label=label,
            inputs=inputs or {},
            parent_id=parent_id or (self._namespace_stack[-1] if self._namespace_stack else None),
            iteration_index=iteration_index,
            execution_index=self._execution_counter,
        )
        self.nodes.append(trace)
        self._active_nodes[qid] = trace
        return trace

    def record_node_end(
        self,
        node_id: str,
        outputs: Any = None,
        *,
        error: str | None = None,
        usage: Usage | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        qid = self._qualify_id(node_id)
        trace = self._active_nodes.pop(qid, None)
        if trace is None:
            return
        trace.outputs = outputs
        trace.error = error
        trace.finished_at = time.time()
        trace.duration_ms = int((trace.finished_at - trace.started_at) * 1000)
        trace.usage = usage
        if tool_calls:
            trace.tool_calls = tool_calls
        if messages is not None:
            trace.messages = messages

    def record_edge(
        self,
        source: str,
        target: str | None,
        *,
        condition: str | None = None,
        condition_value: Any = None,
        satisfied: bool = True,
    ) -> None:
        self.edges.append(
            EdgeTrace(
                source=self._qualify_id(source),
                target=self._qualify_id(target) if target else None,
                condition=condition,
                condition_value=condition_value,
                satisfied=satisfied,
            )
        )
