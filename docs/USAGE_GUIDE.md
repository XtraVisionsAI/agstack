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
from agstack.genai.flow import (
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
├── genai/            # 生成式 AI 三层（依赖单向 llm ← flow ← harness）
│   ├── llm/          # 模型接入：client / hooks（F6 调用钩子）/ prompts / token
│   ├── flow/         # Flow 执行框架（agent / tool / flow / context / registry / nodes / guards）
│   └── harness/      # 通用运行时（ports / events / projection / metering / overflow / context / spill）
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
| `registry` | 全局注册中心 | `from agstack.genai.flow import registry` |
| `Agent` | LLM 代理 | `from agstack.genai.flow import Agent` |
| `Tool` | 工具定义 | `from agstack.genai.flow import Tool` |
| `Flow` | 流程编排 | `from agstack.genai.flow import Flow` |

---

## 3. LLM Flow System

### 3.1 Tool (工具)

工具是可以被 Agent 调用的函数。

**创建工具**:

```python
from agstack.genai.flow import Tool, FlowContext

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
from agstack.genai.flow import registry

registry.register_tool("web_search", WebSearchTool)
```

**工具并发执行声明**（2.1.0+）:

LLM 一轮返回多个 tool_calls 时默认严格串行执行。工具可声明与其它同类工具并发执行：

```python
Tool(
    name="web_search",
    description="Search the web",
    function=search,
    concurrency_safe=True,   # 默认 False（fail closed：不声明就当不安全）
)
```

Agent 按 LLM 返回顺序扫描 tool_calls，把**连续的** `concurrency_safe` 调用聚成一组
`asyncio.gather` 并发执行，其余仍串行。约定：

- 组内各调用的事件（`TOOL_CALL_RESULT`、进度事件）按完成顺序发出，消费端靠 `toolCallId` 关联；
- tool 消息写回消息历史时按 tool_call 原始顺序排列（OpenAI 协议要求与 `assistant.tool_calls` 对应）；
- 组内单个调用失败不影响其它调用（沿用 `ToolResult(success=False)` 通道）；
- 并发期间 `execution_records` / `pending_custom_events` 的追加顺序不再确定（asyncio 单线程，无数据损坏）。

只把幂等、无共享可变状态依赖的工具（如只读检索）声明为 `concurrency_safe`。

**工具执行钩子**（2.1.0+）:

registry 级全局钩子链，在"工具入参进入工具函数前 / 结果落入上下文前"插入横切逻辑
（参数级权限门、统一审计、超长输出落盘 spill、结果尺寸硬上限、脱敏），
无需逐个包装工具。织入点在 `Tool.execute_async`——所有调用路径（agent 工具循环、
tool 节点、`tool.run()` 直调）的唯一咽喉，子类覆写 `_execute` 也绕不开。

```python
from agstack.genai.flow import Deny, ToolHook, registry

class KbPermissionGate(ToolHook):
    async def pre_execute(self, context, tool, inputs):
        if inputs.get("kb_id") not in allowed_kbs(context.user_id):
            return Deny("no permission for this kb")   # 拒绝：工具不执行
        return {**inputs, "tenant_id": resolve_tenant(context)}  # 或改写入参

class ResultSpill(ToolHook):
    async def post_execute(self, context, tool, result):
        if len(str(result.result)) > 65536:
            locator = spill_to_storage(result.result)
            result.result = {"spilled": True, "locator": locator}
        return result

registry.register_tool_hook(KbPermissionGate())
registry.register_tool_hook(ResultSpill(), prepend=True)   # 抢占链头：post 成为最外层
```

执行顺序与语义（洋葱模型）：

- `pre_execute` 按注册顺序、`post_execute` 按逆序执行；`prepend=True` 抢占链头。
- **pre 返回 Deny 或抛异常＝拒绝执行（fail closed）**：工具本体不执行，reason 转为
  `ToolResult(success=False)` 反馈给模型，模型可自行改道，flow 不中断。
  权限门自己出错时不放行。
- **post 抛异常＝记日志放行原结果（fail open）**：审计/截断钩子的 bug 不毁掉主流程。
- **Deny 的失败结果同样穿过 post 链**：审计钩子能看到全部结局，包括被拒绝的调用。
- post 改写对三个出口同时生效：喂给 LLM 的 content、面向用户的 summary 输入、
  `execution_records`。
- `execution_records` 与 trace 记录的是 **pre 链改写后的入参**（日志＝执行事实）。
- 钩子会被并发调用（`concurrency_safe` 工具组内同时穿链）：实现必须无状态或自行同步，
  与 Tool 单例的既有纪律同构。
- `registry.clear_tool_hooks()` 清空全局链（测试隔离用）。
- 未注册任何钩子时行为与 2.0.0 一致（零开销路径）。

明确不在本版范围（如有需要另行提案）：around/execute 阶段包装（timeout/retry 已有
节点级 retry）、ask 审批决策（将来做 HITL 时作为 Deny 之外的第三种返回值追加，
不破坏既有钩子）、按请求/租户的作用域过滤（如需采用"全局链 + `context.tool_hooks`
局部链合并"扩展，局部链不入序列化）、独立观察型通道（纯观察＝post 原样返回，
其失败已被 fail open 包容）。

### 3.2 Agent (代理)

Agent 是调用 LLM 并可以使用工具的智能代理。

**创建 Agent**:

```python
from agstack.genai.flow import Agent, FlowContext

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
from agstack.genai.flow import Flow, FlowContext

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

**协作式取消**（2.1.0+）:

`FlowContext` 提供取消原语，宿主可从任意位置（另一个 task、HTTP 断连回调）请求停止执行：

```python
context = FlowContext()
task = asyncio.create_task(flow.run(context))
...
context.cancel()          # 幂等；引擎在下一个检查点停止
context.is_cancelled      # 长 I/O 工具可自查以提前返回
```

- **取消是协作式的**：粒度是"下一个检查点"（flow 节点执行前、retry 重试前、agent LLM
  轮次开始、tool_call 执行前），不是即时中断。引擎不强杀在途的工具或 LLM 调用
  （避免半写状态），只保证不再开始新的工作。
- 停止形状：`stream()` 以 `RUN_ERROR`（code=`CANCELLED`）结束事件流；`run()` 抛
  `FlowExecutionError("FLOW_CANCELLED")`。
- 若取消到达时已无剩余工作（最后一个节点正常完成），flow 视为正常结束，不发取消事件。
- 不调用 `cancel()` 的现有代码零感知，行为不变。

### 3.4 FlowContext (上下文)

FlowContext 在执行过程中传递状态和数据。

```python
from agstack.genai.flow import FlowContext

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

### 3.5 LLM 调用钩子（2.3.0+）

registry 级全局钩子链，织入 `LLMClient.chat`（异步，含流式与 vision 转发；`chat_sync` 不穿链）。
`before_call` 在请求发出前拿到**完整消息列表**并可改写（压缩、复用前缀、注入快照），`after_call` 在响应
返回后观察（锚点计量、溢出判定、审计）。请求参数类覆盖（temperature / max_tokens / thinking）继续走
`Agent.request_overrides`，两者不重叠。

```python
from agstack.genai.flow import registry
from agstack.genai.llm.hooks import CallMeta, LLMCallHook, StreamSummary

class AnchorMeter(LLMCallHook):
    async def before_call(self, messages, tools, meta: CallMeta):
        return compact_if_needed(messages, meta.extra.get("context"))   # 必须返回消息列表

    async def after_call(self, response, meta: CallMeta):
        usage = response.usage                 # 非流式：openai ChatCompletion；流式：StreamSummary(usage, finish_reason)
        record_anchor(meta.extra.get("context"), usage)

registry.register_llm_hook(AnchorMeter())
registry.register_llm_hook(Compressor(), prepend=True)   # 抢链头：before 最先改写、after 最后观察
```

语义：

- `before_call` 按注册顺序、`after_call` 按逆序执行；`prepend=True` 抢占链头。
- **before 抛异常＝本次调用失败（fail closed）**：改写消息的钩子出错时不能让模型看到半成品上下文；异常原样向上抛。
- **after 抛异常＝记日志放行（fail open）**。
- `CallMeta`：`model` / `kind`（chat / chat_stream / vision …）/ `stream` / `request_id` / `extra`。Agent 工具循环
  每次请求在 `extra` 里给 `agent` / `turn` / `retry` / `context`；直接调 `client.chat` 的调用方可传 `hook_meta={...}`
  （client 弹出，不透传后端）。
- 未注册任何钩子时为零开销路径；`registry.clear_llm_hooks()` 清空（测试隔离）。

### 3.6 harness 运行时部件（2.3.0+）

`agstack.genai.harness` 收模型无关、表无关、产品无关的运行时部件；存储由应用实现端口注入。

**存储端口**：agstack 只声明 Protocol，应用在进程入口注册一次。

```python
from agstack.genai.harness import register_ports, get_ports, LogEvent, SessionLog, SpillStore

register_ports(session_log=PgSessionLog(), spill=FsSpillStore(), usage=usage_collector_callback)
get_ports().spill   # 未注册的端口为 None，消费方自行降级
```

- `SessionLog`：`append(session_id, events) -> 末 seq` / `read(after_seq, limit)` / `shadow(target_seqs, by_seq, kind)` /
  `latest_anchor`。`LogEvent{kind, role, content, metadata, seq, shadowed_by}`，kind 见 `LOG_KINDS`；重试 / 溢出恢复 /
  折叠**不删行**，写新行并遮蔽旧行（`SHADOW_KINDS`），投影只读 `shadowed_by is None` 的行。
- `SpillStore`：`save_text(owner, source, name, content) -> SpillRef(locator, chars, tokens, name)` / `read_text(locator,
  offset, limit)`。locator 对模型不透明，权限与密级由实现按 `SpillOwner` 判定。
- `UsageSink`：与 `set_usage_callback` 同一回调，`register_ports(usage=...)` 两处一致。
- `KVStore`：可选。

**截断设施**（`truncation`）：`truncate_middle(text, max_tokens, count_tokens)` 保头尾、中段换规模标注；
`clamp_results(results, per_item_max=, total_max=, count_tokens=)` 单条截断后按低相关度整条丢弃，返回丢弃数由调用方
显式告知模型。上限数值由应用按窗口比例给，`count_tokens` 按模型绑定（如 `functools.partial(count_tokens, model=...)`）。

**spill 钩子**（`spill`）：超长工具结果落盘的 ToolHook post 钩子，`prepend=True` 挂链头。

```python
from agstack.genai.harness import SpillHook, SpillPolicy, SpillOwner

policy = SpillPolicy(
    max_inline_tokens=6000,
    count_tokens=partial(count_tokens, model="qwen3"),
    owner_of=lambda ctx: SpillOwner(user_id=ctx.user_id, session_id=ctx.thread_id),
    exclude_tools=frozenset({"read_spill"}),          # 回读工具必须排除
)
registry.register_tool_hook(SpillHook(policy), prepend=True)
```

content 超 `max_inline_tokens` 时全文经 `SpillStore` 落盘，内联替换为头尾 + 固定格式通知（含 locator），落盘引用写进
`ToolResult.metadata["spill"]`；未注册 SpillStore 为空操作；**存储失败只 warn 保留内联**。`result.result` 形状不变。

**2.3 对 Tool post 链的语义补充**：进入 post 链时 `result.content` 已按 result_formatter 算好，钩子可直接改写
content；钩子若换掉 `result.result` 而未给新 content，Tool 按新 result 重算（2.1 的「改 result 即改模型所见」保留）。
`ToolResult` 新增 `metadata` 字段（钩子 / 工具附带的结构化信息，不喂给模型）。

### 3.7 守卫、事件、投影与计量（2.4.0+）

**AgentGuards**（`agstack.genai.flow.guards`）：工具调用的「红线在代码」层，机制在库、策略在应用。

```python
from agstack.genai.flow import AgentGuards, GuardState, GuardedToolCalls, buffer_plan_text

GUARDS = AgentGuards(
    capped_family=("retrieval", "web_search"), cap=5,        # 受上限的工具族与上限
    checks=(kb_first,),                                       # 应用自定义执行前守卫：(state, name, args) -> (kind, payload) | None
    hint=sufficiency_hint,                                    # 结果末尾附提示：(state, name, content) -> str | None
    count_tokens=count_tokens, fold_budget_ratio=3,           # 工具结果累计 > context_length // 3 时折叠最早一条
    fold_renderer=render_digest,                              # (context, content) -> 折叠后文本
    cap_kind="guard_retrieval_cap",                           # 守卫动作写执行记录时的 tool_name（审计呈现）
)

class MyAgent(GuardedToolCalls, Agent):                       # mixin 必须在 Agent 之前
    def __init__(self, **kw):
        self.guards, self.guard = GUARDS, GuardState()
        super().__init__(name="a", instructions="...", **kw)

    async def stream(self, context, inputs=None):
        self.guard.reset()                                    # 每次运行重置计数；guard.cap 可按次覆盖上限
        async for evt in buffer_plan_text(super().stream(context, inputs), context, agent_name=self.name,
                                          closing_line=my_closing_line):
            yield evt
```

执行前：重复调用（同名同参，参数 JSON 规范化比较）→ 工具族上限 → 自定义 checks，命中即把 payload 作为 tool 消息退给
模型并写一条与 Tool 管线同形的执行记录（`tool_name` 为守卫种类）；执行后：附提示、按预算折叠（最新一条永不折叠，
`_folded` 标记不重复处理）。`buffer_plan_text` 按轮缓冲助手文字：轮以工具调用结束的文字记为 `agent_plan` 执行记录
不放流（超过 `buffer_chars` 后实时放流），轮次耗尽或末轮无文字按 `closing_line(messages)` 收尾并改写输出 `result`。

**EventHub**（`agstack.genai.harness.events`）：任务事件的序号 / 快照 / 订阅 / 重放。持久化不走端口，由应用注入三个协程：

```python
from agstack.genai.harness import EventHub, filter_user_event

hub = EventHub(persist=save_event, replay=load_events, is_finished=task_done)   # (task_id, seq, evt) / (task_id, after_seq) / (task_id)
hub.begin(task_id)
async for evt in flow.stream(context):
    user_evt = filter_user_event(evt, tool_label=registry.get_tool_label)    # 规则表：_echo / _label / CUSTOM / RUN_*
    if user_evt is not None:
        await hub.publish(task_id, user_evt)                                  # 序号 → persist → 快照 → 订阅者
hub.end(task_id)
# SSE 重连
async for evt in hub.stream(task_id, after_seq=n): ...                        # replay 后接实时，终止事件即止
```

订阅者 / 快照 / 序号是进程级状态，同进程内任意 `EventHub()` 实例共享。

**Projection**（`agstack.genai.harness.projection`）：日志行 → 模型历史，行形状与 `LogEvent` 同（ORM 行直接可用）。

```python
from agstack.genai.harness import Projection, select_recent

proj = Projection(event_of=..., project_event=..., event_skipper=..., rewrite=..., notes=...)
history = proj.project(select_recent(rows_newest_first, limit, is_dialogue_grade))
```

遮蔽行（`shadowed_by` 非空）一律过滤；事件行按 `project_event` 投影或丢弃，`event_skipper(rows)` 用整批行构造跨行判定；
`rewrite(role, content, metadata)` 改写正文；`notes(role, metadata)` 附带 system note；末尾连续同角色 user / assistant 合并。

**tokens**（`agstack.genai.harness.metering`）：`clamp_ratio(estimated, actual, bounds=(0.8, 1.5))`、
`calibration_from_samples(samples, model)`（取第一条同模型有效样本）、`calibration_sample(...)`、
`CalibratedCounter(count_tokens, model, ratio)`、`anchored_estimate(anchor, tail_tokens, fallback_tokens=)`。

### 3.8 上下文溢出恢复（3.0+）

**OverflowPolicy**（`agstack.genai.harness.overflow`）：请求把窗口撑爆时的三态判定与一次 compact-and-retry，
机制在库、压缩动作在应用。

```python
from agstack.genai.harness import OverflowPolicy, OVERFLOW_RECORD

async def compact(context, kind) -> bool:                      # kind ∈ {"error", "silent", "length"}
    folded = fold_tool_messages(context, "a", budget_tokens=0, model=..., count_tokens=...)   # 折叠工具结果
    dropped = context.history[: len(context.history) // 2]     # 再砍历史前半（或换成同步摘要）
    del context.history[: len(dropped)]
    add_trace_record(context, OVERFLOW_RECORD, args={"kind": kind}, result=f"folded={folded} dropped={len(dropped)}")
    return bool(folded or dropped)                             # False → 不重发，按原路报错

OVERFLOW = OverflowPolicy(compact=compact, context_length=lambda c: c.get_variable("context_length"))

class MyAgent(Agent):
    overflow = OVERFLOW                                        # 类属性缺省；构造参数 overflow= 可覆盖
```

三态：后端报错文本命中「context length / maximum context / prompt is too long / too many tokens …」（限流不算）→
`error`；请求成功但实报 `prompt_tokens` > 窗口 → `silent`；`finish_reason="length"` 且无输出且输入 ≥ 99% 窗口 →
`length`。命中且本次尝试尚未向用户放出文字 / 工具调用时：yield `CUSTOM agent_overflow {agentName, kind, turn}` →
`await compact(context, kind)` → 有成效即同一轮重发；每次运行最多 `max_recoveries`（缺省 1）次，再溢出按原路报
`AGENT_EXECUTION_FAILED`。`classify_overflow(...)` 可单独调用。

### 3.9 上下文压缩引擎（3.0+）

**ContextEngine**（`agstack.genai.harness.context`）：在 token 预算内构造一轮请求的模型历史。机制在库，历史从哪张表来、摘要
怎么生成 / 落库、阈值取多少由应用给。

```python
from agstack.genai.harness import ContextEngine, ContextPolicy, SummaryRecord, schedule_once

class TopicHistory:                      # 实现 HistorySource：recent / after / all_rows / count / project / ref_of
    ...

class TopicSummaries:                    # 实现 SummaryStore：latest / save
    ...

async def summarize(messages, previous):  # 调模型出摘要正文；previous 为旧摘要（增量合并）或 None
    ...

engine = ContextEngine(
    source=TopicHistory(topic_id), summaries=TopicSummaries(topic_id), summarize=summarize,
    count=lambda text: count_tokens(text, model) * ratio, context_length=32768, model=model,
    anchor=last_anchor,                  # 上轮 {first_id, last_id, history_tokens, model}，无则 None
    policy=ContextPolicy(output_ratio=0.25, recent_limit=20, summary_min_messages=16,
                         incremental_threshold=10, keep_recent=6, render=render_summary, schedule=schedule_once),
    session_key=topic_id,
)
history = await engine.build_history(reserved_tokens=prompt_tokens)
span = engine.history_span               # 快速路径整窗送出时的行跨度，与实测 prompt_tokens 配对落下一轮锚点
```

顺序：预算分层 → 最近 N 条快速路径（锚点计量算窗内 token）→ 预算不足只读消费已有摘要（缺失 / 过期投递
`policy.schedule(session_key, engine.refresh_summary)`，请求路径不调模型）→ 两遍装填（先 user 消息到 `user_ratio`）。
`refresh_summary()` 是后台任务体：无摘要且行数 ≥ `summary_min_messages` 全量摘要（留最近 `keep_recent` 条），有摘要
且新行 ≥ `incremental_threshold` 增量摘要。`fill_to_budget` / `anchored_history_tokens` / `messages_tokens` 可单独用。

### 3.10 包布局（3.0 起）

生成式 AI 三层收在 `agstack.genai` 下，依赖单向：`agstack.genai.llm` 只留模型接入（`client` / `hooks` / `prompts` / `token`），
编排是 `agstack.genai.flow`（依赖 llm），运行时是 `agstack.genai.harness`（依赖 llm 与 flow）。2.x 的 `agstack.llm`、
`agstack.llm.flow`、`agstack.llm.harness` 已移除，不留别名（破坏性变更随 major 发布，下游一次改完）。`LLMCallHook`
留在 `agstack.genai.llm.hooks`（client 织入它，不能反向依赖 harness），`agstack.genai.harness` 重导出。
`harness.tokens` 改名 `harness.metering`（校准与锚点计量），与 `llm.token`（tiktoken 计数）区分。

## 4. Registry & Factory

### 4.1 Registry (注册中心)

**用途**: 管理所有组件的注册和创建。

**API**:

```python
from agstack.genai.flow import registry

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
from agstack.genai.flow import create_tool, create_agent

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
from agstack.genai.flow.exceptions import (
    FlowError,
    AgentError,
    ToolExecutionError,
    ModelError,
    FlowConfigError
)
```

### 6.2 捕获异常

```python
from agstack.genai.flow import create_tool
from agstack.genai.flow.exceptions import ToolExecutionError

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
from agstack.genai.flow.exceptions import FlowError

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
    from agstack.genai.flow import registry
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
from agstack.genai.flow import registry
from agstack.genai.flow.exceptions import ToolExecutionError

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
from agstack.genai.flow import Tool, FlowContext

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
from agstack.genai.flow import (
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
from agstack.genai.flow.exceptions import (
    FlowError,
    AgentError,
    ToolExecutionError,
)

# 状态管理
from agstack.genai.flow import FlowState, Record, Status
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
