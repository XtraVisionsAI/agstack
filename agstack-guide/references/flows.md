# Flows Guide

## What are Flows?

Flows orchestrate multiple agents and tools into complex, multi-step workflows. They enable you to build sophisticated AI applications by chaining together different components in a defined sequence or graph.

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
            "type": "tool",          # Node type
            "config": {              # Node configuration
                "tool_name": "...",
                "inputs": {"key": "$v.var_name"}
            }
        },
        {
            "id": "step2",
            "type": "agent",
            "config": {
                "agent_name": "...",
                "inputs": {"input": "$o.step1.result"}
            }
        }
    ],
    edges=[                          # Routing (optional, enables graph mode)
        {"source": "step1", "target": "step2"}
    ],
    variables={},                    # Default variables
    cycle_limits={}                  # Per-node iteration caps
)

# Execute flow
context = FlowContext()
result = await flow.run(context)
```

## Two Execution Modes

### Sequential Mode (no edges)

When `edges` is empty, nodes execute in order:

```python
flow = Flow(
    flow_id="sequential",
    name="Sequential Flow",
    nodes=[
        {"id": "step1", "type": "tool", "config": {...}},
        {"id": "step2", "type": "agent", "config": {...}},
        {"id": "step3", "type": "tool", "config": {...}}
    ]
)
```

### Edge-Driven Mode (with edges)

When `edges` is provided, execution follows the graph:

```python
flow = Flow(
    flow_id="graph",
    name="Graph Flow",
    nodes=[
        {"id": "start", "type": "tool", "config": {...}},
        {"id": "branch_a", "type": "agent", "config": {...}},
        {"id": "branch_b", "type": "agent", "config": {...}},
    ],
    edges=[
        {"source": "start", "target": "branch_a", "condition": "$o.start.type == urgent"},
        {"source": "start", "target": "branch_b"}  # fallback (no condition)
    ]
)
```

## Variable References

Flows use a reference syntax to pass data between nodes:

### Output References (`$o.`)

Access results from previous nodes:

```python
"$o.node_id"              # Full output of a node
"$o.node_id.field"        # Access nested field
"$o.node_id.user.name"    # Deep nested access
```

### Variable References (`$v.`)

Access flow-level variables:

```python
"$v.variable_name"       # Access context.variables["variable_name"]
```

### Example

```python
# Set variables before running
context.set_variable("user_id", "123")
context.set_variable("topic", "Python")

# In node config:
"inputs": {
    "id": "$v.user_id",           # → "123"
    "data": "$o.fetch.result",    # → output of "fetch" node's "result" field
}
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
                "inputs": {
                    "email": "$v.email",
                    "name": "$v.name"
                }
            }
        },
        {
            "id": "send_welcome",
            "type": "tool",
            "config": {
                "tool_name": "send_email",
                "inputs": {
                    "to": "$v.email",
                    "subject": "Welcome!",
                    "user_id": "$o.create_account.id"
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
                "inputs": {"query": "$v.topic"}
            }
        },
        {
            "id": "analyze",
            "type": "agent",
            "config": {
                "agent_name": "analyst",
                "inputs": {"input": "$o.search.result"}
            }
        },
        {
            "id": "summarize",
            "type": "agent",
            "config": {
                "agent_name": "writer",
                "inputs": {"input": "$o.analyze.result"}
            }
        }
    ],
    edges=[
        {"source": "search", "target": "analyze"},
        {"source": "analyze", "target": "summarize"}
    ]
)
```

### Flow with LLM Chat Node

```python
flow = Flow(
    flow_id="classify_and_respond",
    name="Classify and Respond",
    nodes=[
        {
            "id": "classify",
            "type": "llm_chat",
            "config": {
                "prompt": "Classify this request into one word (urgent/normal/spam): {query}",
                "model": "gpt-4o",
                "temperature": 0.1,
                "inputs": {"query": "$v.user_query"}
            }
        },
        {
            "id": "respond",
            "type": "agent",
            "config": {
                "agent_name": "support",
                "inputs": {
                    "input": "$v.user_query",
                    "priority": "$o.classify.result"
                }
            }
        }
    ],
    edges=[
        {"source": "classify", "target": "respond"}
    ]
)
```

### Flow with Python Node

```python
{
    "id": "transform",
    "type": "python",
    "config": {
        "code": "def main(data, threshold):\n    filtered = [x for x in data if x > threshold]\n    return {'filtered': filtered, 'count': len(filtered)}",
        "inputs": {
            "data": "$o.fetch.items",
            "threshold": "$v.min_score"
        }
    }
}
```

### Flow with Switch Node

```python
{
    "id": "route",
    "type": "switch",
    "config": {
        "variable": "$o.classify.result",
        "cases": {"urgent": "urgent", "normal": "normal", "spam": "spam"},
        "default": "normal"
    }
}
# Output: {"choice": "<matched_case>"}
# Use with edges: {"source": "route", "target": "handle_urgent", "condition": "$o.route.choice == urgent"}
```

### Flow with Subflow Node

```python
{
    "id": "sub_process",
    "type": "subflow",
    "config": {
        "flow_name": "registered_flow_name",  # from registry
        "inputs": {"data": "$o.previous.result"}
    }
}
```

## Running Flows

### Basic Execution

```python
from agstack.llm.flow import FlowContext

# Create context with input variables
context = FlowContext()
context.set_variable("topic", "Python programming")
context.set_variable("user_id", "user456")

# Run flow
result = await flow.run(context)

# Access results — returns context.outputs (dict of node_id → output)
print(result)  # {"step1": {...}, "step2": {...}, ...}
```

### Accessing Node Outputs

```python
result = await flow.run(context)

# Get specific node output
search_output = result.get("search")      # or context.outputs["search"]
analyze_output = result.get("analyze")

# All outputs are also accessible via context
print(context.outputs["search"])
```

### Streaming Execution

```python
from agstack.llm.flow import EventType

async for evt in flow.stream(context):
    event_type = evt.get("type")

    if event_type == EventType.STEP_STARTED:
        step = evt.get("stepName")
        print(f"Step started: {step}")

    elif event_type == EventType.TEXT_MESSAGE_CONTENT:
        # Streaming content from agent/llm_chat nodes
        print(evt.get("delta"), end="", flush=True)

    elif event_type == EventType.STEP_FINISHED:
        step = evt.get("stepName")
        print(f"\nStep finished: {step}")

    elif event_type == EventType.RUN_ERROR:
        print(f"Error: {evt.get('message')}")
```

## Edge Conditions

Edges support comparison expressions:

```python
edges=[
    # Equality
    {"source": "A", "target": "B", "condition": "$o.A.status == success"},

    # Inequality
    {"source": "A", "target": "C", "condition": "$o.A.status != success"},

    # Numeric comparisons
    {"source": "A", "target": "D", "condition": "$o.A.score > 0.8"},
    {"source": "A", "target": "E", "condition": "$o.A.count <= 10"},

    # Boolean (truthy check)
    {"source": "A", "target": "F", "condition": "$o.A.is_valid"},

    # Fallback (no condition — used when no conditional edge matches)
    {"source": "A", "target": "G"}
]
```

**Resolution order**: Conditional edges are evaluated first; the first match wins. If none match, the fallback edge (without condition) is used.

## Cycle Limits

For flows with loops (edges that create cycles), use `cycle_limits` to prevent infinite execution:

```python
flow = Flow(
    flow_id="refine_loop",
    name="Iterative Refinement",
    nodes=[
        {"id": "draft", "type": "agent", "config": {...}},
        {"id": "review", "type": "agent", "config": {...}},
    ],
    edges=[
        {"source": "draft", "target": "review"},
        {"source": "review", "target": "draft", "condition": "$o.review.needs_revision == true"},
        {"source": "review", "target": None}  # end
    ],
    cycle_limits={"draft": 5}  # "draft" node runs at most 5 times
)
```

When a node exceeds its cycle limit, conditional edges are skipped and only the fallback edge is followed.

## Retry Policy

Nodes can specify retry behavior for transient failures:

```python
{
    "id": "fragile",
    "type": "tool",
    "config": {
        "tool_name": "external_api",
        "inputs": {"url": "$v.api_url"},
        "retry": {
            "max_retries": 3,   # Retry up to 3 times after initial failure
            "delay": 1.0,       # Initial delay in seconds
            "backoff": 2.0      # Exponential backoff multiplier
        }
    }
}
```

## Parallel Execution

The `parallel` node type runs multiple branches concurrently:

```python
flow = Flow(
    flow_id="parallel_fetch",
    name="Parallel Fetch",
    nodes=[
        {
            "id": "parallel_step",
            "type": "parallel",
            "config": {
                "branches": ["fetch_a", "fetch_b", "fetch_c"]
            }
        },
        {"id": "fetch_a", "type": "tool", "config": {"tool_name": "api_a", "inputs": {...}}},
        {"id": "fetch_b", "type": "tool", "config": {"tool_name": "api_b", "inputs": {...}}},
        {"id": "fetch_c", "type": "tool", "config": {"tool_name": "api_c", "inputs": {...}}},
        {
            "id": "merge",
            "type": "python",
            "config": {
                "code": "def main(a, b, c):\n    return {'combined': [a, b, c]}",
                "inputs": {
                    "a": "$o.fetch_a",
                    "b": "$o.fetch_b",
                    "c": "$o.fetch_c"
                }
            }
        }
    ],
    edges=[
        {"source": "parallel_step", "target": "merge"}
    ]
)
```

## Iteration

The `iteration` node type loops over a list:

```python
{
    "id": "process_items",
    "type": "iteration",
    "config": {
        "items": "$o.fetch.results",       # List to iterate over
        "item_variable": "current_item",   # Variable name for current item
        "index_variable": "current_index", # Variable name for index
        "body": ["transform_step"]         # Node IDs to execute per item
    }
}
# Output: {"results": [<output of last body node for each item>]}
```

## Flow Registration

Flows can be registered for reuse:

```python
from agstack.llm.flow import registry

# Register flow class/factory
registry.register_flow("onboarding", lambda: onboarding_flow)
registry.register_flow("research", lambda: research_flow)

# Create flow from registry
flow = registry.create_flow("onboarding")
```

## Loading Flows from Configuration

Flows can be loaded from JSON files or dicts:

```python
from agstack.llm.flow import FlowLoader

# Load from dict
config = {
    "flow_id": "my_flow",
    "name": "My Flow",
    "nodes": [...],
    "edges": [...]
}
flow = FlowLoader.load_from_dict(config)

# Load from JSON file
flow = FlowLoader.load_from_file("flows/my_flow.json")

# Load from JSON string
flow = FlowLoader.load_from_string(json_str)
```

## FlowTrace (Structured Execution Trace)

After flow execution, `context.trace` contains a complete structured trace:

```python
context = FlowContext()
result = await flow.run(context)

trace = context.trace
print(f"Started: {trace.started_at}")
print(f"Finished: {trace.finished_at}")
print(f"Total usage: {trace.total_usage}")

# Node traces contain timing, inputs, outputs, tool calls
for node_trace in trace.nodes:
    print(f"  {node_trace.node_id} ({node_trace.node_type}): {node_trace.duration_ms}ms")
    if node_trace.tool_calls:
        for tc in node_trace.tool_calls:
            print(f"    tool: {tc['tool_name']} → {tc['success']}")

# Edge traces record routing decisions
for edge_trace in trace.edges:
    print(f"  {edge_trace.source} → {edge_trace.target} (condition={edge_trace.condition}, satisfied={edge_trace.satisfied})")
```

## Error Handling

```python
from agstack.llm.flow.exceptions import FlowError, NodeExecutionError

try:
    result = await flow.run(context)
except NodeExecutionError as e:
    print(f"Node execution failed: {e.error_key}")
    print(f"Details: {e.arguments}")
except FlowError as e:
    print(f"Flow error: {e.error_key}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

## Node Types Reference

| Type | Handler | Description |
|------|---------|-------------|
| `agent` | AgentNodeHandler | Multi-turn LLM with tool use |
| `tool` | ToolNodeHandler | Execute registered tool |
| `llm_chat` | LLMChatNodeHandler | Single-turn LLM call |
| `python` | PythonNodeHandler | Sandboxed Python execution |
| `switch` | SwitchNodeHandler | Variable-based routing |
| `subflow` | SubflowNodeHandler | Execute another flow |
| `detect` | DetectNodeHandler | Detection/classification |
| `echo` | EchoNodeHandler | Pass-through echo |
| `llm_embed` | LLMEmbedNodeHandler | Embedding generation |
| `llm_rerank` | LLMRerankNodeHandler | Reranking |
| `message` | (flow-level) | Template text output |
| `parallel` | (flow-level) | Concurrent branches |
| `iteration` | (flow-level) | Loop over items |

## Custom Node Handlers

Register your own node types:

```python
from agstack.llm.flow import NodeHandler, register_node_handler

class MyHandler(NodeHandler):
    node_type = "my_type"

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        resolved = self.resolve_inputs(config, context)
        # Custom logic
        return {"result": "done"}

register_node_handler("my_type", MyHandler())
```

## Best Practices

1. **Use edges for complex flows**: Edge-driven mode enables conditional routing, cycles, and clear data flow
2. **Clear Node IDs**: Use descriptive, meaningful node identifiers
3. **Error Handling**: Always wrap flow execution in try-except
4. **Reference syntax**: Use `$o.node_id.field` for outputs, `$v.key` for variables
5. **Node Ordering**: In sequential mode, order nodes logically
6. **Cycle Limits**: Always set `cycle_limits` for flows with loops
7. **Configuration**: Store complex flows as JSON and load with `FlowLoader`
8. **Testing**: Test each node independently before integrating into flow
9. **Monitoring**: Use `context.trace` for debugging and performance analysis
10. **Retry**: Add retry policies for nodes calling external services
