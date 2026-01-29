# Registry and Factory Pattern Guide

## What is the Registry?

The Registry is the **core architectural pattern** in agstack that manages all component lifecycle - registration, creation, and discovery. It provides a centralized way to register and access Agents, Tools, Flows, and other components throughout your application.

## Architecture Overview

AgStack has a **two-layer registry design**:

1. **Global Registry** (`agstack.registry`) - Base implementation for all components
2. **Flow Registry** (`agstack.llm.flow.registry`) - Type-safe adapter for Flow system

```python
# Global Registry (base layer)
from agstack.registry import registry as global_registry

# Flow Registry (type-safe adapter)
from agstack.llm.flow import registry
```

## Global vs Flow Registry

### When to Use Each

| Scenario | Use | Reason |
|----------|-----|--------|
| Working with Agents, Tools, Flows | Flow Registry | Type-safe, convenient API |
| Working with Routers, Preprocessors | Global Registry | More component types |
| Building Flow applications | Flow Registry | Designed for Flow system |
| Low-level component management | Global Registry | Direct access to manifests |

### Key Differences

**Global Registry**:
- Supports 5 component types: tool, agent, flow, router, preprocessor
- Returns `Any | None` (requires type checking)
- Direct access to `ComponentManifest`

**Flow Registry**:
- Supports 3 component types: tool, agent, flow
- Returns typed objects: `Tool | None`, `Agent | None`
- Adapter around global registry

## Component Registration

### Basic Registration

```python
from agstack.llm.flow import registry

# Register a tool class
class MyTool(Tool):
    def __init__(self):
        super().__init__(
            name="my_tool",
            description="My custom tool",
            function=self.execute
        )
    
    async def execute(self, context: FlowContext):
        return "Result"

registry.register_tool("my_tool", MyTool)

# Register an agent
class ChatAgent(Agent):
    def __init__(self):
        super().__init__(
            name="chat",
            instructions="You are helpful",
            model="gpt-4o"
        )

registry.register_agent("chat", ChatAgent)

# Register a flow
registry.register_flow("my_flow", MyFlow)
```

### Factory Function Registration

For tools or agents that need initialization parameters:

```python
# Tool with configuration
class APIClient(Tool):
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        super().__init__(
            name="api_client",
            description="Call external API",
            function=self.call_api
        )
    
    async def call_api(self, context: FlowContext):
        # Use self.base_url and self.api_key
        pass

# Register with factory function for lazy initialization
registry.register_tool(
    "api_client",
    lambda: APIClient(
        base_url="https://api.example.com",
        api_key=os.getenv("API_KEY")
    )
)
```

### Registration with Metadata

```python
from agstack.registry import registry as global_registry

# Register with metadata and dependencies
global_registry.register_tool(
    name="advanced_tool",
    tool_class=AdvancedTool,
    metadata={
        "author": "team@example.com",
        "category": "data-processing",
        "description": "Advanced data processing tool"
    },
    dependencies=["database_tool", "cache_tool"],
    version="2.1.0"
)
```

## Component Creation

### Registry Method (Safe)

Returns `None` if component doesn't exist:

```python
from agstack.llm.flow import registry

# Create tool - returns None if not found
tool = registry.create_tool("my_tool")
if tool:
    result = await tool.run(context)
else:
    print("Tool not found")

# Create agent with parameters
agent = registry.create_agent("chat", temperature=0.7)
if agent:
    response = await agent.run(context)
```

### Factory Function (Fast-Fail)

Raises `RuntimeError` if component doesn't exist:

```python
from agstack.llm.flow import create_tool, create_agent

# Raises RuntimeError if not registered
tool = create_tool("my_tool")
result = await tool.run(context)

# With parameters
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
# Create multiple tools at once
tools = registry.create_tools(["web_search", "calculator", "database"])

# Only successfully created tools are returned
# Failed tools are silently skipped
for tool in tools:
    print(f"Created: {tool.name}")
```

### List All Components

```python
# List all registered tools
all_tools = registry.list_tools()
print(f"Available tools: {all_tools}")

# List all agents
all_agents = registry.list_agents()

# List all flows
all_flows = registry.list_flows()

# Get complete registry info (global registry only)
from agstack.registry import registry as global_registry
info = global_registry.get_all_info()
# Returns: {"tool": [...], "agent": [...], "flow": [...], ...}
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

### Get Component Metadata

```python
from agstack.registry import registry as global_registry

# Get full manifest
manifest = global_registry.get("tool", "my_tool")
if manifest:
    print(f"Name: {manifest.name}")
    print(f"Type: {manifest.type}")
    print(f"Version: {manifest.version}")
    print(f"Metadata: {manifest.metadata}")
    print(f"Dependencies: {manifest.dependencies}")
```

## Advanced Patterns

### Conditional Registration

```python
def register_components(config):
    """Register components based on configuration"""
    from agstack.llm.flow import registry
    
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

### Plugin System

```python
def load_plugins(plugin_dir: str):
    """Dynamically load and register plugins"""
    import importlib.util
    from pathlib import Path
    
    for plugin_file in Path(plugin_dir).glob("*.py"):
        # Load plugin module
        spec = importlib.util.spec_from_file_location(
            plugin_file.stem, 
            plugin_file
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Plugin should have a register() function
        if hasattr(module, "register"):
            module.register(registry)

# Usage
load_plugins("./plugins")
```

### Dependency Injection

```python
class ServiceRegistry:
    """Higher-level registry with dependency injection"""
    
    def __init__(self, config, db_pool, cache):
        self.config = config
        self.db_pool = db_pool
        self.cache = cache
        self._register_all()
    
    def _register_all(self):
        from agstack.llm.flow import registry
        
        # Register tools with injected dependencies
        registry.register_tool(
            "user_lookup",
            lambda: UserLookupTool(db=self.db_pool)
        )
        
        registry.register_tool(
            "cache_get",
            lambda: CacheTool(cache=self.cache)
        )
        
        # Register agent with config
        registry.register_agent(
            "chat",
            lambda: ChatAgent(
                model=self.config.default_model,
                temperature=self.config.temperature
            )
        )

# Usage
services = ServiceRegistry(config, db_pool, cache)
```

### Testing with Mock Components

```python
import pytest
from agstack.llm.flow import registry

@pytest.fixture
def mock_registry():
    """Setup mock components for testing"""
    
    # Register mock tool
    class MockTool(Tool):
        def __init__(self):
            super().__init__(
                name="mock_tool",
                description="Mock for testing",
                function=self.execute
            )
        
        async def execute(self, context):
            return "mock_result"
    
    registry.register_tool("test_tool", MockTool)
    
    yield registry
    
    # Cleanup after test
    # Note: Current registry doesn't have unregister()
    # In production, use separate registry instances for tests

def test_with_mock_tool(mock_registry):
    tool = mock_registry.create_tool("test_tool")
    assert tool is not None
```

## Component Manifest

### Understanding ComponentManifest

```python
from agstack.registry import ComponentManifest

manifest = ComponentManifest(
    name="my_tool",              # Unique identifier
    type="tool",                 # Component type
    component=MyToolClass,       # Actual class or factory
    metadata={                   # Custom metadata
        "author": "team@example.com",
        "category": "data",
        "tags": ["processing", "analytics"]
    },
    dependencies=["cache_tool"], # Required components
    version="1.2.0"              # Version string
)
```

### Accessing Manifest Information

```python
from agstack.registry import registry as global_registry

# Get manifest
manifest = global_registry.get("tool", "my_tool")

# Check dependencies
if manifest and manifest.dependencies:
    print(f"Requires: {manifest.dependencies}")
    
    # Ensure dependencies are registered
    for dep in manifest.dependencies:
        if dep not in registry.list_tools():
            raise RuntimeError(f"Missing dependency: {dep}")

# Check version compatibility
if manifest and manifest.version:
    major_version = int(manifest.version.split(".")[0])
    if major_version < 2:
        print("Warning: Old version detected")
```

## Common Patterns

### Centralized Registration

```python
# components/registry.py
from agstack.llm.flow import registry

def register_all_components():
    """Register all application components"""
    
    # Tools
    from .tools import (
        WebSearchTool,
        DatabaseTool,
        EmailTool
    )
    registry.register_tool("web_search", WebSearchTool)
    registry.register_tool("database", DatabaseTool)
    registry.register_tool("email", EmailTool)
    
    # Agents
    from .agents import (
        ChatAgent,
        ResearchAgent,
        SupportAgent
    )
    registry.register_agent("chat", ChatAgent)
    registry.register_agent("research", ResearchAgent)
    registry.register_agent("support", SupportAgent)
    
    # Flows
    from .flows import (
        OnboardingFlow,
        SupportFlow
    )
    registry.register_flow("onboarding", OnboardingFlow)
    registry.register_flow("support", SupportFlow)

# app/main.py
from components.registry import register_all_components

def main():
    # Register at startup
    register_all_components()
    
    # Start application
    uvicorn.run(app)
```

### Lazy Initialization

```python
# Defer expensive initialization until first use
class ExpensiveTool(Tool):
    _model = None  # Class-level cache
    
    def __init__(self):
        super().__init__(
            name="expensive",
            description="Tool with expensive initialization",
            function=self.execute
        )
    
    @classmethod
    def get_model(cls):
        if cls._model is None:
            # Expensive: load model, connect to service, etc.
            cls._model = load_expensive_model()
        return cls._model
    
    async def execute(self, context):
        model = self.get_model()
        return model.process(context.get_variable("input"))

# Register - no initialization happens yet
registry.register_tool("expensive", ExpensiveTool)

# Initialization happens on first use
tool = create_tool("expensive")  # ExpensiveTool.__init__() called
result = await tool.run(context)  # get_model() loads model on first call
```

### Environment-Based Registration

```python
import os
from agstack.llm.flow import registry

def register_for_environment():
    """Register different components based on environment"""
    env = os.getenv("ENVIRONMENT", "development")
    
    if env == "development":
        # Use mock tools for development
        registry.register_tool("payment", MockPaymentTool)
        registry.register_tool("email", MockEmailTool)
    
    elif env == "staging":
        # Use staging services
        registry.register_tool(
            "payment",
            lambda: PaymentTool(api_key=os.getenv("STAGING_PAYMENT_KEY"))
        )
        registry.register_tool(
            "email",
            lambda: EmailTool(smtp_host="staging-smtp.example.com")
        )
    
    elif env == "production":
        # Use production services
        registry.register_tool(
            "payment",
            lambda: PaymentTool(api_key=os.getenv("PAYMENT_KEY"))
        )
        registry.register_tool(
            "email",
            lambda: EmailTool(smtp_host="smtp.example.com")
        )
```

## Best Practices

### 1. Register at Startup

```python
# ✅ Good: Register once at application startup
def main():
    register_all_components()
    app.run()

# ❌ Bad: Registering in request handlers
@app.post("/chat")
async def chat(request):
    registry.register_agent("chat", ChatAgent)  # Wrong!
    agent = create_agent("chat")
```

### 2. Use Factory Functions for Configuration

```python
# ✅ Good: Use factory for configuration
registry.register_tool(
    "api_client",
    lambda: APIClient(
        base_url=config.api_url,
        timeout=config.timeout
    )
)

# ❌ Bad: Hardcoding in class
class APIClient(Tool):
    def __init__(self):
        self.base_url = "https://hardcoded.com"  # Not flexible
```

### 3. Handle Missing Components

```python
# ✅ Good: Check before use
tool = registry.create_tool("optional_feature")
if tool:
    result = await tool.run(context)
else:
    # Graceful fallback
    result = default_behavior()

# ❌ Bad: Assume component exists
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

### 5. Document Dependencies

```python
# ✅ Good: Explicit dependencies
from agstack.registry import registry as global_registry

global_registry.register_tool(
    "workflow_tool",
    WorkflowTool,
    dependencies=["database_tool", "cache_tool"],
    metadata={"requires": "PostgreSQL 12+"}
)

# Check dependencies before use
manifest = global_registry.get("tool", "workflow_tool")
for dep in manifest.dependencies:
    if dep not in registry.list_tools():
        raise RuntimeError(f"Missing dependency: {dep}")
```

## Troubleshooting

### Component Not Found

```python
# Problem: RuntimeError when using factory function
tool = create_tool("my_tool")  # RuntimeError: Tool 'my_tool' not registered

# Solution 1: Check if registered
if "my_tool" in registry.list_tools():
    tool = create_tool("my_tool")
else:
    print("Tool not registered yet")

# Solution 2: Use safe creation
tool = registry.create_tool("my_tool")
if not tool:
    print("Tool not found")
```

### Factory Function Issues

```python
# Problem: Factory called too early
registry.register_tool(
    "db_tool",
    DatabaseTool(db=db_pool)  # ❌ Called immediately, db_pool might not exist
)

# Solution: Use lambda for lazy evaluation
registry.register_tool(
    "db_tool",
    lambda: DatabaseTool(db=db_pool)  # ✅ Called when create_tool() is invoked
)
```

### Type Checking Issues

```python
# Problem: Type checker doesn't know return type
tool = registry.create_tool("my_tool")  # Type: Any | None

# Solution 1: Use Flow registry for better types
from agstack.llm.flow import registry
tool = registry.create_tool("my_tool")  # Type: Tool | None

# Solution 2: Add type annotation
from agstack.llm.flow import Tool
tool: Tool | None = registry.create_tool("my_tool")
```

## Summary

**Key Takeaways**:

1. **Two Registries**: Use Flow Registry for Agents/Tools/Flows, Global Registry for other components
2. **Registration**: Register once at startup with factory functions for flexibility
3. **Creation**: Use `registry.create_*()` for safety, `create_*()` for speed
4. **Organization**: Centralize registration, organize by module
5. **Dependencies**: Document and check dependencies explicitly

**When in doubt**:
- Use **Flow Registry** (`agstack.llm.flow.registry`) for most cases
- Use **`registry.create_*()`** when component might not exist
- Use **`create_*()`** when component must exist
- Register components **once at startup**, not per-request
