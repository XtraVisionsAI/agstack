# Tools Guide

## What are Tools?

Tools are reusable functions that agents can call to perform specific actions. They enable agents to interact with external systems, process data, or execute custom logic.

## Tool Anatomy

```python
from agstack.llm.flow import Tool, FlowContext

class MyTool(Tool):
    def __init__(self):
        super().__init__(
            name="my_tool",                    # Unique identifier
            description="What this tool does", # Clear description for LLM
            function=self.execute,             # Function to execute
            parameters={                       # JSON Schema for parameters
                "type": "object",
                "properties": {
                    "param1": {
                        "type": "string",
                        "description": "Parameter description"
                    }
                },
                "required": ["param1"]
            }
        )

    async def execute(self, context: FlowContext, inputs: dict):
        # inputs contains the parameters passed by the LLM or flow
        param1 = inputs.get("param1", "")
        result = f"Processed: {param1}"
        return {"output": result}
```

## Tool Function Signature

The tool function receives two arguments:

- `context: FlowContext` — execution context (access variables, outputs, usage)
- `inputs: dict[str, Any]` — parameters passed by the LLM (agent mode) or resolved from flow config

The function should return a `dict[str, Any]` on success, or raise an exception on failure.

## Creating Tools

### Simple Tool

```python
class GreetingTool(Tool):
    def __init__(self):
        super().__init__(
            name="greeting",
            description="Generate a personalized greeting message",
            function=self.greet,
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Person's name"}
                },
                "required": ["name"]
            }
        )

    async def greet(self, context: FlowContext, inputs: dict):
        name = inputs.get("name", "World")
        return {"message": f"Hello, {name}! Welcome to agstack."}
```

### Tool with Multiple Parameters

```python
class CalculatorTool(Tool):
    def __init__(self):
        super().__init__(
            name="calculator",
            description="Perform basic arithmetic operations",
            function=self.calculate,
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide"],
                        "description": "The operation to perform"
                    },
                    "a": {"type": "number", "description": "First number"},
                    "b": {"type": "number", "description": "Second number"}
                },
                "required": ["operation", "a", "b"]
            }
        )

    async def calculate(self, context: FlowContext, inputs: dict):
        op = inputs["operation"]
        a = inputs["a"]
        b = inputs["b"]

        if op == "add":
            return {"result": a + b}
        elif op == "subtract":
            return {"result": a - b}
        elif op == "multiply":
            return {"result": a * b}
        elif op == "divide":
            if b == 0:
                raise ValueError("Cannot divide by zero")
            return {"result": a / b}
```

### Tool with External API Call

```python
import httpx

class WeatherTool(Tool):
    def __init__(self, api_key: str):
        self.api_key = api_key
        super().__init__(
            name="get_weather",
            description="Get current weather for a location",
            function=self.fetch_weather,
            parameters={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City name or zip code"
                    }
                },
                "required": ["location"]
            }
        )

    async def fetch_weather(self, context: FlowContext, inputs: dict):
        location = inputs["location"]

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.weather.com/v1/current",
                params={"location": location, "key": self.api_key}
            )
            data = response.json()

        return {"location": location, "temp": data["temp"], "condition": data["condition"]}
```

### Tool with Database Access

```python
class UserLookupTool(Tool):
    def __init__(self, db_pool):
        self.db = db_pool
        super().__init__(
            name="lookup_user",
            description="Find user information by email or ID",
            function=self.lookup,
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User ID or email"}
                },
                "required": ["user_id"]
            }
        )

    async def lookup(self, context: FlowContext, inputs: dict):
        user_id = inputs["user_id"]

        query = "SELECT * FROM users WHERE id = $1 OR email = $1"
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(query, user_id)

        if not row:
            return {"found": False, "error": "User not found"}

        return {
            "found": True,
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
        }
```

## Advanced Tool Parameters

### label, echo, category, summary_fn, result_formatter

```python
class SearchTool(Tool):
    def __init__(self):
        super().__init__(
            name="search",
            description="Search for documents",
            function=self.search,
            parameters={...},
            label="Searching...",       # User-visible step name (implies echo=True)
            echo=True,                  # Forward TEXT_MESSAGE events to user
            category="retrieval",       # Tool category for organization
            summary_fn=self._summary,   # Generate user-facing summary
            result_formatter=self._fmt, # Custom LLM content formatting
        )

    def _summary(self, result: "ToolResult") -> str | None:
        if result.success:
            return f"Found {len(result.result.get('items', []))} results"
        return None

    def _fmt(self, result: "ToolResult") -> str:
        # Custom format for what the LLM sees as tool output
        return json.dumps(result.result, ensure_ascii=False)
```

## Registering Tools

Tools must be registered before use:

```python
from agstack.llm.flow import registry

# Register tool class (instantiated on create)
registry.register_tool("greeting", GreetingTool)
registry.register_tool("calculator", CalculatorTool)

# Register with factory function (for tools needing init params)
registry.register_tool("weather", lambda: WeatherTool(api_key="your_key"))

# Register with label/echo
registry.register_tool("search", SearchTool, label="Searching...", echo=True)
```

## Using Tools

### In Agents

```python
from agstack.llm.flow import Agent, registry

# Create tool list
tools = registry.create_tools(["greeting", "calculator"])

# Give tools to agent
class AssistantAgent(Agent):
    def __init__(self):
        super().__init__(
            name="assistant",
            instructions="Use the available tools to help users",
            model="gpt-4o",
            tools=tools
        )
```

### Directly in Code

```python
from agstack.llm.flow import FlowContext, create_tool

context = FlowContext()

# Execute tool with inputs
tool = create_tool("greeting")
result = await tool.run(context, {"name": "Alice"})
print(result)  # {"message": "Hello, Alice! Welcome to agstack."}
```

### In Flows

```python
from agstack.llm.flow import Flow

flow = Flow(
    flow_id="my_flow",
    name="Process Data",
    nodes=[
        {
            "id": "fetch_data",
            "type": "tool",
            "config": {
                "tool_name": "lookup_user",
                "inputs": {"user_id": "$v.user_id"}
            }
        },
        {
            "id": "greet_user",
            "type": "tool",
            "config": {
                "tool_name": "greeting",
                "inputs": {"name": "$o.fetch_data.name"}
            }
        }
    ],
    edges=[
        {"source": "fetch_data", "target": "greet_user"}
    ]
)
```

## Tool Execution Details

### ToolResult

When a tool executes, it produces a `ToolResult` dataclass:

```python
from agstack.llm.flow import ToolResult

# ToolResult fields:
# - name: str          — tool name
# - arguments: dict    — inputs passed to the tool
# - result: dict       — return value from the tool function
# - success: bool      — whether execution succeeded
# - error: str | None  — error message if failed
# - content: str | None — formatted content for LLM consumption
# - summary: str | None — user-facing summary (from summary_fn)
```

### execute_async vs run

- `tool.execute_async(context, inputs)` → returns `ToolResult` (full details, timing, records)
- `tool.run(context, inputs)` → returns `dict | None` (just the result dict, or None on failure)

## Parameter Schema

Tools use JSON Schema to define parameters:

### Basic Types

```python
parameters={
    "type": "object",
    "properties": {
        "string_param": {"type": "string"},
        "number_param": {"type": "number"},
        "integer_param": {"type": "integer"},
        "boolean_param": {"type": "boolean"},
        "array_param": {"type": "array", "items": {"type": "string"}},
        "object_param": {"type": "object"}
    }
}
```

### With Descriptions and Enums

```python
parameters={
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query to execute"
        },
        "priority": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Task priority level"
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of results",
            "default": 10
        }
    },
    "required": ["query"]
}
```

## Error Handling

### Within Tools

Tool functions that raise exceptions are automatically caught and produce a failed `ToolResult`:

```python
async def risky_operation(self, context: FlowContext, inputs: dict):
    param = inputs.get("param")
    if not param:
        raise ValueError("param is required")

    # If this raises, ToolResult.success=False, ToolResult.error=str(e)
    result = await some_api_call(param)
    return {"data": result}
```

### When Using Tools Directly

```python
from agstack.llm.flow import create_tool

try:
    tool = create_tool("my_tool")
    result = await tool.run(context, {"param": "value"})
    if result is None:
        print("Tool execution failed")
except RuntimeError as e:
    print(f"Tool not found: {e}")
```

## Best Practices

1. **Clear Descriptions**: Write descriptions that help the LLM understand when and how to use the tool
2. **Specific Names**: Use descriptive, action-oriented names (e.g., "get_weather" not "weather")
3. **Return Dicts**: Always return structured dicts, not bare strings
4. **Function Signature**: Always use `async def fn(self, context, inputs)` — get params from `inputs`
5. **Async Operations**: Always use async/await for I/O operations
6. **Error Handling**: Let exceptions propagate — the framework catches them gracefully
7. **Documentation**: Include parameter descriptions in JSON Schema
8. **Stateless Logic**: Prefer using `inputs` over `context.get_variable()` within tool functions

## Common Patterns

### Search Tool

```python
class SearchTool(Tool):
    def __init__(self, search_service):
        self.search = search_service
        super().__init__(
            name="search",
            description="Search for information",
            function=self.execute_search,
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10}
                },
                "required": ["query"]
            }
        )

    async def execute_search(self, context: FlowContext, inputs: dict):
        query = inputs["query"]
        limit = inputs.get("limit", 10)
        results = await self.search.query(query, limit=limit)
        return {"items": results, "count": len(results)}
```

### CRUD Tool

```python
class CreateUserTool(Tool):
    def __init__(self, db):
        self.db = db
        super().__init__(
            name="create_user",
            description="Create a new user account",
            function=self.create,
            parameters={
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "name": {"type": "string"},
                    "role": {"type": "string", "enum": ["user", "admin"]}
                },
                "required": ["email", "name"]
            }
        )

    async def create(self, context: FlowContext, inputs: dict):
        email = inputs["email"]
        name = inputs["name"]
        role = inputs.get("role", "user")

        user_id = await self.db.create_user(email, name, role)
        return {"id": user_id, "email": email, "name": name, "role": role}
```

### Notification Tool

```python
class NotifyTool(Tool):
    def __init__(self, notification_service):
        self.notifier = notification_service
        super().__init__(
            name="send_notification",
            description="Send notification to a user",
            function=self.notify,
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "message": {"type": "string"},
                    "channel": {"type": "string", "enum": ["email", "sms", "push"]}
                },
                "required": ["user_id", "message", "channel"]
            }
        )

    async def notify(self, context: FlowContext, inputs: dict):
        user_id = inputs["user_id"]
        message = inputs["message"]
        channel = inputs["channel"]

        success = await self.notifier.send(user_id, message, channel)
        return {"sent": success, "channel": channel}
```
