# Harness Runtime (2.3.0+)

`agstack.llm.harness` collects runtime pieces that are model-, table- and product-agnostic. Storage is injected by the
application through ports; agstack itself never touches a database or file system.

## Ports (SPI)

```python
from agstack.llm.harness import register_ports, get_ports, LogEvent, SessionLog, SpillStore, SpillOwner, SpillRef

register_ports(session_log=MySessionLog(), spill=MySpillStore(), usage=my_usage_sink)
get_ports().spill          # None when not registered — consumers degrade gracefully
```

| Port | Methods | Notes |
|---|---|---|
| `SessionLog` | `append(session_id, events) -> last seq`, `read(session_id, after_seq=, limit=)`, `shadow(session_id, target_seqs, by_seq, kind)`, `latest_anchor(session_id)` | Append-only log. Retries / overflow recovery / folds never delete rows: write a new event and shadow the old one (`SHADOW_KINDS`). Projections read only `shadowed_by is None`. |
| `SpillStore` | `save_text(owner, source, name, content) -> SpillRef`, `read_text(locator, offset=, limit=)` | `locator` is opaque to the model; permissions belong to the implementation. |
| `UsageSink` | callable `(UsageEvent) -> None` | Same callback as `set_usage_callback`; `register_ports(usage=...)` keeps both in sync. |
| `KVStore` | `get(scope, key)`, `put(scope, key, value)` | Optional. |

`LogEvent{kind, role, content, metadata, seq, shadowed_by}`; `kind` ∈ `LOG_KINDS` (message, event, tool_call, tool_result,
summary, fold, attempt, system_snapshot, phase_marker). `clear_ports()` resets everything (tests).

## Truncation

```python
from functools import partial
from agstack.llm.token import count_tokens
from agstack.llm.harness import truncate_middle, clamp_results

counter = partial(count_tokens, model="qwen3")
text = truncate_middle(text, max_tokens=4000, count_tokens=counter)             # keep head + tail, insert a size marker
items, dropped = clamp_results(items, per_item_max=4000, total_max=8000, count_tokens=counter)
```

`clamp_results` truncates each item's `content`, then drops whole items from the lowest `relevance_score` until the total
fits; the caller reports `dropped` to the model explicitly. Limits are policy and belong to the application.

## Spill hook

```python
from agstack.llm.harness import SpillHook, SpillPolicy, SpillOwner

policy = SpillPolicy(
    max_inline_tokens=6000,
    count_tokens=counter,
    owner_of=lambda ctx: SpillOwner(user_id=ctx.user_id, session_id=ctx.thread_id),
    exclude_tools=frozenset({"read_spill"}),     # the read-back tool must be excluded
)
registry.register_tool_hook(SpillHook(policy), prepend=True)   # outermost post hook
```

When a tool's `content` exceeds `max_inline_tokens`, the full text goes to `SpillStore`, the inline content becomes
head + notice (with the locator) + tail, and `ToolResult.metadata["spill"]` records the reference. Without a registered
`SpillStore` the hook is a no-op; a store failure only logs a warning and keeps the content inline. `result.result` is never
reshaped.

## LLM call hooks (F6)

```python
from agstack.llm.hooks import LLMCallHook, CallMeta, StreamSummary

class Compressor(LLMCallHook):
    async def before_call(self, messages, tools, meta: CallMeta):   # may rewrite the message list; exceptions fail closed
        return messages
    async def after_call(self, response, meta: CallMeta):          # observe; exceptions are logged and ignored
        ...

registry.register_llm_hook(Compressor(), prepend=True)
```

Woven into `LLMClient.chat` (async, streaming included; `chat_sync` is not hooked). `CallMeta.extra` carries
`agent / turn / retry / context` from the agent loop, or whatever a direct caller passes as `hook_meta={...}`.
Non-streaming `after_call` receives the `ChatCompletion`; streaming receives `StreamSummary(usage, finish_reason)`.

## Tool post-hook semantics since 2.3

`result.content` is rendered **before** the post chain, so hooks can rewrite what the model sees. A hook that replaces
`result.result` without providing a new `content` gets `content` re-rendered from the new result. `ToolResult.metadata`
is a free-form dict for hook/tool side information that is not fed to the model.

## Agent guards (2.4)

`agstack.llm.flow.guards`: mechanism in the library, policy in the app. `AgentGuards(capped_family, cap, checks, hint,
count_tokens, fold_budget_ratio, fold_renderer, *_kind)` + `GuardState` (per-run counters, `cap` override) +
`GuardedToolCalls` mixin (placed before `Agent` in the MRO). Pre-execution: duplicate call → family cap → custom
`checks`; a hit is returned to the model as the tool message and recorded as an execution record shaped like a Tool
call. Post-execution: `hint` appended to the tool message, oldest tool results folded when the running total exceeds
`context_length // fold_budget_ratio` (the newest is never folded). `buffer_plan_text(events, context, agent_name=,
closing_line=)` wraps `Agent.stream`: text that precedes a tool call becomes an `agent_plan` record instead of being
streamed; exhausted turns / empty final turns end with `closing_line(messages)`.

## Events, projection, tokens (2.4)

- `EventHub(persist=, replay=, is_finished=)`: per-task monotonic sequence, `TaskSnapshot`, in-process subscribers
  (class-level, shared across instances), `publish()` and `stream(after_seq)` (replay then live, stops on
  RUN_FINISHED / RUN_ERROR). `filter_user_event(evt, tool_label=)` is the flow-event → user-event rule table.
- `Projection(event_of=, project_event=, event_skipper=, rewrite=, notes=)`.`project(rows)`: drops shadowed rows,
  projects event rows, rewrites bodies, attaches notes, merges consecutive same-role messages. `select_recent(rows,
  limit, is_dialogue_grade)` picks the window newest-first.
- `tokens`: `clamp_ratio`, `calibration_from_samples`, `calibration_sample`, `CalibratedCounter`, `anchored_estimate`.
