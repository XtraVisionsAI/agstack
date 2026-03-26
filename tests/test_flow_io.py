#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Flow 引擎统一输入输出测试"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agstack.llm.flow.context import FlowContext
from agstack.llm.flow.flow import Flow
from agstack.llm.flow.tool import Tool


# ── FlowContext ──


class TestFlowContextOutputs:
    """context.outputs 和 set_output 测试"""

    def test_set_output_and_read(self):
        ctx = FlowContext()
        ctx.set_output("node_a", {"result": "hello"})
        assert ctx.outputs["node_a"] == {"result": "hello"}

    def test_outputs_preserve_type(self):
        ctx = FlowContext()
        data = {"docs": [{"title": "a"}, {"title": "b"}], "count": 2}
        ctx.set_output("search", data)
        assert ctx.outputs["search"] is data
        assert isinstance(ctx.outputs["search"]["docs"], list)

    def test_no_node_results_attribute(self):
        ctx = FlowContext()
        assert not hasattr(ctx, "node_results") or "node_results" not in ctx.__dataclass_fields__


class TestResolveReference:
    """$v. 和 $o. 引用语法测试"""

    def test_variable_ref(self):
        ctx = FlowContext(variables={"language": "zh", "user_id": "u123"})
        assert ctx.resolve_reference("$v.language") == "zh"
        assert ctx.resolve_reference("$v.user_id") == "u123"

    def test_variable_ref_missing(self):
        ctx = FlowContext()
        assert ctx.resolve_reference("$v.missing") is None

    def test_output_ref_simple(self):
        ctx = FlowContext()
        ctx.set_output("search", {"docs": [1, 2, 3], "count": 3})
        assert ctx.resolve_reference("$o.search") == {"docs": [1, 2, 3], "count": 3}

    def test_output_ref_field(self):
        ctx = FlowContext()
        ctx.set_output("search", {"docs": [1, 2, 3], "count": 3})
        assert ctx.resolve_reference("$o.search.count") == 3

    def test_output_ref_nested(self):
        ctx = FlowContext()
        ctx.set_output("search", {"meta": {"total": 100, "page": 1}})
        assert ctx.resolve_reference("$o.search.meta.total") == 100

    def test_output_ref_missing_node(self):
        ctx = FlowContext()
        assert ctx.resolve_reference("$o.nonexistent.field") is None

    def test_output_ref_missing_field(self):
        ctx = FlowContext()
        ctx.set_output("node_a", {"result": "hi"})
        assert ctx.resolve_reference("$o.node_a.nonexistent") is None

    def test_literal_value(self):
        ctx = FlowContext()
        assert ctx.resolve_reference("hello world") == "hello world"
        assert ctx.resolve_reference("123") == "123"

    def test_non_string_passthrough(self):
        ctx = FlowContext()
        assert ctx.resolve_reference(42) == 42
        assert ctx.resolve_reference(None) is None
        assert ctx.resolve_reference(["a", "b"]) == ["a", "b"]


# ── Tool ──


class TestTool:
    """Tool 签名和返回值测试"""

    def test_tool_function_receives_inputs(self):
        captured = {}

        def my_fn(context, inputs):
            captured.update(inputs)
            return {"status": "ok"}

        tool = Tool(name="test", description="test", function=my_fn)
        result = asyncio.get_event_loop().run_until_complete(
            tool.execute_async(FlowContext(), inputs={"query": "hello"})
        )
        assert result.success
        assert result.result == {"status": "ok"}
        assert captured == {"query": "hello"}

    def test_tool_default_empty_inputs(self):
        def my_fn(context, inputs):
            return {"received": inputs}

        tool = Tool(name="test", description="test", function=my_fn)
        result = asyncio.get_event_loop().run_until_complete(tool.execute_async(FlowContext()))
        assert result.success
        assert result.result == {"received": {}}

    def test_tool_run_returns_dict(self):
        def my_fn(context, inputs):
            return {"value": 42}

        tool = Tool(name="test", description="test", function=my_fn)
        result = asyncio.get_event_loop().run_until_complete(tool.run(FlowContext()))
        assert result == {"value": 42}

    def test_tool_error_returns_empty_dict(self):
        def my_fn(context, inputs):
            raise ValueError("boom")

        tool = Tool(name="test", description="test", function=my_fn)
        result = asyncio.get_event_loop().run_until_complete(tool.execute_async(FlowContext()))
        assert not result.success
        assert result.result == {}
        assert result.error is not None and "boom" in result.error


# ── _extract_route_key ──


class TestExtractRouteKey:
    """路由键提取测试"""

    def test_dict_with_choice(self):
        assert Flow._extract_route_key({"choice": "qa"}) == "qa"

    def test_dict_without_choice(self):
        assert Flow._extract_route_key({"result": "hello"}) == "done"

    def test_dict_empty(self):
        assert Flow._extract_route_key({}) == "done"

    def test_non_dict(self):
        assert Flow._extract_route_key("some string") == "done"
        assert Flow._extract_route_key(None) == "done"
        assert Flow._extract_route_key(42) == "done"

    def test_choice_as_non_string(self):
        assert Flow._extract_route_key({"choice": 123}) == "123"


# ── NodeHandler.resolve_inputs ──


class TestNodeHandlerResolveInputs:
    """NodeHandler.resolve_inputs 测试"""

    def test_resolve_with_refs(self):
        from agstack.llm.flow.nodes.base import NodeHandler

        handler = NodeHandler()
        ctx = FlowContext(variables={"lang": "zh"})
        ctx.set_output("node_a", {"text": "hello"})

        config = {
            "inputs": {
                "language": "$v.lang",
                "content": "$o.node_a.text",
                "literal": "fixed value",
            }
        }
        resolved = handler.resolve_inputs(config, ctx)
        assert resolved == {"language": "zh", "content": "hello", "literal": "fixed value"}


# ── PythonNodeHandler ──


class TestPythonNodeHandler:
    """Python 节点返回 dict 测试"""

    def test_returns_dict_not_json_string(self):
        from agstack.llm.flow.nodes.python_node import PythonNodeHandler

        handler = PythonNodeHandler()
        ctx = FlowContext()
        node = {
            "id": "py1",
            "type": "python",
            "config": {
                "code": "def main(**kwargs):\n    return {'sum': kwargs.get('a', 0) + kwargs.get('b', 0)}",
                "inputs": {},
            },
        }
        result = asyncio.get_event_loop().run_until_complete(handler.execute(node, ctx))
        assert isinstance(result, dict)
        assert result == {"sum": 0}

    def test_with_inputs_ref(self):
        from agstack.llm.flow.nodes.python_node import PythonNodeHandler

        handler = PythonNodeHandler()
        ctx = FlowContext()
        ctx.set_output("prev", {"value": 10})
        node = {
            "id": "py2",
            "type": "python",
            "config": {
                "code": "def main(x=0, **kwargs):\n    return {'doubled': x * 2}",
                "inputs": {"x": "$o.prev.value"},
            },
        }
        result = asyncio.get_event_loop().run_until_complete(handler.execute(node, ctx))
        assert result == {"doubled": 20}


# ── DetectNodeHandler ──


class TestDetectNodeHandler:
    """Detect 节点返回 {"choice": ...} 测试"""

    @patch("agstack.llm.flow.nodes.detect_node.get_llm_client")
    def test_returns_choice_dict(self, mock_get_client):
        from agstack.llm.flow.nodes.detect_node import DetectNodeHandler

        # mock LLM response
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = '{"result": "qa"}'
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        handler = DetectNodeHandler()
        ctx = FlowContext()
        node = {
            "id": "detect1",
            "type": "detect",
            "config": {
                "instruction": "classify",
                "options": ["qa", "chitchat"],
                "inputs": {"query": "$v.user_query"},
            },
        }
        ctx.variables["user_query"] = "What is Python?"

        result = asyncio.get_event_loop().run_until_complete(handler.execute(node, ctx))
        assert isinstance(result, dict)
        assert result == {"choice": "qa"}

    @patch("agstack.llm.flow.nodes.detect_node.get_llm_client")
    def test_dynamic_instruction_and_options(self, mock_get_client):
        from agstack.llm.flow.nodes.detect_node import DetectNodeHandler

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = '{"result": "billing"}'
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        handler = DetectNodeHandler()
        ctx = FlowContext(
            variables={
                "my_instruction": "classify ticket type",
                "my_options": ["billing", "technical", "general"],
            }
        )
        node = {
            "id": "detect1",
            "type": "detect",
            "config": {
                "inputs": {
                    "query": "$v.user_query",
                    "instruction": "$v.my_instruction",
                    "options": "$v.my_options",
                },
            },
        }
        ctx.variables["user_query"] = "I was charged twice"
        result = asyncio.get_event_loop().run_until_complete(handler.execute(node, ctx))
        assert result == {"choice": "billing"}

    @patch("agstack.llm.flow.nodes.detect_node.get_llm_client")
    def test_dynamic_model_and_temperature(self, mock_get_client):
        from agstack.llm.flow.nodes.detect_node import DetectNodeHandler

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = '{"result": "qa"}'
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        handler = DetectNodeHandler()
        ctx = FlowContext(variables={"chosen_model": "qwen2.5-72b", "temp": 0.1})
        node = {
            "id": "detect2",
            "type": "detect",
            "config": {
                "options": ["qa", "chitchat"],
                "inputs": {
                    "query": "hello",
                    "model": "$v.chosen_model",
                    "temperature": "$v.temp",
                },
            },
        }
        result = asyncio.get_event_loop().run_until_complete(handler.execute(node, ctx))
        call_args = mock_client.chat.call_args
        assert call_args.kwargs["model"] == "qwen2.5-72b"
        assert call_args.kwargs["temperature"] == 0.1
        assert result == {"choice": "qa"}


# ── LLMChatNodeHandler ──


class TestLLMChatNodeHandler:
    """LLM Chat 节点测试"""

    @patch("agstack.llm.flow.nodes.llm_chat_node.get_llm_client")
    def test_returns_dict(self, mock_get_client):
        from agstack.llm.flow.nodes.llm_chat_node import LLMChatNodeHandler

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Hello!"
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=5, completion_tokens=3, total_tokens=8)

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        handler = LLMChatNodeHandler()
        ctx = FlowContext()
        node = {
            "id": "chat1",
            "type": "llm_chat",
            "config": {
                "prompt": "Say hello",
                "inputs": {},
            },
        }
        result = asyncio.get_event_loop().run_until_complete(handler.execute(node, ctx))
        assert isinstance(result, dict)
        assert result == {"result": "Hello!"}

    def test_build_prompt_json_dumps_non_str(self):
        from agstack.llm.flow.nodes.llm_chat_node import LLMChatNodeHandler

        handler = LLMChatNodeHandler()
        resolved = {"items": ["a", "b", "c"], "count": 3}
        result = handler._build_prompt("Items: {items}, Count: {count}", resolved)
        assert '["a", "b", "c"]' in result
        assert "3" in result

    def test_build_prompt_str_passthrough(self):
        from agstack.llm.flow.nodes.llm_chat_node import LLMChatNodeHandler

        handler = LLMChatNodeHandler()
        resolved = {"name": "Alice"}
        result = handler._build_prompt("Hello {name}!", resolved)
        assert result == "Hello Alice!"

    @patch("agstack.llm.flow.nodes.llm_chat_node.get_llm_client")
    def test_system_prompt_variable_substitution(self, mock_get_client):
        from agstack.llm.flow.nodes.llm_chat_node import LLMChatNodeHandler

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "OK"
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=5, completion_tokens=1, total_tokens=6)

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        handler = LLMChatNodeHandler()
        ctx = FlowContext()
        node = {
            "id": "chat2",
            "type": "llm_chat",
            "config": {
                "prompt": "test",
                "system_prompt": "You speak {lang}",
                "inputs": {"lang": "$v.language"},
            },
        }
        ctx.variables["language"] = "Chinese"
        asyncio.get_event_loop().run_until_complete(handler.execute(node, ctx))

        # Verify system prompt was built with variable substitution
        call_args = mock_client.chat.call_args
        messages = call_args.kwargs["messages"]
        system_msg = [m for m in messages if m["role"] == "system"]
        assert len(system_msg) == 1
        assert system_msg[0]["content"] == "You speak Chinese"


# ── Flow routing ──


class TestFlowRouting:
    """Flow 路由和 loop break 测试"""

    def test_resolve_next_node_with_condition(self):
        flow = Flow(
            flow_id="test",
            name="test",
            edges=[
                {"source": "detect1", "condition": "qa", "target": "qa_node"},
                {"source": "detect1", "condition": "chitchat", "target": "chat_node"},
                {"source": "detect1", "condition": "done", "target": "end_node"},
            ],
        )
        assert flow._resolve_next_node("detect1", "qa") == "qa_node"
        assert flow._resolve_next_node("detect1", "chitchat") == "chat_node"
        assert flow._resolve_next_node("detect1", "done") == "end_node"

    def test_route_key_from_detect_result(self):
        """detect 返回 {"choice": "qa"} 应该正确路由"""
        detect_result = {"choice": "qa"}
        route_key = Flow._extract_route_key(detect_result)
        assert route_key == "qa"

    def test_route_key_from_normal_result(self):
        """普通节点（无 choice）应该路由到 done"""
        normal_result = {"result": "some text"}
        route_key = Flow._extract_route_key(normal_result)
        assert route_key == "done"


# ── Full data flow integration ──


class TestDataFlowIntegration:
    """完整数据流集成测试（不涉及 LLM 调用）"""

    def test_python_to_python_via_outputs(self):
        """python 节点 → outputs → 下游 python 节点引用"""
        from agstack.llm.flow.nodes.python_node import PythonNodeHandler

        handler = PythonNodeHandler()
        ctx = FlowContext()

        # 第一个 python 节点
        node1 = {
            "id": "step1",
            "type": "python",
            "config": {
                "code": "def main(**kwargs):\n    return {'items': [1, 2, 3], 'total': 6}",
                "inputs": {},
            },
        }
        result1 = asyncio.get_event_loop().run_until_complete(handler.execute(node1, ctx))
        ctx.set_output("step1", result1)

        # 第二个 python 节点引用第一个的输出
        node2 = {
            "id": "step2",
            "type": "python",
            "config": {
                "code": "def main(total=0, **kwargs):\n    return {'doubled': total * 2}",
                "inputs": {"total": "$o.step1.total"},
            },
        }
        result2 = asyncio.get_event_loop().run_until_complete(handler.execute(node2, ctx))
        assert result2 == {"doubled": 12}

    def test_variables_and_outputs_coexist(self):
        """$v. 和 $o. 可以在同一个 inputs 中共存"""
        from agstack.llm.flow.nodes.python_node import PythonNodeHandler

        handler = PythonNodeHandler()
        ctx = FlowContext(variables={"multiplier": 3})
        ctx.set_output("prev", {"value": 10})

        node = {
            "id": "compute",
            "type": "python",
            "config": {
                "code": "def main(value=0, multiplier=1, **kwargs):\n    return {'result': value * multiplier}",
                "inputs": {"value": "$o.prev.value", "multiplier": "$v.multiplier"},
            },
        }
        result = asyncio.get_event_loop().run_until_complete(handler.execute(node, ctx))
        assert result == {"result": 30}
