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
    
    async def execute(self, context: FlowContext):
        # Tool implementation
        param1 = context.get_variable("param1")
        result = f"Processed: {param1}"
        return result
```

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
    
    async def greet(self, context: FlowContext):
        name = context.get_variable("name")
        return f"Hello, {name}! Welcome to agstack."
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
    
    async def calculate(self, context: FlowContext):
        op = context.get_variable("operation")
        a = context.get_variable("a")
        b = context.get_variable("b")
        
        if op == "add":
            return a + b
        elif op == "subtract":
            return a - b
        elif op == "multiply":
            return a * b
        elif op == "divide":
            if b == 0:
                raise ValueError("Cannot divide by zero")
            return a / b
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
    
    async def fetch_weather(self, context: FlowContext):
        location = context.get_variable("location")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.weather.com/v1/current",
                params={"location": location, "key": self.api_key}
            )
            data = response.json()
            
        return f"Weather in {location}: {data['temp']}°C, {data['condition']}"
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
    
    async def lookup(self, context: FlowContext):
        user_id = context.get_variable("user_id")
        
        query = "SELECT * FROM users WHERE id = $1 OR email = $1"
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(query, user_id)
        
        if not row:
            return "User not found"
        
        return {
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "created_at": row["created_at"]
        }
```

## Registering Tools

Tools must be registered before use:

```python
from agstack.llm.flow import registry

# Register tool instance
registry.register_tool("greeting", GreetingTool())
registry.register_tool("calculator", CalculatorTool())

# Register with factory function
registry.register_tool("weather", lambda: WeatherTool(api_key="your_key"))
```

## Using Tools

### In Agents

```python
from agstack.llm.flow import Agent, registry

# Create tool list
tools = [
    registry.create_tool("greeting"),
    registry.create_tool("calculator")
]

# Give tools to agent
agent = Agent(
    name="assistant",
    instructions="Use the available tools to help users",
    model="gpt-4o",
    tools=tools
)
```

### Directly in Code

```python
from agstack.llm.flow import FlowContext, create_tool

# Create context with parameters
context = FlowContext(session_id="test")
context.set_variable("name", "Alice")

# Execute tool
tool = create_tool("greeting")
result = await tool.run(context)
print(result)  # "Hello, Alice! Welcome to agstack."
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
                "parameters": {"user_id": "{input.user_id}"}
            }
        },
        {
            "id": "greet_user",
            "type": "tool",
            "config": {
                "tool_name": "greeting",
                "parameters": {"name": "{fetch_data@result.name}"}
            }
        }
    ]
)
```

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

### With Descriptions

```python
parameters={
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query to execute"
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of results to return",
            "default": 10
        }
    },
    "required": ["query"]
}
```

### With Enums

```python
parameters={
    "type": "object",
    "properties": {
        "priority": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Task priority level"
        }
    }
}
```

### Complex Nested Objects

```python
parameters={
    "type": "object",
    "properties": {
        "user": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
                "tags": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["name"]
        }
    }
}
```

## Tool Results

Tools should return:

1. **Simple values**: strings, numbers, booleans
2. **Dictionaries**: For structured data
3. **Lists**: For multiple results

```python
# Simple string
return "Operation completed successfully"

# Dictionary
return {
    "status": "success",
    "data": {"id": 123, "name": "Alice"},
    "timestamp": "2026-01-28T10:30:00Z"
}

# List
return ["result1", "result2", "result3"]
```

## Error Handling

### Within Tools

```python
from agstack.llm.flow.exceptions import ToolExecutionError

async def risky_operation(self, context: FlowContext):
    param = context.get_variable("param")
    
    try:
        # Risky operation
        result = some_api_call(param)
        return result
    except ValueError as e:
        raise ToolExecutionError(
            error_key="invalid_param",
            arguments={"param": param, "error": str(e)}
        )
    except Exception as e:
        raise ToolExecutionError(
            error_key="operation_failed",
            arguments={"error": str(e)}
        )
```

### When Using Tools

```python
from agstack.llm.flow.exceptions import ToolExecutionError

try:
    tool = create_tool("my_tool")
    result = await tool.run(context)
except ToolExecutionError as e:
    print(f"Tool error: {e.error_key}")
    print(f"Details: {e.arguments}")
except RuntimeError as e:
    print(f"Tool not found: {e}")
```

## Best Practices

1. **Clear Descriptions**: Write descriptions that help the LLM understand when and how to use the tool
2. **Specific Names**: Use descriptive, action-oriented names (e.g., "get_weather" not "weather")
3. **Parameter Validation**: Always validate parameters before use
4. **Error Handling**: Raise ToolExecutionError with meaningful error keys
5. **Return Formatting**: Return structured data when possible (dicts over strings)
6. **Async Operations**: Always use async/await for I/O operations
7. **Stateless Design**: Don't rely on instance variables; use context
8. **Documentation**: Include parameter descriptions in JSON Schema

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
    
    async def execute_search(self, context: FlowContext):
        query = context.get_variable("query")
        limit = context.get_variable("limit", 10)
        results = await self.search.query(query, limit=limit)
        return results
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
    
    async def create(self, context: FlowContext):
        email = context.get_variable("email")
        name = context.get_variable("name")
        role = context.get_variable("role", "user")
        
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
    
    async def notify(self, context: FlowContext):
        user_id = context.get_variable("user_id")
        message = context.get_variable("message")
        channel = context.get_variable("channel")
        
        success = await self.notifier.send(user_id, message, channel)
        return {"sent": success, "channel": channel}
```
