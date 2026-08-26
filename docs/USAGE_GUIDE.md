# AgStack Usage Guide

> **Version**: 1.0.0  
> **Last Updated**: 2026-01-27  
> **Purpose**: 指导外部开发者正确使用 AgStack 构建应用

## 📋 Table of Contents

- [1. Quick Start](#1-quick-start)
- [2. Core Concepts](#2-core-concepts)
- [3. LLM Flow System](#3-llm-flow-system)
- [4. Registry & Factory](#4-registry--factory)
- [5. Schema & Models](#5-schema--models)
- [6. Error Handling](#6-error-handling)
- [7. Best Practices](#7-best-practices)

---

## 1. Quick Start

### 1.1 Installation

```bash
pip install agstack
```

**Requirements**:
- Python >= 3.12
- FastAPI (如果使用 Web 功能)
- Pydantic >= 2.12.4

### 1.2 Basic Example

```python
from agstack.llm.flow import (
    Agent,
    Tool,
    Flow,
    FlowContext,
    registry,
    create_tool
)

# 1. 定义工具
class MyTool(Tool):
    def __init__(self):
        super().__init__(
            name="my_tool",
            description="My custom tool",
            function=self.execute
        )
    
    async def execute(self, context: FlowContext):
        return "Tool result"

# 2. 注册工具
registry.register_tool("my_tool", MyTool)

# 3. 使用工具
context = FlowContext(session_id="test")
tool = create_tool("my_tool")
result = await tool.run(context)
```

---

## 2. Core Concepts

### 2.1 项目结构

```
agstack/
├── schema.py          # 数据模型基类
├── registry.py        # 全局注册中心
├── exceptions.py      # 异常定义
├── llm/              # LLM 相关功能
│   ├── client.py     # LLM 客户端
│   ├── flow/         # Flow 执行框架
│   │   ├── agent.py  # Agent 定义
│   │   ├── tool.py   # Tool 定义
│   │   ├── flow.py   # Flow 编排
│   │   ├── context.py # 执行上下文
│   │   ├── registry.py # Flow 注册中心
│   │   └── factory.py  # 工厂函数
│   └── ...
├── fastapi/          # FastAPI 集成
├── infra/            # 基础设施
│   ├── db/           # 数据库
│   ├── es/           # Elasticsearch
│   └── mq/           # 消息队列
└── security/         # 安全相关
```

### 2.2 核心组件

| 组件 | 作用 | 导入 |
|------|------|------|
| `BaseSchema` | Pydantic 模型基类 | `from agstack.schema import BaseSchema` |
| `registry` | 全局注册中心 | `from agstack.llm.flow import registry` |
| `Agent` | LLM 代理 | `from agstack.llm.flow import Agent` |
| `Tool` | 工具定义 | `from agstack.llm.flow import Tool` |
| `Flow` | 流程编排 | `from agstack.llm.flow import Flow` |

---

## 3. LLM Flow System

### 3.1 Tool (工具)

工具是可以被 Agent 调用的函数。

**创建工具**:

```python
from agstack.llm.flow import Tool, FlowContext

class WebSearchTool(Tool):
    def __init__(self):
        super().__init__(
            name="web_search",
            description="Search the web",
            function=self.search,
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }
        )
    
    async def search(self, context: FlowContext):
        query = context.get_variable("query")
        # 实现搜索逻辑
        return f"Search results for: {query}"
```

**注册工具**:

```python
from agstack.llm.flow import registry

registry.register_tool("web_search", WebSearchTool)
```

### 3.2 Agent (代理)

Agent 是调用 LLM 并可以使用工具的智能代理。

**创建 Agent**:

```python
from agstack.llm.flow import Agent, FlowContext

class MyAgent(Agent):
    def __init__(self, model="gpt-4"):
        self.model = model
        self.tools = []
    
    async def run(self, context: FlowContext):
        # 实现 Agent 逻辑
        prompt = context.get_variable("prompt")
        # 调用 LLM
        response = await self.call_llm(prompt)
        return response
```

**注册 Agent**:

```python
registry.register_agent("my_agent", lambda: MyAgent(model="gpt-4"))
```

**max_turns 耗尽行为**（1.26.0+）:

Agent 的 tool-use 循环打满 `max_turns` 时不再静默截断，行为由 `on_max_turns` 控制：

- `"finalize"`（默认）：发出 `CUSTOM` 事件 `agent_max_turns`（value: `{"agentName", "maxTurns"}`），
  以 `TEXT_MESSAGE_END` 闭合消息流，并把最后一轮已生成的部分文本作为降级输出写入
  `context.outputs[agent_name]`，附带 `"truncated": True` 标记。
- `"error"`：发出 `RUN_ERROR` 事件后抛出 `AgentError("AGENT_MAX_TURNS_EXCEEDED")`。

下游可通过 `$o.<agent>.truncated` 判断输出是否被截断。

### 3.3 Flow (流程)

Flow 用于编排多个 Agent 和 Tool 的执行。

**创建 Flow**:

```python
from agstack.llm.flow import Flow, FlowContext

flow = Flow(
    flow_id="my_flow",
    name="My Flow",
    nodes=[
        {
            "id": "step1",
            "type": "tool",
            "config": {
                "tool_name": "web_search",
                "parameters": {"query": "Python"}
            }
        },
        {
            "id": "step2",
            "type": "agent",
            "config": {
                "agent_name": "my_agent",
                "parameters": {"prompt": "Summarize"}
            }
        }
    ]
)

# 执行 Flow
context = FlowContext(session_id="test")
result = await flow.run(context)
```

**run() 与 stream()**（1.26.0+）:

`run()` 是 `stream()` 的非事件消费包装，两条路径共享同一执行引擎：重试策略（`retry` 配置）、
执行轨迹（`FlowTrace`）、`output_mode: "append"`、iterator 状态清理的行为完全一致。
节点失败统一抛 `NodeExecutionError`（包装原始异常）。

**tool 节点失败语义**（1.26.0+）:

tool 节点默认失败即中断 flow（经重试耗尽后抛 `NodeExecutionError`）。可通过节点级
`on_error` 开关声明降级语义：

```python
{
    "id": "search",
    "type": "tool",
    "config": {
        "tool_name": "retrieval",
        "on_error": "continue",   # 默认 "raise"
    }
}
```

- `"raise"`（默认）：与既有行为一致，失败中断 flow。
- `"continue"`：不抛异常，节点输出 `{"success": False, "error": <错误信息>}`，flow 继续走边路由，
  条件边可用 `$o.search.success == false` 分流到兜底分支；错误同时记入该节点的 `NodeTrace.error`。

注意：与 agent 内部 tool-use 循环的容错（工具失败结果拼进上下文由模型自行应对）不同，
tool 节点的失败语义由 flow 作者通过 `on_error` 显式声明。

**per-node token 归因**（1.26.0+）:

引擎按节点执行前后 `context.usage` 的差值填充 `NodeTrace.usage`；无 LLM 调用的节点为 `None`。
已知限制：parallel 分支并发共享 context，用量整体归因到 parallel 容器节点，分支节点为 `None`；
iteration 的 body 节点串行执行，正常按差值归因（容器节点不重复归因）。token 计费口径仍走
LLM client 层的 usage 回调，与此无关。

### 3.4 FlowContext (上下文)

FlowContext 在执行过程中传递状态和数据。

```python
from agstack.llm.flow import FlowContext

context = FlowContext(session_id="user123")

# 设置变量
context.set_variable("query", "Python tutorial")

# 获取变量
query = context.get_variable("query")

# 添加消息
context.add_message("user", "Hello")

# 设置节点结果
context.set_node_result("step1", {"data": "result"})
```

---

## 4. Registry & Factory

### 4.1 Registry (注册中心)

**用途**: 管理所有组件的注册和创建。

**API**:

```python
from agstack.llm.flow import registry

# 注册
registry.register_tool("name", ToolClass)
registry.register_agent("name", AgentClass)
registry.register_flow("name", FlowClass)

# 创建（返回 None 如果不存在）
tool = registry.create_tool("name")
agent = registry.create_agent("name", param="value")
flow = registry.create_flow("name")

# 查询
tool_class = registry.get_tool_class("name")
tools = registry.list_tools()

# 批量创建
tools = registry.create_tools(["tool1", "tool2"])
```

### 4.2 Factory (工厂函数)

**用途**: 快速创建组件，失败时抛出异常。

```python
from agstack.llm.flow import create_tool, create_agent

# 创建工具（失败抛 RuntimeError）
tool = create_tool("web_search")
await tool.run(context)

# 创建 Agent
agent = create_agent("my_agent", model="gpt-4")
await agent.run(context)
```

**何时使用**:

| 场景 | 使用 | 原因 |
|------|------|------|
| 需要检查组件是否存在 | `registry.create_*()` | 返回 None，可以处理 |
| 确信组件已注册 | `create_*()` | 快速失败，代码简洁 |
| 批量创建 | `registry.create_tools()` | 支持批量操作 |

---

## 5. Schema & Models

### 5.1 BaseSchema

**用途**: 所有需要验证和序列化的数据模型的基类。

**特性**:
- 自动类型验证
- datetime 自动格式化（ISO 8601）
- UUID 自动转换为字符串
- `extra="ignore"` 容错
- 支持 ORM 对象

**使用**:

```python
from agstack.schema import BaseSchema
from datetime import datetime
from pydantic import Field

class MyModel(BaseSchema):
    id: str
    name: str
    created_at: datetime = Field(default_factory=datetime.now)
    data: dict = Field(default_factory=dict)

# 创建实例
model = MyModel(id="123", name="Test")

# 序列化
data = model.model_dump()
# {'id': '123', 'name': 'Test', 'created_at': '2026-01-27T12:34:56+0800', 'data': {}}

# 反序列化
model = MyModel.model_validate(data)
```

### 5.2 内部数据类

对于不需要验证和序列化的内部数据，使用 `dataclass`：

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class InternalResult:
    """内部使用的结果对象"""
    success: bool
    data: Any
    error: str | None = None
```

---

## 6. Error Handling

### 6.1 异常层次

```python
from agstack.exceptions import AppException
from agstack.llm.flow.exceptions import (
    FlowError,
    AgentError,
    ToolExecutionError,
    ModelError,
    FlowConfigError
)
```

### 6.2 捕获异常

```python
from agstack.llm.flow import create_tool
from agstack.llm.flow.exceptions import ToolExecutionError

try:
    tool = create_tool("my_tool")
    result = await tool.run(context)
except ToolExecutionError as e:
    print(f"Tool failed: {e}")
except RuntimeError as e:
    print(f"Tool not found: {e}")
```

### 6.3 自定义异常

```python
from agstack.llm.flow.exceptions import FlowError

class MyCustomError(FlowError):
    """自定义错误"""
    def __init__(self, message: str):
        super().__init__("CUSTOM_ERROR", 500, {"message": message})
```

---

## 7. Best Practices

### 7.1 组件注册

```python
# ✅ 推荐：在应用启动时注册所有组件
def register_components():
    from agstack.llm.flow import registry
    from .tools import WebSearchTool, CalculatorTool
    from .agents import ChatAgent
    
    registry.register_tool("web_search", WebSearchTool)
    registry.register_tool("calculator", CalculatorTool)
    registry.register_agent("chat", ChatAgent)

# 在应用入口调用
if __name__ == "__main__":
    register_components()
    # 启动应用
```

### 7.2 上下文管理

```python
# ✅ 推荐：复用上下文
context = FlowContext(session_id=user_id)

# 第一步
tool1 = create_tool("step1")
result1 = await tool1.run(context)

# 第二步（使用第一步的结果）
context.set_variable("previous_result", result1)
tool2 = create_tool("step2")
result2 = await tool2.run(context)
```

### 7.3 错误处理

```python
# ✅ 推荐：优雅处理错误
from agstack.llm.flow import registry
from agstack.llm.flow.exceptions import ToolExecutionError

async def safe_execute_tool(tool_name: str, context):
    tool = registry.create_tool(tool_name)
    if not tool:
        return {"error": f"Tool {tool_name} not found"}
    
    try:
        result = await tool.run(context)
        return {"success": True, "data": result}
    except ToolExecutionError as e:
        return {"error": str(e)}
```

### 7.4 类型提示

```python
# ✅ 推荐：使用类型提示
from typing import Optional
from agstack.llm.flow import Tool, FlowContext

async def create_and_run_tool(
    tool_name: str,
    context: FlowContext
) -> Optional[dict]:
    """创建并运行工具"""
    tool: Optional[Tool] = registry.create_tool(tool_name)
    if not tool:
        return None
    
    result = await tool.run(context)
    return {"result": result}
```

---

## 📚 Quick Reference

### 常用导入

```python
# Schema
from agstack.schema import BaseSchema

# Flow 核心
from agstack.llm.flow import (
    Agent,
    Tool,
    Flow,
    FlowContext,
    ToolResult,
    registry,
    create_tool,
    create_agent,
)

# 异常
from agstack.llm.flow.exceptions import (
    FlowError,
    AgentError,
    ToolExecutionError,
)

# 状态管理
from agstack.llm.flow import FlowState, Record, Status
```

### 常见模式

```python
# 1. 创建和注册工具
class MyTool(Tool):
    def __init__(self):
        super().__init__(name="my_tool", description="...", function=self.run)
    async def run(self, context): ...

registry.register_tool("my_tool", MyTool)

# 2. 使用工具（安全）
tool = registry.create_tool("my_tool")
if tool:
    result = await tool.run(context)

# 3. 使用工具（快速失败）
tool = create_tool("my_tool")
result = await tool.run(context)

# 4. 创建 Pydantic 模型
class MyModel(BaseSchema):
    field: str
    value: int

# 5. 流程编排
flow = Flow(
    flow_id="id",
    name="name",
    nodes=[
        {"id": "1", "type": "tool", "config": {...}},
        {"id": "2", "type": "agent", "config": {...}}
    ]
)
result = await flow.run(context)
```

---

## 🔗 Resources

- **Documentation**: (TBD)
- **GitHub**: (TBD)
- **Examples**: (TBD)
- **API Reference**: (TBD)

---

## ❓ FAQ

**Q: registry 和 factory 有什么区别？**

A: `registry.create_*()` 返回 None 如果组件不存在，需要检查；`create_*()` 失败时抛出异常，适合确信组件存在的场景。

**Q: 什么时候用 dataclass，什么时候用 BaseSchema？**

A: dataclass 用于内部数据传递（如 ToolResult）；BaseSchema 用于需要验证和序列化的实体（如 Record）。

**Q: 如何处理循环导入？**

A: 使用 `TYPE_CHECKING` 块进行类型提示导入：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .module import Class  # 仅类型检查时导入
```

**Q: 如何自定义 Agent？**

A: 继承 `Agent` 类并实现 `run()` 方法，然后注册到 registry。

---

**Note**: 本指南持续更新中，如有问题请参考源码或提交 Issue。
