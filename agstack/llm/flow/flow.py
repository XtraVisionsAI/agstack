#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Flow 定义和执行"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from . import event
from .exceptions import NodeExecutionError


if TYPE_CHECKING:
    from .context import FlowContext
    from .nodes.base import NodeHandler


_OPERATORS = (">=", "<=", "!=", "==", ">", "<")


def _parse_literal(s: str) -> Any:
    """解析字面量值"""
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if s in ("none", "None", "null"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


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
    cycle_limits: dict[str, int] = field(default_factory=dict)

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

    def _eval_condition(self, condition: str, context: "FlowContext") -> bool:
        """对边条件表达式求值"""
        for op in _OPERATORS:
            if op in condition:
                left, right = condition.split(op, 1)
                left_val = context.resolve_reference(left.strip())
                right_val = _parse_literal(right.strip())
                if op == "==":
                    return left_val == right_val
                if op == "!=":
                    return left_val != right_val
                try:
                    if op == ">":
                        return left_val > right_val
                    if op == "<":
                        return left_val < right_val
                    if op == ">=":
                        return left_val >= right_val
                    if op == "<=":
                        return left_val <= right_val
                except TypeError:
                    return False
                break
        return bool(context.resolve_reference(condition.strip()))

    def _resolve_next_node(self, current_id: str, context: "FlowContext", force_fallback: bool = False) -> str | None:
        """根据当前节点，通过 edges 表达式求值查找下一节点。

        force_fallback=True 时跳过条件边，只走无条件边（用于循环超限逃逸）。
        """
        fallback_target: str | None = None
        for edge in self.edges:
            if edge.get("source") != current_id:
                continue
            cond = edge.get("condition")
            if cond is None:
                fallback_target = edge.get("target")
            elif not force_fallback:
                satisfied = self._eval_condition(cond, context)
                context.trace.record_edge(
                    current_id,
                    edge.get("target"),
                    condition=cond,
                    condition_value=satisfied,
                    satisfied=satisfied,
                )
                if satisfied:
                    return edge.get("target")

        # 记录最终选中的 fallback 边
        if fallback_target is not None:
            context.trace.record_edge(
                current_id,
                fallback_target,
                condition=None,
                condition_value=None,
                satisfied=True,
            )
        return fallback_target

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

    def _resolve_visibility(self, node_type: str, config: dict) -> tuple[str | None, bool]:
        """从 node config 和 registry 解析可见性（label, echo）

        优先级：flow node config > registry 注册属性
        """
        from .registry import registry

        label = config.get("label")
        explicit_echo = "echo" in config
        echo = config.get("echo", False)

        if not explicit_echo and label is None:
            if node_type == "agent":
                agent_name = config.get("agent_name", "")
                label = registry.get_agent_label(agent_name)
                if not label:
                    echo = registry.get_agent_echo(agent_name)
            elif node_type == "tool":
                tool_name = config.get("tool_name", "")
                label = registry.get_tool_label(tool_name)
                if not label:
                    echo = registry.get_tool_echo(tool_name)

        if label and not explicit_echo:
            echo = True

        return label, echo

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
        vis_label, vis_echo = self._resolve_visibility(node_type, node.get("config", {}))
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
                    evt["_node_id"] = node_id
                    evt["_label"] = vis_label
                    evt["_echo"] = vis_echo
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

    def _check_cycle_limit(self, node_id: str, visit_count: dict[str, int]) -> bool:
        """检查节点是否超出循环次数限制。返回 True 表示超限。"""
        limit = self.cycle_limits.get(node_id)
        if limit is not None and visit_count.get(node_id, 0) > limit:
            return True
        return False

    async def run(self, context: "FlowContext") -> dict[str, Any]:
        """执行 Flow"""
        if not self.edges:
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
            current_node_id: str | None = self.nodes[0]["id"] if self.nodes else None
            visit_count: dict[str, int] = {}

            while current_node_id:
                node = self.get_node_config(current_node_id)
                if not node:
                    break

                # 循环计数与超限检测
                visit_count[current_node_id] = visit_count.get(current_node_id, 0) + 1
                force_fallback = self._check_cycle_limit(current_node_id, visit_count)
                if force_fallback:
                    current_node_id = self._resolve_next_node(current_node_id, context, force_fallback=True)
                    continue

                context.current_node = current_node_id
                node_type: str = node.get("type", "")

                if node_type == "message":
                    config = node.get("config", {})
                    template = config.get("content", "")
                    text = template.format_map(_SafeFormatDict(context.variables))
                    context.set_output(current_node_id, {"result": text})
                    current_node_id = self._resolve_next_node(current_node_id, context)

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
                    merged: dict[str, Any] = {}
                    for bid in branches:
                        branch_result = context.outputs.get(bid, {})
                        if isinstance(branch_result, dict):
                            merged.update(branch_result)
                    context.set_output(current_node_id, merged)
                    current_node_id = self._resolve_next_node(current_node_id, context)

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
                    current_node_id = self._resolve_next_node(current_node_id, context)

                elif node_type in self._node_handlers:
                    handler = self._node_handlers[node_type]
                    result = await handler.execute(node, context)
                    context.set_output(current_node_id, result)
                    current_node_id = self._resolve_next_node(current_node_id, context)

                else:
                    raise NodeExecutionError("UNKNOWN_NODE_TYPE", args={"node_type": node_type})

        return context.outputs

    async def stream(self, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """流式执行 Flow（输出 AG-UI 标准事件）"""
        context.trace.started_at = time.time()
        yield event.step_started(step_name=f"flow:{self.name}")

        try:
            if not self.edges:
                async for evt in self._stream_sequential(context):
                    yield evt
            else:
                async for evt in self._stream_edge_driven(context):
                    yield evt
        except Exception as e:
            context.trace.error = str(e)
            raise
        finally:
            context.trace.finished_at = time.time()
            context.trace.total_usage = context.usage

        yield event.step_finished(step_name=f"flow:{self.name}")

    async def _stream_sequential(self, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """顺序流式执行"""
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
        visit_count: dict[str, int] = {}

        while current_node_id:
            node = self.get_node_config(current_node_id)
            if not node:
                yield event.run_error(
                    message=f"Node not found: {current_node_id}",
                    code="NODE_NOT_FOUND",
                )
                raise NodeExecutionError("NODE_NOT_FOUND", args={"node_id": current_node_id})

            # 循环计数与超限检测
            visit_count[current_node_id] = visit_count.get(current_node_id, 0) + 1
            force_fallback = self._check_cycle_limit(current_node_id, visit_count)
            if force_fallback:
                current_node_id = self._resolve_next_node(current_node_id, context, force_fallback=True)
                continue

            context.current_node = current_node_id
            node_type: str = node.get("type", "")

            if node_type == "message":
                msg_config = node.get("config", {})
                context.trace.record_node_start(current_node_id, "message", inputs=msg_config)

                # message 节点增加 STEP 事件
                step_evt = event.step_started(step_name=f"message:{current_node_id}")
                step_evt["_node_id"] = current_node_id
                step_evt["_label"] = msg_config.get("label")
                step_evt["_echo"] = msg_config.get("echo", True)
                yield step_evt

                async for evt in self._emit_message(node, context):
                    evt["_node_id"] = current_node_id
                    evt["_label"] = msg_config.get("label")
                    evt["_echo"] = msg_config.get("echo", True)
                    yield evt

                # 存储 message 输出
                template = msg_config.get("content", "")
                text = template.format_map(_SafeFormatDict(context.variables))
                context.set_output(current_node_id, {"result": text})

                fin_evt = event.step_finished(step_name=f"message:{current_node_id}")
                fin_evt["_node_id"] = current_node_id
                fin_evt["_label"] = msg_config.get("label")
                fin_evt["_echo"] = msg_config.get("echo", True)
                yield fin_evt

                context.trace.record_node_end(current_node_id, outputs={"result": text})
                current_node_id = self._resolve_next_node(current_node_id, context)

            elif node_type == "parallel":
                config = node.get("config", {})
                branches = config.get("branches", [])

                context.trace.record_node_start(current_node_id, "parallel", inputs=config)

                step_evt = event.step_started(step_name=f"parallel:{current_node_id}")
                step_evt["_node_id"] = current_node_id
                step_evt["_label"] = None
                step_evt["_echo"] = False
                yield step_evt

                parallel_qid = context.trace._qualify_id(current_node_id)

                async def _exec_branch(branch_id: str, _parent_qid: str = parallel_qid) -> None:
                    branch_node = self.get_node_config(branch_id)
                    if not branch_node:
                        return
                    branch_type = branch_node.get("type", "")
                    branch_config = branch_node.get("config", {})
                    branch_handler = self._node_handlers.get(branch_type)
                    if not branch_handler:
                        return

                    context.trace.record_node_start(
                        branch_id,
                        branch_type,
                        inputs=branch_config.get("inputs", {}),
                        parent_id=_parent_qid,
                    )
                    context.current_node = branch_id
                    try:
                        result = await branch_handler.execute(branch_node, context)
                        context.set_output(branch_id, result)
                        context.trace.record_node_end(branch_id, outputs=result)
                    except Exception as e:
                        context.trace.record_node_end(branch_id, error=str(e))
                        raise

                await asyncio.gather(*[_exec_branch(bid) for bid in branches])
                merged: dict[str, Any] = {}
                for bid in branches:
                    branch_result = context.outputs.get(bid, {})
                    if isinstance(branch_result, dict):
                        merged.update(branch_result)
                context.set_output(current_node_id, merged)

                fin_evt = event.step_finished(step_name=f"parallel:{current_node_id}")
                fin_evt["_node_id"] = current_node_id
                fin_evt["_label"] = None
                fin_evt["_echo"] = False
                yield fin_evt

                context.trace.record_node_end(current_node_id, outputs=merged)
                current_node_id = self._resolve_next_node(current_node_id, context)

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

                context.trace.record_node_start(current_node_id, "iteration", inputs=config)

                step_evt = event.step_started(step_name=f"iteration:{current_node_id}")
                step_evt["_node_id"] = current_node_id
                step_evt["_label"] = None
                step_evt["_echo"] = False
                yield step_evt

                for idx, item in enumerate(items):
                    context.set_variable(item_var, item)
                    context.set_variable(index_var, idx)
                    for body_node_id in body_nodes:
                        body_node = self.get_node_config(body_node_id)
                        if not body_node:
                            continue
                        body_type = body_node.get("type", "")
                        body_config = body_node.get("config", {})
                        body_handler = self._node_handlers.get(body_type)
                        if not body_handler:
                            continue

                        context.trace.record_node_start(
                            body_node_id,
                            body_type,
                            inputs=body_config.get("inputs", {}),
                            parent_id=context.trace._qualify_id(current_node_id),
                            iteration_index=idx,
                        )
                        body_result = await body_handler.execute(body_node, context)
                        context.set_output(body_node_id, body_result)
                        # 收集 body 节点产生的 execution_records
                        body_tool_calls = context.pop_execution_records()
                        context.trace.record_node_end(
                            body_node_id,
                            outputs=body_result,
                            tool_calls=body_tool_calls if body_tool_calls else None,
                        )
                    if body_nodes:
                        results.append(context.outputs.get(body_nodes[-1]))

                iteration_output = {"results": results}
                context.set_output(current_node_id, iteration_output)

                fin_evt = event.step_finished(step_name=f"iteration:{current_node_id}")
                fin_evt["_node_id"] = current_node_id
                fin_evt["_label"] = None
                fin_evt["_echo"] = False
                yield fin_evt

                context.trace.record_node_end(current_node_id, outputs=iteration_output)
                current_node_id = self._resolve_next_node(current_node_id, context)

            elif node_type in self._node_handlers:
                # 获取 resolved inputs 用于 trace
                config = node.get("config", {})
                handler = self._node_handlers[node_type]
                resolved_inputs = handler.resolve_inputs(config, context)

                context.trace.record_node_start(
                    current_node_id,
                    node_type,
                    inputs=resolved_inputs,
                    label=config.get("label"),
                )

                async for evt in self._execute_node_with_retry(node, context, current_node_id):
                    yield evt

                # 收集 agent 节点存放的 tool_calls 或通用 execution_records
                tool_calls = context.get_variable("_last_node_tool_calls")
                if tool_calls is None:
                    tool_calls = context.pop_execution_records() or None
                else:
                    context.set_variable("_last_node_tool_calls", None)

                # 获取可选的 messages（agent 节点）
                messages = None
                if node_type == "agent" and context.get_variable("_capture_messages"):
                    agent_name = config.get("agent_name", "")
                    messages = context.get_messages(agent_name) if agent_name else None

                context.trace.record_node_end(
                    current_node_id,
                    outputs=context.outputs.get(current_node_id),
                    tool_calls=tool_calls if tool_calls else None,
                    messages=messages,
                )
                current_node_id = self._resolve_next_node(current_node_id, context)

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
