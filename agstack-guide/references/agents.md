# Agents Guide

## What are Agents?

Agents are LLM-powered intelligent components that can have conversations, use tools, and complete complex tasks autonomously. They are the core execution units in agstack's flow system.

## Agent Anatomy

```python
from agstack.llm.flow import Agent, FlowContext

class MyAgent(Agent):
    def __init__(self):
        super().__init__(
            name="my_agent",              # Unique identifier
            instructions="...",            # System prompt/behavior instructions
            model="gpt-4o",               # LLM model to use
            tools=[tool1, tool2],         # Optional: Tools the agent can call
            temperature=0.7,              # Optional: LLM temperature
            max_tokens=2000,              # Optional: Response token limit
            max_turns=10,                 # Optional: Max tool-loop iterations
            label="Processing...",        # Optional: User-visible step label
            echo=False,                   # Optional: Forward TEXT_MESSAGE to user
        )
```

## Creating Agents

### Basic Agent

```python
class ChatAgent(Agent):
    def __init__(self):
        super().__init__(
            name="chat_agent",
            instructions="You are a helpful assistant that answers questions concisely.",
            model="gpt-4o"
        )
```

### Agent with Tools

```python
from agstack.llm.flow import registry

# First register your tools
registry.register_tool("web_search", WebSearchTool)
registry.register_tool("calculator", CalculatorTool)

# Create tools list
tools = registry.create_tools(["web_search", "calculator"])

class ResearchAgent(Agent):
    def __init__(self):
        super().__init__(
            name="research_agent",
            instructions="You are a research assistant. Use web search to find information.",
            model="gpt-4o",
            tools=tools
        )
```

## Registering Agents

Agents must be registered before use:

```python
from agstack.llm.flow import registry

# Register agent class (not instance, not lambda)
registry.register_agent("chat", ChatAgent)
registry.register_agent("research", ResearchAgent)
```

## Running Agents

### Non-Streaming Execution

```python
from agstack.llm.flow import FlowContext, create_agent

# Create context
context = FlowContext()
context.set_variable("query", "What is Python?")

# Get agent and run
agent = create_agent("chat")
response = await agent.run(context)

print(response["result"])  # Agent's response text
print(context.usage)       # Token usage info
```

### Streaming Execution

```python
from agstack.llm.flow import EventType

async for evt in agent.stream(context):
    event_type = evt.get("type")

    if event_type == EventType.TEXT_MESSAGE_CONTENT:
        # Stream text content as it arrives
        print(evt.get("delta"), end="", flush=True)

    elif event_type == EventType.TOOL_CALL_START:
        # Agent started calling a tool
        tool_name = evt.get("toolCallName")
        print(f"\n[Calling tool: {tool_name}]")

    elif event_type == EventType.TOOL_CALL_RESULT:
        # Tool execution completed
        result = evt.get("content")
        print(f"[Tool result: {result}]")

    elif event_type == EventType.TEXT_MESSAGE_END:
        # Agent finished its response
        print("\n[Done]")
```

## Agent Context

Agents access execution state through FlowContext:

```python
# Set input for agent (two options)
context.set_variable("input", "user message here")
# or
context.set_variable("query", "user message here")

# After execution, get agent's output
output = context.outputs.get("agent_name")  # {"result": "..."}
last_msg = context.get_last_output("agent_name")  # Last assistant text
```

## Multi-Turn Conversations

Agents automatically handle multi-turn conversations (messages are scoped per agent name):

```python
context = FlowContext()

# Turn 1
context.set_variable("query", "What is FastAPI?")
response1 = await agent.run(context)

# Turn 2 - context retains conversation history for this agent
context.set_variable("query", "Can you give me an example?")
response2 = await agent.run(context)  # Agent knows previous context
```

## Tool Loop Management

When an agent uses tools, agstack automatically manages the request-response loop:

1. Agent receives user query
2. Agent decides to call a tool
3. Tool executes and returns result
4. Result is added to conversation
5. Agent continues with tool result
6. Process repeats until agent gives final answer (up to `max_turns`)

This is handled automatically - you don't need to manage the loop.

## Token Usage Tracking

```python
response = await agent.run(context)

usage = context.usage
print(f"Prompt tokens: {usage.prompt_tokens}")
print(f"Completion tokens: {usage.completion_tokens}")
print(f"Total tokens: {usage.total_tokens}")
```

## Error Handling

```python
from agstack.llm.flow.exceptions import FlowError

try:
    agent = create_agent("my_agent")
    response = await agent.run(context)
except FlowError as e:
    print(f"Agent execution failed: {e.error_key}")
except RuntimeError as e:
    print(f"Agent not found: {e}")
```

## Best Practices

1. **Clear Instructions**: Write specific, detailed system prompts
2. **Appropriate Tools**: Only provide tools the agent actually needs
3. **Model Selection**: Use gpt-4o for complex reasoning, lighter models for simple tasks
4. **Error Handling**: Always handle FlowError and RuntimeError
5. **Token Limits**: Set max_tokens to prevent excessive responses
6. **Temperature**: Use lower values (0.1-0.3) for consistent results, higher (0.7-0.9) for creative tasks
7. **max_turns**: Set appropriate loop limit to prevent infinite tool calling

## Common Patterns

### Question-Answering Agent

```python
class QAAgent(Agent):
    def __init__(self):
        super().__init__(
            name="qa_agent",
            instructions="""You answer questions accurately and concisely.
            If you don't know the answer, say so. Don't make up information.""",
            model="gpt-4o",
            temperature=0.2,
            max_tokens=500
        )
```

### Task Automation Agent

```python
class AutomationAgent(Agent):
    def __init__(self):
        super().__init__(
            name="automation_agent",
            instructions="""You help automate tasks by breaking them down into steps
            and executing them using available tools. Be systematic and thorough.""",
            model="gpt-4o",
            tools=[tool1, tool2, tool3],
            temperature=0.3
        )
```

### Data Analysis Agent

```python
class AnalysisAgent(Agent):
    def __init__(self):
        super().__init__(
            name="analysis_agent",
            instructions="""You analyze data and provide insights. Use the database
            query tool to fetch data, then analyze and summarize findings.""",
            model="gpt-4o",
            tools=[db_query_tool, chart_tool],
            temperature=0.1
        )
```
