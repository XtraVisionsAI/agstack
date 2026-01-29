# Flows Guide

## What are Flows?

Flows orchestrate multiple agents and tools into complex, multi-step workflows. They enable you to build sophisticated AI applications by chaining together different components in a defined sequence.

## Flow Anatomy

```python
from agstack.llm.flow import Flow, FlowContext

flow = Flow(
    flow_id="my_workflow",           # Unique identifier
    name="My Workflow",              # Human-readable name
    description="What this flow does",
    nodes=[                          # List of execution steps
        {
            "id": "step1",           # Unique node ID
            "type": "tool",          # Node type: "tool" or "agent"
            "config": {              # Node configuration
                "tool_name": "...",
                "parameters": {...}
            }
        },
        {
            "id": "step2",
            "type": "agent",
            "config": {
                "agent_name": "...",
                "parameters": {...}
            }
        }
    ]
)

# Execute flow
context = FlowContext(session_id="user123")
result = await flow.run(context)
```

## Creating Flows

### Simple Sequential Flow

```python
flow = Flow(
    flow_id="user_onboarding",
    name="User Onboarding Flow",
    nodes=[
        {
            "id": "create_account",
            "type": "tool",
            "config": {
                "tool_name": "create_user",
                "parameters": {
                    "email": "{input.email}",
                    "name": "{input.name}"
                }
            }
        },
        {
            "id": "send_welcome",
            "type": "tool",
            "config": {
                "tool_name": "send_email",
                "parameters": {
                    "to": "{input.email}",
                    "subject": "Welcome!",
                    "body": "Welcome to our platform, {input.name}!"
                }
            }
        }
    ]
)
```

### Flow with Agents and Tools

```python
flow = Flow(
    flow_id="research_and_summarize",
    name="Research and Summarize",
    nodes=[
        {
            "id": "search",
            "type": "tool",
            "config": {
                "tool_name": "web_search",
                "parameters": {"query": "{input.topic}"}
            }
        },
        {
            "id": "analyze",
            "type": "agent",
            "config": {
                "agent_name": "analyst",
                "parameters": {
                    "query": "Analyze these search results: {search@result}"
                }
            }
        },
        {
            "id": "summarize",
            "type": "agent",
            "config": {
                "agent_name": "writer",
                "parameters": {
                    "query": "Create a summary based on: {analyze@result}"
                }
            }
        }
    ]
)
```

### Flow with Data Processing Pipeline

```python
flow = Flow(
    flow_id="data_pipeline",
    name="Data Processing Pipeline",
    nodes=[
        {
            "id": "fetch",
            "type": "tool",
            "config": {
                "tool_name": "fetch_data",
                "parameters": {"source": "{input.source}"}
            }
        },
        {
            "id": "validate",
            "type": "tool",
            "config": {
                "tool_name": "validate_data",
                "parameters": {"data": "{fetch@result}"}
            }
        },
        {
            "id": "transform",
            "type": "tool",
            "config": {
                "tool_name": "transform_data",
                "parameters": {"data": "{validate@result}"}
            }
        },
        {
            "id": "store",
            "type": "tool",
            "config": {
                "tool_name": "store_data",
                "parameters": {"data": "{transform@result}"}
            }
        }
    ]
)
```

## Variable References

Flows use a reference syntax to pass data between nodes:

### Input Variables

```python
"{input.variable_name}"  # Access flow input
```

Example:
```python
context.set_variable("user_id", "123")
# In node: "parameters": {"id": "{input.user_id}"}
```

### Node Results

```python
"{node_id@result}"  # Access result from previous node
```

Example:
```python
{
    "id": "process",
    "type": "tool",
    "config": {
        "tool_name": "process_data",
        "parameters": {"data": "{fetch@result}"}  # Use result from "fetch" node
    }
}
```

### Nested Access

```python
"{node_id@result.field}"  # Access nested field in result
"{node_id@result.user.name}"  # Access deeply nested field
```

Example:
```python
{
    "id": "greet",
    "type": "tool",
    "config": {
        "tool_name": "greeting",
        "parameters": {"name": "{lookup@result.user.name}"}
    }
}
```

## Running Flows

### Basic Execution

```python
from agstack.llm.flow import FlowContext

# Create context with input variables
context = FlowContext(session_id="session123")
context.set_variable("topic", "Python programming")
context.set_variable("user_id", "user456")

# Run flow
result = await flow.run(context)

# Access results
print(result.content)  # Final result
print(result.records)  # Execution records for each node
```

### Accessing Node Results

```python
result = await flow.run(context)

# Get specific node result
search_result = result.get_node_result("search")
analyze_result = result.get_node_result("analyze")

# Iterate through all node results
for node_id, node_result in result.records.items():
    print(f"{node_id}: {node_result.content}")
```

### Streaming Execution

```python
from agstack.llm.flow.events import EventType

async for event in flow.stream(context):
    event_type = event.get("type")
    
    if event_type == EventType.FLOW_START:
        print("Flow started")
    
    elif event_type == EventType.NODE_START:
        node_id = event.get("nodeId")
        print(f"Node {node_id} started")
    
    elif event_type == EventType.NODE_COMPLETED:
        node_id = event.get("nodeId")
        result = event.get("result")
        print(f"Node {node_id} completed: {result}")
    
    elif event_type == EventType.TEXT_MESSAGE_CONTENT:
        # Streaming content from agent nodes
        print(event.get("delta"), end="", flush=True)
    
    elif event_type == EventType.FLOW_COMPLETED:
        final_result = event.get("result")
        print(f"\nFlow completed: {final_result}")
```

## Flow Context

FlowContext manages state throughout flow execution:

```python
# Create context
context = FlowContext(session_id="session123")

# Set input variables
context.set_variable("user_id", "123")
context.set_variable("action", "process")

# Get variables
user_id = context.get_variable("user_id")
action = context.get_variable("action", default="default_value")

# Access node results during execution
node_result = context.get_node_result("previous_node")

# Check execution state
messages = context.message_history
token_usage = context.token_usage
```

## Flow Registration

Flows can be registered for reuse:

```python
from agstack.llm.flow import registry

# Register flow
registry.register_flow("onboarding", lambda: user_onboarding_flow)
registry.register_flow("research", lambda: research_flow)

# Create flow from registry
flow = registry.create_flow("onboarding")
```

## Loading Flows from Configuration

Flows can be defined in configuration files:

```python
from agstack.llm.flow.loader import load_flow_from_config

# Load from dict
config = {
    "flow_id": "my_flow",
    "name": "My Flow",
    "nodes": [...]
}
flow = load_flow_from_config(config)

# Load from YAML/JSON file
flow = load_flow_from_file("flows/my_flow.yaml")
```

Example YAML:
```yaml
flow_id: customer_support
name: Customer Support Flow
description: Handle customer support requests
nodes:
  - id: classify
    type: agent
    config:
      agent_name: classifier
      parameters:
        query: "Classify this request: {input.request}"
  
  - id: handle
    type: agent
    config:
      agent_name: support_agent
      parameters:
        query: "Handle {classify@result} request: {input.request}"
  
  - id: notify
    type: tool
    config:
      tool_name: send_notification
      parameters:
        user_id: "{input.user_id}"
        message: "Your request has been processed: {handle@result}"
```

## Error Handling

```python
from agstack.llm.flow.exceptions import FlowError

try:
    result = await flow.run(context)
except FlowError as e:
    print(f"Flow execution failed: {e.error_key}")
    print(f"Failed at node: {e.node_id}")
    print(f"Details: {e.arguments}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

## Node Types

### Tool Node

Executes a registered tool:

```python
{
    "id": "node_id",
    "type": "tool",
    "config": {
        "tool_name": "registered_tool_name",
        "parameters": {
            "param1": "value1",
            "param2": "{input.var}"
        }
    }
}
```

### Agent Node

Executes a registered agent:

```python
{
    "id": "node_id",
    "type": "agent",
    "config": {
        "agent_name": "registered_agent_name",
        "parameters": {
            "query": "Process this: {previous_node@result}"
        }
    }
}
```

## Best Practices

1. **Clear Node IDs**: Use descriptive, meaningful node identifiers
2. **Error Handling**: Always wrap flow execution in try-except
3. **Variable Naming**: Use consistent, clear variable names
4. **Node Ordering**: Order nodes logically - later nodes can reference earlier ones
5. **Result Validation**: Validate node results before passing to next node
6. **Session Management**: Use unique session_id for each flow execution
7. **Configuration**: Store complex flows in YAML/JSON files
8. **Testing**: Test each node independently before integrating into flow
9. **Monitoring**: Log node results and execution times for debugging
10. **Documentation**: Document expected inputs and outputs for each flow

## Common Patterns

### Sequential Processing

```python
Flow(
    flow_id="sequential",
    name="Sequential Processing",
    nodes=[
        {"id": "step1", "type": "tool", "config": {...}},
        {"id": "step2", "type": "tool", "config": {...}},
        {"id": "step3", "type": "tool", "config": {...}}
    ]
)
```

### Fetch-Process-Store

```python
Flow(
    flow_id="etl",
    name="ETL Pipeline",
    nodes=[
        {
            "id": "extract",
            "type": "tool",
            "config": {"tool_name": "fetch_data", ...}
        },
        {
            "id": "transform",
            "type": "agent",
            "config": {"agent_name": "processor", ...}
        },
        {
            "id": "load",
            "type": "tool",
            "config": {"tool_name": "store_data", ...}
        }
    ]
)
```

### Multi-Agent Collaboration

```python
Flow(
    flow_id="collaboration",
    name="Multi-Agent Workflow",
    nodes=[
        {
            "id": "research",
            "type": "agent",
            "config": {"agent_name": "researcher", ...}
        },
        {
            "id": "analyze",
            "type": "agent",
            "config": {"agent_name": "analyst", ...}
        },
        {
            "id": "summarize",
            "type": "agent",
            "config": {"agent_name": "writer", ...}
        }
    ]
)
```

### Human-in-the-Loop

```python
Flow(
    flow_id="approval_flow",
    name="Human Approval Flow",
    nodes=[
        {
            "id": "draft",
            "type": "agent",
            "config": {"agent_name": "drafter", ...}
        },
        {
            "id": "request_approval",
            "type": "tool",
            "config": {"tool_name": "send_approval_request", ...}
        },
        {
            "id": "finalize",
            "type": "tool",
            "config": {
                "tool_name": "finalize_document",
                "parameters": {"approved": "{input.approval_status}"}
            }
        }
    ]
)
```

## Advanced Features

### Dynamic Node Configuration

```python
# Parameters can be computed from multiple sources
{
    "id": "complex",
    "type": "agent",
    "config": {
        "agent_name": "processor",
        "parameters": {
            "query": """
                Process data from {fetch@result} 
                for user {input.user_id}
                with settings {config@result.settings}
            """
        }
    }
}
```

### Conditional Logic (via Agent Instructions)

```python
{
    "id": "router",
    "type": "agent",
    "config": {
        "agent_name": "router",
        "parameters": {
            "query": """
                Based on {classify@result}, determine next action.
                If urgent, use fast processing. Otherwise, use standard processing.
            """
        }
    }
}
```

### Parallel Execution Preparation

While agstack currently executes nodes sequentially, you can prepare for future parallel execution:

```python
# These nodes could potentially run in parallel (future feature)
Flow(
    flow_id="parallel_ready",
    name="Parallel-Ready Flow",
    nodes=[
        {"id": "fetch_a", "type": "tool", "config": {...}},  # Independent
        {"id": "fetch_b", "type": "tool", "config": {...}},  # Independent
        {
            "id": "merge",
            "type": "tool",
            "config": {
                "tool_name": "merge_data",
                "parameters": {
                    "data_a": "{fetch_a@result}",
                    "data_b": "{fetch_b@result}"
                }
            }
        }
    ]
)
```
