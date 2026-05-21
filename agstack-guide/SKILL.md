---
name: agstack-guide
description: Comprehensive guide for building FastAPI applications with integrated LLM capabilities using the agstack framework. Use this skill when users need help with: (1) Creating LLM-powered agents with tool calling, (2) Building reusable tools for agents, (3) Orchestrating multi-step workflows with flows, (4) Setting up FastAPI apps with agstack, (5) Implementing AI-powered features like chatbots, automation, or multi-agent systems, (6) Understanding agstack's component registry, schemas, and infrastructure patterns, or (7) Any questions about how to use agstack framework features and best practices.
---

# AgStack Development Guide

AgStack is a production-ready Python framework for building FastAPI applications with integrated LLM capabilities. It provides a unified infrastructure for creating AI-powered applications with intelligent agents, tools, and orchestrated workflows.

## Quick Start

### Installation

```bash
pip install agstack
```

**Requirements**: Python >= 3.12

### Basic Setup

```python
from agstack.llm.client import setup_llm_client
from agstack.llm.flow import Agent, Tool, Flow, FlowContext, registry

# Configure LLM client
setup_llm_client(
    base_url="https://api.openai.com/v1",
    api_key="your-api-key"
)
```

## Core Concepts

AgStack has three primary building blocks:

1. **Agents**: LLM-powered intelligent components that can converse and use tools
2. **Tools**: Reusable functions that agents call to perform actions
3. **Flows**: Orchestration of agents and tools into multi-step workflows

All components use a **Registry Pattern** for management and **FlowContext** for state.

## Component Overview

### Agents

Create intelligent agents powered by LLMs:

```python
from agstack.llm.flow import Agent, registry

class ChatAgent(Agent):
    def __init__(self):
        super().__init__(
            name="chat_agent",
            instructions="You are a helpful assistant",
            model="gpt-4o",
            tools=[]  # Optional tools
        )

# Register agent (pass class, not instance)
registry.register_agent("chat", ChatAgent)

# Use agent
from agstack.llm.flow import FlowContext, create_agent

context = FlowContext(flow_id="my-session")
context.set_variable("query", "Hello!")

agent = create_agent("chat")
response = await agent.run(context)
```

**See [agents.md](references/agents.md) for detailed agent documentation.**

### Tools

Create reusable functions for agents:

```python
from agstack.llm.flow import Tool, FlowContext, registry

class GreetingTool(Tool):
    def __init__(self):
        super().__init__(
            name="greeting",
            description="Generate a personalized greeting",
            function=self.greet,
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                },
                "required": ["name"]
            }
        )

    async def greet(self, context: FlowContext, inputs: dict):
        name = inputs.get("name", "World")
        return {"message": f"Hello, {name}!"}

# Register tool
registry.register_tool("greeting", GreetingTool)
```

**See [tools.md](references/tools.md) for detailed tool documentation.**

### Flows

Orchestrate multi-step workflows:

```python
from agstack.llm.flow import Flow

flow = Flow(
    flow_id="my_workflow",
    name="Multi-step Workflow",
    nodes=[
        {
            "id": "step1",
            "type": "tool",
            "config": {
                "tool_name": "greeting",
                "inputs": {"name": "$v.user_name"}
            }
        },
        {
            "id": "step2",
            "type": "agent",
            "config": {
                "agent_name": "chat",
                "inputs": {"input": "$o.step1.message"}
            }
        }
    ]
)

# Run flow
context = FlowContext()
context.set_variable("user_name", "Alice")
result = await flow.run(context)
```

**See [flows.md](references/flows.md) for detailed flow documentation.**

## Development Workflow

### 1. Define Tools

Start by creating the tools your agents will need:

```python
# Define tools for specific actions
class WebSearchTool(Tool): ...
class DatabaseQueryTool(Tool): ...
class EmailSenderTool(Tool): ...

# Register all tools
registry.register_tool("web_search", WebSearchTool)
registry.register_tool("db_query", DatabaseQueryTool)
registry.register_tool("send_email", EmailSenderTool)
```

### 2. Create Agents

Build agents that use your tools:

```python
# Create agent with tools
tools = registry.create_tools(["web_search", "db_query"])

class ResearchAgent(Agent):
    def __init__(self):
        super().__init__(
            name="researcher",
            instructions="Use tools to research topics thoroughly",
            model="gpt-4o",
            tools=tools
        )

registry.register_agent("researcher", ResearchAgent)
```

### 3. Orchestrate with Flows

Combine agents and tools into workflows using edge-driven routing:

```python
research_flow = Flow(
    flow_id="research_flow",
    name="Research Workflow",
    nodes=[
        {
            "id": "search",
            "type": "tool",
            "config": {
                "tool_name": "web_search",
                "inputs": {"query": "$v.topic"}
            }
        },
        {
            "id": "analyze",
            "type": "agent",
            "config": {
                "agent_name": "researcher",
                "inputs": {"input": "$o.search.result"}
            }
        }
    ],
    edges=[
        {"source": "search", "target": "analyze"}
    ]
)
```

### 4. Execute

Run your components:

```python
# Direct agent execution
agent = create_agent("researcher")
response = await agent.run(context)

# Flow execution
result = await flow.run(context)

# Streaming execution
async for evt in agent.stream(context):
    # Process AG-UI events
    pass
```

## Key Patterns

### Registry Pattern

All components use centralized registration:

```python
from agstack.llm.flow import registry, create_agent, create_tool

# Register components (pass class or factory)
registry.register_agent("name", AgentClass)
registry.register_tool("name", ToolClass)
registry.register_flow("name", FlowClass)

# Safe creation (returns None if not found)
agent = registry.create_agent("name")

# Fast-fail creation (raises RuntimeError if not found)
agent = create_agent("name")
```

**See [registry.md](references/registry.md) for complete registry and factory pattern documentation.**

### FlowContext for State

FlowContext manages state across execution:

```python
context = FlowContext(flow_id="unique_id")

# Set input variables
context.set_variable("key", "value")

# Get variables
value = context.get_variable("key")
value = context.get_variable("key", default="default")

# Access node outputs (after flow execution)
result = context.outputs.get("node_id")

# Reference syntax for variable resolution
resolved = context.resolve_reference("$o.node_id.field")  # node output
resolved = context.resolve_reference("$v.key")            # variable

# Check state
messages = context.get_messages("agent_name")
usage = context.usage  # Usage dataclass
```

### Event-Driven Streaming

Use AG-UI protocol events for streaming:

```python
from agstack.llm.flow import EventType

async for evt in agent.stream(context):
    event_type = evt.get("type")

    if event_type == EventType.TEXT_MESSAGE_CONTENT:
        print(evt.get("delta"), end="")

    elif event_type == EventType.TOOL_CALL_START:
        tool_name = evt.get("toolCallName")
        print(f"\n[Calling: {tool_name}]")

    elif event_type == EventType.TEXT_MESSAGE_END:
        print("\n[Completed]")
```

## Data Models

### Always Use BaseSchema

```python
from agstack.schema import BaseSchema
from datetime import datetime
from pydantic import Field

class User(BaseSchema):
    id: str
    name: str
    created_at: datetime = Field(default_factory=datetime.now)
    metadata: dict = Field(default_factory=dict)

# Automatic serialization
user = User(id="123", name="Alice")
data = user.model_dump()
# {'id': '123', 'name': 'Alice', 'created_at': '2026-01-28T10:30:00+0800', ...}
```

**Never use `BaseModel`** - always use `BaseSchema` for Pydantic models in agstack.

## Error Handling

```python
from agstack.llm.flow.exceptions import (
    FlowError,
    ToolExecutionError,
    AgentError,
    NodeExecutionError,
)

try:
    tool = create_tool("my_tool")
    result = await tool.run(context, {"param": "value"})
except ToolExecutionError as e:
    print(f"Tool error: {e.error_key}")
    print(f"Details: {e.arguments}")
except RuntimeError as e:
    print(f"Component not found: {e}")
except AgentError as e:
    print(f"Agent error: {e.error_key}")
except FlowError as e:
    print(f"Flow error: {e.error_key}")
```

## Code Standards

### Import Rules

**Always use relative imports within the project**:

```python
# Correct
from ...schema import BaseSchema
from ..client import get_llm_client

# Incorrect
from agstack.schema import BaseSchema
from agstack.llm.client import get_llm_client
```

### Type Hints

Always add type hints:

```python
from agstack.llm.flow import FlowContext

async def my_function(context: FlowContext, inputs: dict[str, str]) -> dict[str, str]:
    result: str = inputs.get("key", "")
    return {"result": result}
```

### Code Quality

- **Line length**: Max 120 characters
- **Python version**: 3.12+
- **Formatter**: ruff
- **Type checker**: pyright

## Common Use Cases

### Chatbot

```python
# Simple conversational agent
class ChatBot(Agent):
    def __init__(self):
        super().__init__(
            name="chatbot",
            instructions="You are a friendly assistant",
            model="gpt-4o"
        )

# Multi-turn conversation
context = FlowContext()

context.set_variable("query", "What is Python?")
response1 = await chatbot.run(context)

context.set_variable("query", "Can you show an example?")
response2 = await chatbot.run(context)  # Remembers previous context
```

### Task Automation

```python
# Automation workflow with edge-driven routing
automation_flow = Flow(
    flow_id="automation",
    name="Task Automation",
    nodes=[
        {
            "id": "classify",
            "type": "agent",
            "config": {
                "agent_name": "classifier",
                "inputs": {"input": "$v.task"}
            }
        },
        {
            "id": "execute",
            "type": "tool",
            "config": {
                "tool_name": "task_executor",
                "inputs": {
                    "task_type": "$o.classify.result",
                    "task_data": "$v.task"
                }
            }
        }
    ],
    edges=[
        {"source": "classify", "target": "execute"}
    ]
)
```

### Multi-Agent System

```python
# Collaborative agents
research_flow = Flow(
    flow_id="research_system",
    name="Multi-Agent Research",
    nodes=[
        {"id": "research", "type": "agent", "config": {"agent_name": "researcher", "inputs": {"input": "$v.topic"}}},
        {"id": "analyze", "type": "agent", "config": {"agent_name": "analyst", "inputs": {"input": "$o.research.result"}}},
        {"id": "summarize", "type": "agent", "config": {"agent_name": "writer", "inputs": {"input": "$o.analyze.result"}}}
    ],
    edges=[
        {"source": "research", "target": "analyze"},
        {"source": "analyze", "target": "summarize"}
    ]
)
```

## FastAPI Integration

### Basic Setup

```python
from agstack.fastapi import setup_fastapi
from agstack.llm.flow import FlowContext, create_agent

app = setup_fastapi(
    title="My App",
    version="1.0.0",
    debug=True,
    static_url="/static"
)

@app.get("/")
async def root():
    return {"message": "Hello from agstack"}

@app.post("/chat")
async def chat(request: ChatRequest):
    context = FlowContext()
    context.set_variable("query", request.message)

    agent = create_agent("chatbot")
    response = await agent.run(context)

    return {"response": response.get("result", "")}
```

### Streaming Endpoints

```python
from agstack.fastapi.sse import EventSourceResponse
from agstack.llm.flow import EventType

@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    context = FlowContext()
    context.set_variable("query", request.message)

    agent = create_agent("chatbot")

    async def event_generator():
        async for evt in agent.stream(context):
            if evt.get("type") == EventType.TEXT_MESSAGE_CONTENT:
                yield {"data": evt.get("delta")}

    return EventSourceResponse(event_generator())
```

## Infrastructure

AgStack includes production-ready infrastructure:

### Database (PostgreSQL)

```python
from agstack.infra.db import get_db_pool

pool = await get_db_pool()
async with pool.acquire() as conn:
    result = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
```

### Elasticsearch

```python
from agstack.infra.es import get_es_client

es = get_es_client()
results = await es.search(index="documents", query={"match": {"content": "search term"}})
```

### Message Queue (RabbitMQ)

```python
from agstack.infra.mq import get_mq_connection

connection = await get_mq_connection()
# Publish/consume messages
```

## Configuration

AgStack uses TOML config files with environment variable overrides (via `agstack.config.manager`):

**config.toml**:
```toml
[app]
name = "my-app"
debug = false

[database]
host = "localhost"
port = 5432
```

**Environment override**:
```bash
export APP_DATABASE_HOST="prod-db.example.com"
```

## Node Types

AgStack supports the following built-in node types for flows:

| Type | Description |
|------|-------------|
| `agent` | Multi-turn LLM agent with tool use loop |
| `tool` | Execute a registered tool |
| `llm_chat` | Single-turn LLM call (lighter than agent) |
| `python` | RestrictedPython sandboxed code execution |
| `switch` | Variable-based routing (zero LLM cost) |
| `subflow` | Execute another registered/inline flow |
| `detect` | Detection/classification node |
| `echo` | Pass-through echo node |
| `llm_embed` | Embedding generation |
| `llm_rerank` | Reranking node |
| `message` | Template text output (flow-level) |
| `parallel` | Parallel branch execution (flow-level) |
| `iteration` | Loop over items (flow-level) |

## Advanced Features

### FlowTrace (Structured Execution Trace)

FlowTrace provides a complete execution trace after flow completion:

```python
from agstack.llm.flow import FlowTrace, NodeTrace, EdgeTrace

context = FlowContext()
result = await flow.run(context)

# Access trace
trace: FlowTrace = context.trace
print(f"Duration: {trace.finished_at - trace.started_at}s")
print(f"Total usage: {trace.total_usage}")

# Inspect node traces
for node_trace in trace.nodes:
    print(f"  {node_trace.node_id}: {node_trace.duration_ms}ms")
```

### Edge-Driven Routing with Conditions

```python
flow = Flow(
    flow_id="conditional",
    name="Conditional Flow",
    nodes=[
        {"id": "classify", "type": "agent", "config": {"agent_name": "classifier", "inputs": {"input": "$v.query"}}},
        {"id": "handle_urgent", "type": "agent", "config": {"agent_name": "fast_agent", "inputs": {"input": "$v.query"}}},
        {"id": "handle_normal", "type": "agent", "config": {"agent_name": "standard_agent", "inputs": {"input": "$v.query"}}}
    ],
    edges=[
        {"source": "classify", "target": "handle_urgent", "condition": "$o.classify.result == urgent"},
        {"source": "classify", "target": "handle_normal"}  # fallback (no condition)
    ]
)
```

### Retry Policy

```python
{
    "id": "fragile_step",
    "type": "tool",
    "config": {
        "tool_name": "external_api",
        "inputs": {"url": "$v.url"},
        "retry": {
            "max_retries": 3,
            "delay": 1.0,
            "backoff": 2.0
        }
    }
}
```

### Cycle Limits

Prevent infinite loops in cyclic graphs:

```python
flow = Flow(
    flow_id="cyclic",
    name="Cyclic Flow",
    nodes=[...],
    edges=[...],
    cycle_limits={"refine": 5}  # node "refine" can run at most 5 times
)
```

### Custom Node Handlers

```python
from agstack.llm.flow import NodeHandler, register_node_handler

class MyCustomHandler(NodeHandler):
    node_type = "my_custom"

    async def execute(self, node: dict, context: FlowContext) -> Any:
        config = node.get("config", {})
        resolved = self.resolve_inputs(config, context)
        # Custom logic here
        return {"result": "done"}

register_node_handler("my_custom", MyCustomHandler())
```

### Tool Visibility (label & echo)

Control how tool execution appears to users:

```python
registry.register_tool("search", WebSearchTool, label="Searching...", echo=True)
registry.register_agent("analyst", AnalystAgent, label="Analyzing", echo=True)
```

## Best Practices

1. **Component Registration**: Register all components at application startup
2. **Session Management**: Use unique `flow_id` for each execution context
3. **Error Handling**: Always wrap execution in try-except blocks
4. **Type Safety**: Use type hints and BaseSchema for all data models
5. **Async First**: Use async/await for all I/O operations
6. **Clear Instructions**: Write specific, detailed agent instructions
7. **Tool Descriptions**: Write clear tool descriptions for LLM understanding
8. **Testing**: Test tools and agents independently before integration
9. **Monitoring**: Track token usage via `context.usage` and FlowTrace
10. **Variable References**: Use `$o.node_id.field` for outputs, `$v.key` for variables

## Troubleshooting

### Component Not Found

```python
# Problem: RuntimeError when creating component
agent = create_agent("my_agent")  # RuntimeError if not registered

# Solution: Ensure component is registered
registry.register_agent("my_agent", MyAgent)
```

### Tool Execution Fails

```python
# Problem: Tool raises ToolExecutionError
# Solution: Check function signature and error handling

class MyTool(Tool):
    def __init__(self):
        super().__init__(
            name="my_tool",
            description="Does something",
            function=self.execute,
            parameters={...}
        )

    async def execute(self, context: FlowContext, inputs: dict):
        param = inputs.get("param")
        if not param:
            raise ValueError("param is required")
        return {"status": "ok", "data": param}
```

### Flow Variable References

```python
# Variable reference syntax:
# $o.node_id.field     → context.outputs["node_id"]["field"]
# $v.key               → context.variables["key"]

# In node config:
"inputs": {"data": "$o.previous_node.result"}  # output reference
"inputs": {"name": "$v.user_name"}             # variable reference
```

## Next Steps

When helping users:

1. **Understand the goal**: What AI capability do they want to build?
2. **Start with tools**: Identify what actions agents need to perform
3. **Build agents**: Create agents with appropriate tools and instructions
4. **Orchestrate**: Use flows if multi-step workflows are needed
5. **Test incrementally**: Test each component before integration
6. **Integrate with FastAPI**: Add API endpoints for production use

## Reference Documentation

For detailed information on specific topics:

- **[agents.md](references/agents.md)**: Complete guide to creating and using agents
- **[tools.md](references/tools.md)**: Complete guide to building tools
- **[flows.md](references/flows.md)**: Complete guide to orchestrating flows
- **[registry.md](references/registry.md)**: Complete guide to registry pattern and component lifecycle
