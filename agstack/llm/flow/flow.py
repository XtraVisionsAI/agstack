#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Flow 定义和执行"""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from . import event
from .exceptions import NodeExecutionError


if TYPE_CHECKING:
    from .context import FlowContext
    from .nodes.base import NodeHandler


@dataclass
class RetryPolicy:
    """节点重试策略"""

    max_retries: int = 0  # 0 = 不重试
    delay: float = 1.0  # 初始延迟（秒）
    backoff: float = 2.0  # 退避倍数


class _SafeFormatDict(dict):
    """安全的模板变量替换，缺失 key 时保留原始占位符"""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


@dataclass
class Flow:
    """Flow 配置定义"""

    flow_id: str
    name: str
    description: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)

    _node_handlers: dict[str, "NodeHandler"] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        from .registry import registry

        self._node_handlers = dict(registry.get_all_node_handlers())

    # ── 重试策略 ──

    @staticmethod
    def _get_retry_policy(node: dict) -> RetryPolicy:
        """从节点 config 解析重试策略"""
        config = node.get("config", {})
        retry_cfg = config.get("retry", {})
        if not retry_cfg:
            return RetryPolicy()
        return RetryPolicy(
            max_retries=retry_cfg.get("max_retries", 0),
            delay=retry_cfg.get("delay", 1.0),
            backoff=retry_cfg.get("backoff", 2.0),
        )

    # ── 边驱动路由 ──

    def _resolve_next_node(self, current_id: str, result: str | None = None) -> str | None:
        """根据当前节点和执行结果，通过 edges 查找下一节点"""
        for edge in self.edges:
            if edge.get("source") == current_id:
                cond = edge.get("condition")
                if cond is None or cond == result:
                    return edge.get("target")
        return None

    @staticmethod
    def _extract_route_key(result: Any) -> str:
        """从节点输出 dict 中提取路由键。

        节点输出 dict 中若包含 ``choice`` 字段，即为路由键。
        没有 ``choice`` 则默认 ``"done"``。
        """
        if isinstance(result, dict):
            return str(result.get("choice", "done"))
        return "done"

    # ── message 节点 ──

    async def _emit_message(self, node: dict, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """输出模板文本，支持 $v. 引用"""
        config = node.get("config", {})
        template = config.get("content", "")
        # 用 variables 做 format_map 替换 {var} 占位符
        text = template.format_map(_SafeFormatDict(context.variables))
        msg_id = context.message_id or str(uuid4())
        yield event.text_message_start(message_id=msg_id, role="assistant")
        yield event.text_message_content(message_id=msg_id, delta=text)
        yield event.text_message_end(message_id=msg_id)

    # ── 带重试的节点执行（统一走 NodeHandler） ──

    async def _execute_node_with_retry(
        self,
        node: dict,
        context: "FlowContext",
        node_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """执行节点，带重试策略，产出 AG-UI 事件"""
        node_type: str = node.get("type", "")
        handler = self._node_handlers.get(node_type)
        if not handler:
            yield event.run_error(
                message=f"Unknown node type: {node_type}",
                code="UNKNOWN_NODE_TYPE",
            )
            raise NodeExecutionError("UNKNOWN_NODE_TYPE", args={"node_type": node_type})

        policy = self._get_retry_policy(node)
        label = handler.get_step_name(node, node_id)
        last_error: Exception | None = None

        for attempt in range(policy.max_retries + 1):
            try:
                if attempt > 0:
                    wait = policy.delay * (policy.backoff ** (attempt - 1))
                    await asyncio.sleep(wait)
                    yield event.custom(
                        name="node_retry",
                        value={
                            "nodeId": node_id,
                            "nodeType": node_type,
                            "label": label,
                            "attempt": attempt + 1,
                            "maxAttempts": policy.max_retries + 1,
                            "error": str(last_error),
                        },
                    )

                async for evt in handler.stream(node, context, node_id):
                    yield evt
                return

            except Exception as e:
                last_error = e
                if attempt < policy.max_retries:
                    continue
                yield event.run_error(
                    message=str(e),
                    code=type(e).__name__,
                )
                raise NodeExecutionError(
                    "NODE_EXECUTION_FAILED",
                    args={"node_id": node_id, "error": str(e)},
                ) from e

    # ── 执行入口 ──

    async def run(self, context: "FlowContext") -> dict[str, Any]:
        """执行 Flow"""
        if not self.edges:
            # 向后兼容：无 edges 时按 nodes 列表顺序执行
            for node in self.nodes:
                node_id = node.get("id")
                if not node_id:
                    continue
                context.current_node = node_id
                node_type: str = node.get("type", "")
                handler = self._node_handlers.get(node_type)
                if handler:
                    result = await handler.execute(node, context)
                    context.set_output(node_id, result)
                else:
                    raise NodeExecutionError("UNKNOWN_NODE_TYPE", args={"node_type": node_type})
        else:
            # edge 驱动执行
            current_node_id: str | None = self.nodes[0]["id"] if self.nodes else None
            while current_node_id:
                node = self.get_node_config(current_node_id)
                if not node:
                    break
                context.current_node = current_node_id
                node_type: str = node.get("type", "")

                if node_type == "message":
                    config = node.get("config", {})
                    template = config.get("content", "")
                    text = template.format_map(_SafeFormatDict(context.variables))
                    context.set_output(current_node_id, {"result": text})
                    current_node_id = self._resolve_next_node(current_node_id, "done")

                elif node_type == "parallel":
                    config = node.get("config", {})
                    branches: list[str] = config.get("branches", [])

                    async def _run_branch(branch_id: str) -> None:
                        branch_node = self.get_node_config(branch_id)
                        if not branch_node:
                            return
                        context.current_node = branch_id
                        branch_type: str = branch_node.get("type", "")
                        branch_handler = self._node_handlers.get(branch_type)
                        if branch_handler:
                            result = await branch_handler.execute(branch_node, context)
                            context.set_output(branch_id, result)

                    await asyncio.gather(*[_run_branch(bid) for bid in branches])
                    context.set_output(current_node_id, {"choice": "done"})
                    current_node_id = self._resolve_next_node(current_node_id, "done")

                elif node_type == "iteration":
                    config = node.get("config", {})
                    items_ref = config.get("items", "")
                    items = context.resolve_reference(items_ref) if isinstance(items_ref, str) else items_ref
                    if not isinstance(items, list):
                        items = [items]

                    item_var = config.get("item_variable", "item")
                    index_var = config.get("index_variable", "index")
                    body_nodes: list[str] = config.get("body", [])
                    results: list[Any] = []

                    for idx, item in enumerate(items):
                        context.set_variable(item_var, item)
                        context.set_variable(index_var, idx)
                        for body_node_id in body_nodes:
                            body_node = self.get_node_config(body_node_id)
                            if not body_node:
                                continue
                            body_type: str = body_node.get("type", "")
                            body_handler = self._node_handlers.get(body_type)
                            if body_handler:
                                body_result = await body_handler.execute(body_node, context)
                                context.set_output(body_node_id, body_result)
                        if body_nodes:
                            results.append(context.outputs.get(body_nodes[-1]))

                    context.set_output(current_node_id, {"results": results})
                    current_node_id = self._resolve_next_node(current_node_id, "done")

                elif node_type == "loop":
                    config = node.get("config", {})
                    body_nodes_l: list[str] = config.get("body", [])
                    condition_node_id = config.get("condition_node")
                    break_cond = config.get("break_condition", "done")
                    max_iter = config.get("max_iterations", 10)
                    loop_var = config.get("loop_variable", "loop_count")

                    for iteration in range(max_iter):
                        context.set_variable(loop_var, iteration)
                        for body_node_id in body_nodes_l:
                            body_node = self.get_node_config(body_node_id)
                            if not body_node:
                                continue
                            body_type: str = body_node.get("type", "")
                            body_handler = self._node_handlers.get(body_type)
                            if body_handler:
                                body_result = await body_handler.execute(body_node, context)
                                context.set_output(body_node_id, body_result)
                        if condition_node_id:
                            cond_result = context.outputs.get(condition_node_id, {})
                            if isinstance(cond_result, dict) and cond_result.get("choice") == break_cond:
                                break

                    context.set_output(current_node_id, {"choice": "done"})
                    current_node_id = self._resolve_next_node(current_node_id, "done")

                elif node_type in self._node_handlers:
                    # 所有执行类节点统一分发
                    handler = self._node_handlers[node_type]
                    result = await handler.execute(node, context)
                    context.set_output(current_node_id, result)
                    route_key = self._extract_route_key(result)
                    current_node_id = self._resolve_next_node(current_node_id, route_key) or self._resolve_next_node(
                        current_node_id, "done"
                    )

                else:
                    raise NodeExecutionError("UNKNOWN_NODE_TYPE", args={"node_type": node_type})

        return context.outputs

    async def stream(self, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """流式执行 Flow（输出 AG-UI 标准事件）"""
        yield event.step_started(step_name=f"flow:{self.name}")

        if not self.edges:
            # 向后兼容：无 edges 时按 nodes 列表顺序执行（原有逻辑）
            async for evt in self._stream_sequential(context):
                yield evt
        else:
            # edge 驱动执行
            async for evt in self._stream_edge_driven(context):
                yield evt

        yield event.step_finished(step_name=f"flow:{self.name}")

    async def _stream_sequential(self, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """顺序流式执行（原有逻辑）"""
        for node in self.nodes:
            node_id = node.get("id")
            if not node_id:
                continue

            context.current_node = node_id
            node_type: str = node.get("type", "")

            if node_type in self._node_handlers:
                async for evt in self._execute_node_with_retry(node, context, node_id):
                    yield evt
            else:
                yield event.run_error(
                    message=f"Unknown node type: {node_type}",
                    code="UNKNOWN_NODE_TYPE",
                )
                raise NodeExecutionError("UNKNOWN_NODE_TYPE", args={"node_type": node_type})

    async def _stream_edge_driven(self, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """边驱动流式执行"""
        current_node_id: str | None = self.nodes[0]["id"] if self.nodes else None

        while current_node_id:
            node = self.get_node_config(current_node_id)
            if not node:
                yield event.run_error(
                    message=f"Node not found: {current_node_id}",
                    code="NODE_NOT_FOUND",
                )
                raise NodeExecutionError("NODE_NOT_FOUND", args={"node_id": current_node_id})

            context.current_node = current_node_id
            node_type: str = node.get("type", "")

            if node_type == "message":
                async for evt in self._emit_message(node, context):
                    yield evt
                current_node_id = self._resolve_next_node(current_node_id, "done")

            elif node_type == "parallel":
                config = node.get("config", {})
                branches = config.get("branches", [])
                yield event.step_started(step_name=f"parallel:{current_node_id}")

                async def _exec_branch(branch_id: str) -> None:
                    branch_node = self.get_node_config(branch_id)
                    if not branch_node:
                        return
                    context.current_node = branch_id
                    branch_type = branch_node.get("type", "")
                    branch_handler = self._node_handlers.get(branch_type)
                    if branch_handler:
                        result = await branch_handler.execute(branch_node, context)
                        context.set_output(branch_id, result)

                await asyncio.gather(*[_exec_branch(bid) for bid in branches])
                context.set_output(current_node_id, {"choice": "done"})
                yield event.step_finished(step_name=f"parallel:{current_node_id}")
                current_node_id = self._resolve_next_node(current_node_id, "done")

            elif node_type == "iteration":
                config = node.get("config", {})
                items_ref = config.get("items", "")
                items = context.resolve_reference(items_ref) if isinstance(items_ref, str) else items_ref
                if not isinstance(items, list):
                    items = [items]

                item_var = config.get("item_variable", "item")
                index_var = config.get("index_variable", "index")
                body_nodes: list[str] = config.get("body", [])
                results: list[Any] = []

                yield event.step_started(step_name=f"iteration:{current_node_id}")
                for idx, item in enumerate(items):
                    context.set_variable(item_var, item)
                    context.set_variable(index_var, idx)
                    for body_node_id in body_nodes:
                        body_node = self.get_node_config(body_node_id)
                        if not body_node:
                            continue
                        body_type = body_node.get("type", "")
                        body_handler = self._node_handlers.get(body_type)
                        if body_handler:
                            body_result = await body_handler.execute(body_node, context)
                            context.set_output(body_node_id, body_result)
                    if body_nodes:
                        results.append(context.outputs.get(body_nodes[-1]))

                context.set_output(current_node_id, {"results": results})
                yield event.step_finished(step_name=f"iteration:{current_node_id}")
                current_node_id = self._resolve_next_node(current_node_id, "done")

            elif node_type == "loop":
                config = node.get("config", {})
                body_nodes_l: list[str] = config.get("body", [])
                condition_node_id = config.get("condition_node")
                break_cond = config.get("break_condition", "done")
                max_iter = config.get("max_iterations", 10)
                loop_var = config.get("loop_variable", "loop_count")

                yield event.step_started(step_name=f"loop:{current_node_id}")
                for iteration in range(max_iter):
                    context.set_variable(loop_var, iteration)
                    for body_node_id in body_nodes_l:
                        body_node = self.get_node_config(body_node_id)
                        if not body_node:
                            continue
                        body_type = body_node.get("type", "")
                        body_handler = self._node_handlers.get(body_type)
                        if body_handler:
                            body_result = await body_handler.execute(body_node, context)
                            context.set_output(body_node_id, body_result)
                    # 检查终止条件
                    if condition_node_id:
                        cond_result = context.outputs.get(condition_node_id, {})
                        if isinstance(cond_result, dict) and cond_result.get("choice") == break_cond:
                            break

                context.set_output(current_node_id, {"choice": "done"})
                yield event.step_finished(step_name=f"loop:{current_node_id}")
                current_node_id = self._resolve_next_node(current_node_id, "done")

            elif node_type in self._node_handlers:
                # 所有执行类节点统一分发
                async for evt in self._execute_node_with_retry(node, context, current_node_id):
                    yield evt
                result = context.outputs.get(current_node_id, {})
                route_key = self._extract_route_key(result)
                current_node_id = self._resolve_next_node(current_node_id, route_key) or self._resolve_next_node(
                    current_node_id, "done"
                )

            else:
                yield event.run_error(
                    message=f"Unknown node type: {node_type}",
                    code="UNKNOWN_NODE_TYPE",
                )
                raise NodeExecutionError("UNKNOWN_NODE_TYPE", args={"node_type": node_type})

    def get_node_config(self, node_id: str) -> dict[str, Any] | None:
        """获取节点配置"""
        for node in self.nodes:
            if node.get("id") == node_id:
                return node
        return None
