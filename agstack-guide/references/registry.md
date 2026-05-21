# Registry and Factory Pattern Guide

## What is the Registry?

The Registry is the core pattern in agstack that manages component lifecycle — registration, creation, and discovery. It provides a centralized way to register and access Agents, Tools, Flows, and Node Handlers.

## Architecture

AgStack uses a single `FlowRegistry` class:

```python
from agstack.llm.flow import registry  # FlowRegistry instance
```

The registry manages four component types: **tools**, **agents**, **flows**, and **node handlers**.

## Component Registration

### Register Tools

```python
from agstack.llm.flow import registry

# Register a tool class (instantiated on create)
class MyTool(Tool):
    def __init__(self):
        super().__init__(
            name="my_tool",
            description="My custom tool",
            function=self.execute,
            parameters={...}
        )

    async def execute(self, context: FlowContext, inputs: dict):
        return {"result": "done"}

registry.register_tool("my_tool", MyTool)

# Register with factory (for tools needing init params)
registry.register_tool("api_client", lambda: APIClient(base_url="https://..."))

# Register with visibility options
registry.register_tool("search", SearchTool, label="Searching...", echo=True)
```

### Register Agents

```python
# Register agent class
class ChatAgent(Agent):
    def __init__(self):
        super().__init__(
            name="chat",
            instructions="You are helpful",
            model="gpt-4o"
        )

registry.register_agent("chat", ChatAgent)

# Register with visibility options
registry.register_agent("analyst", AnalystAgent, label="Analyzing", echo=True)
```

### Register Flows

```python
registry.register_flow("my_flow", MyFlowClass)
registry.register_flow("onboarding", lambda: build_onboarding_flow())
```

### Register Node Handlers

```python
from agstack.llm.flow import register_node_handler

register_node_handler("my_type", MyHandler())
```

## Registration Signatures

```python
# Tool: name, class/factory/instance, *, label, echo
registry.register_tool(name: str, tool_class, *, label: str | None = None, echo: bool = False)

# Agent: name, class, *, label, echo
registry.register_agent(name: str, agent_class: type[Agent], *, label: str | None = None, echo: bool = False)

# Flow: name, class/factory
registry.register_flow(name: str, flow_class: type)

# Node Handler: node_type, handler instance
registry.register_node_handler(node_type: str, handler)
```

## Component Creation

### Registry Method (Safe)

Returns `None` if component doesn't exist:

```python
from agstack.llm.flow import registry

# Create tool - returns None if not found
tool = registry.create_tool("my_tool")
if tool:
    result = await tool.run(context, {"key": "value"})
else:
    print("Tool not found")

# Create agent with override kwargs
agent = registry.create_agent("chat", model="gpt-4o-mini")
if agent:
    response = await agent.run(context)

# Create flow
flow = registry.create_flow("my_flow")
```

### Factory Function (Fast-Fail)

Raises `RuntimeError` if component doesn't exist:

```python
from agstack.llm.flow import create_tool, create_agent

# Raises RuntimeError if not registered
tool = create_tool("my_tool")
result = await tool.run(context)

# With override parameters
agent = create_agent("chat", model="gpt-4o")
response = await agent.run(context)
```

### When to Use Each

| Scenario | Use | Example |
|----------|-----|---------|
| Component might not exist | `registry.create_*()` | Optional features, plugins |
| Component must exist | `create_*()` | Core functionality |
| Need error handling | `registry.create_*()` | User-provided component names |
| Want clean code | `create_*()` | Internal, guaranteed components |

## Batch Operations

### Create Multiple Tools

```python
# Create multiple tools at once (skips missing ones)
tools = registry.create_tools(["web_search", "calculator", "database"])

for tool in tools:
    print(f"Created: {tool.name}")
```

### List All Components

```python
# List all registered component names
all_tools = registry.list_tools()        # ["web_search", "calculator", ...]
all_agents = registry.list_agents()      # ["chat", "researcher", ...]
all_flows = registry.list_flows()        # ["onboarding", ...]

# Get complete registry info
info = registry.get_all_info()
# {"tool": [...], "agent": [...], "flow": [...], "node_handler": [...]}
```

## Component Discovery

### Check if Component Exists

```python
# Method 1: Try to create
tool = registry.create_tool("my_tool")
if tool:
    print("Tool exists")

# Method 2: Check in list
if "my_tool" in registry.list_tools():
    print("Tool exists")

# Method 3: Get class/factory
tool_class = registry.get_tool_class("my_tool")
if tool_class:
    print("Tool exists")
```

### Get Tool/Agent Visibility Info

```python
# Get display label
label = registry.get_tool_label("search")  # "Searching..." or None

# Check if echo is enabled
echo = registry.get_tool_echo("search")    # True/False
echo = registry.get_agent_echo("analyst")  # True/False
```

## Agent Creation with Overrides

When creating an agent, kwargs override the agent's default attributes:

```python
# Register agent with defaults
class ChatAgent(Agent):
    def __init__(self, model="gpt-4o", temperature=0.7, **kwargs):
        super().__init__(name="chat", model=model, temperature=temperature, **kwargs)

registry.register_agent("chat", ChatAgent)

# Create with overrides
agent = registry.create_agent("chat", model="gpt-4o-mini", temperature=0.3)
# → ChatAgent(model="gpt-4o-mini", temperature=0.3)
```

## Common Patterns

### Centralized Registration

```python
# components/registry.py
from agstack.llm.flow import registry

def register_all_components():
    """Register all application components at startup"""

    # Tools
    from .tools import WebSearchTool, DatabaseTool, EmailTool
    registry.register_tool("web_search", WebSearchTool)
    registry.register_tool("database", DatabaseTool)
    registry.register_tool("email", EmailTool)

    # Agents
    from .agents import ChatAgent, ResearchAgent, SupportAgent
    registry.register_agent("chat", ChatAgent)
    registry.register_agent("research", ResearchAgent)
    registry.register_agent("support", SupportAgent)

    # Flows
    from .flows import OnboardingFlow, SupportFlow
    registry.register_flow("onboarding", OnboardingFlow)
    registry.register_flow("support", SupportFlow)

# app/main.py
from components.registry import register_all_components

def main():
    register_all_components()
    uvicorn.run(app)
```

### Factory Functions for Configuration

```python
import os

# Tools that need runtime configuration
registry.register_tool(
    "api_client",
    lambda: APIClient(
        base_url=os.getenv("API_URL", "https://api.example.com"),
        timeout=30
    )
)
```

### Conditional Registration

```python
def register_components(config):
    """Register components based on configuration"""

    # Always register core tools
    registry.register_tool("calculator", CalculatorTool)

    # Conditionally register features
    if config.enable_web_search:
        registry.register_tool(
            "web_search",
            lambda: WebSearchTool(api_key=config.search_api_key)
        )

    if config.enable_database:
        registry.register_tool(
            "db_query",
            lambda: DatabaseTool(connection=config.db_connection)
        )
```

### Environment-Based Registration

```python
import os

def register_for_environment():
    env = os.getenv("ENVIRONMENT", "development")

    if env == "development":
        registry.register_tool("payment", MockPaymentTool)
        registry.register_tool("email", MockEmailTool)
    elif env == "production":
        registry.register_tool(
            "payment",
            lambda: PaymentTool(api_key=os.getenv("PAYMENT_KEY"))
        )
        registry.register_tool(
            "email",
            lambda: EmailTool(smtp_host="smtp.example.com")
        )
```

### Testing with Mock Components

```python
import pytest
from agstack.llm.flow import registry

@pytest.fixture
def mock_registry():
    """Setup mock components for testing"""

    class MockTool(Tool):
        def __init__(self):
            super().__init__(
                name="mock_tool",
                description="Mock for testing",
                function=self.execute,
            )

        async def execute(self, context, inputs):
            return {"mock": True}

    registry.register_tool("test_tool", MockTool)
    yield registry
```

## Best Practices

### 1. Register at Startup

```python
# Good: Register once at application startup
def main():
    register_all_components()
    app.run()

# Bad: Registering in request handlers
@app.post("/chat")
async def chat(request):
    registry.register_agent("chat", ChatAgent)  # Wrong!
```

### 2. Use Factory Functions for Configuration

```python
# Good: Use lambda for deferred initialization
registry.register_tool(
    "api_client",
    lambda: APIClient(base_url=config.api_url)
)

# Bad: Instantiating too early
registry.register_tool("api_client", APIClient(base_url=config.api_url))
# ↑ Called immediately, config might not be ready
```

### 3. Handle Missing Components

```python
# Good: Check before use
tool = registry.create_tool("optional_feature")
if tool:
    result = await tool.run(context, inputs)
else:
    result = default_behavior()

# Bad: Assume component exists
tool = create_tool("optional_feature")  # Crashes if not registered
```

### 4. Organize by Module

```python
# tools/__init__.py
from agstack.llm.flow import registry
from .search import WebSearchTool
from .database import DatabaseTool

def register():
    registry.register_tool("web_search", WebSearchTool)
    registry.register_tool("database", DatabaseTool)

# agents/__init__.py
from agstack.llm.flow import registry
from .chat import ChatAgent

def register():
    registry.register_agent("chat", ChatAgent)

# main.py
from tools import register as register_tools
from agents import register as register_agents

register_tools()
register_agents()
```

## Troubleshooting

### Component Not Found

```python
# Problem: RuntimeError when using factory function
tool = create_tool("my_tool")  # RuntimeError: Tool 'my_tool' not registered

# Solution 1: Check if registered
if "my_tool" in registry.list_tools():
    tool = create_tool("my_tool")

# Solution 2: Use safe creation
tool = registry.create_tool("my_tool")  # Returns None instead of raising
```

### Factory Function Issues

```python
# Problem: Factory called too early
registry.register_tool("db_tool", DatabaseTool(db=db_pool))
# ↑ Instantiated immediately, db_pool might not exist yet

# Solution: Use lambda for lazy evaluation
registry.register_tool("db_tool", lambda: DatabaseTool(db=db_pool))
# ↑ Called only when create_tool("db_tool") is invoked
```

## Summary

**Key Takeaways**:

1. **Single Registry**: Use `from agstack.llm.flow import registry`
2. **Registration**: Register once at startup; use lambda for deferred init
3. **Creation**: `registry.create_*()` for safety, `create_*()` for speed
4. **Organization**: Centralize registration, organize by module
5. **Visibility**: Use `label` and `echo` to control user-facing events

**When in doubt**:
- Use **`registry.create_*()`** when component might not exist
- Use **`create_*()`** when component must exist
- Register components **once at startup**, not per-request
