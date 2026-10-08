# Harness Runtime (2.3.0+, replay 3.1)

`agstack.genai.harness` collects runtime pieces that are model-, table- and product-agnostic. Storage is injected by the
application through ports; agstack itself never touches a database or file system.

## Ports (SPI)

```python
from agstack.genai.harness import register_ports, get_ports, LogEvent, SessionLog, SpillStore, SpillOwner, SpillRef

register_ports(session_log=MySessionLog(), spill=MySpillStore(), usage=my_usage_sink)
get_ports().spill          # None when not registered — consumers degrade gracefully
```

| Port | Methods | Notes |
|---|---|---|
| `SessionLog` | `append(session_id, events) -> last seq`, `read(session_id, after_seq=-1, limit=)` (events with `seq > after_seq`), `latest_anchor(session_id)` | Append-only log of **one run** (`session_id` is the task id since 3.1); `seq` is the EventHub sequence of that run — no clock, no cross-process contention. Retries / overflow recovery / folds never delete rows: write a new row and shadow the old one in the application's own table (`SHADOW_KINDS` is shared vocabulary; the port has no `shadow` method since 3.1). Projections read only `shadowed_by is None`. |
| `SpillStore` | `save_text(owner, source, name, content) -> SpillRef`, `read_text(locator, offset=, limit=)` | `locator` is opaque to the model; permissions belong to the implementation. |
| `UsageSink` | callable `(UsageEvent) -> None` | Same callback as `set_usage_callback`; `register_ports(usage=...)` keeps both in sync. |
| `KVStore` | `get(scope, key)`, `put(scope, key, value)` | Optional. |

`LogEvent{kind, role, content, metadata, seq, shadowed_by: str | None}`; `kind` ∈ `LOG_KINDS` (message, event, tool_call,
tool_result, summary, fold, attempt, system_snapshot, phase_marker). `TokenAnchor{anchor_ref, prompt_tokens, model}` locates
the anchor by a row reference (usually a message id). `clear_ports()` resets everything (tests).

## Truncation

```python
from functools import partial
from agstack.genai.llm.token import count_tokens
from agstack.genai.harness import truncate_middle, clamp_results

counter = partial(count_tokens, model="qwen3")
text = truncate_middle(text, max_tokens=4000, count_tokens=counter)             # keep head + tail, insert a size marker
items, dropped = clamp_results(items, per_item_max=4000, total_max=8000, count_tokens=counter)
```

`clamp_results` truncates each item's `content`, then drops whole items from the lowest `relevance_score` until the total
fits; the caller reports `dropped` to the model explicitly. Limits are policy and belong to the application.

## Spill hook

```python
from agstack.genai.harness import SpillHook, SpillPolicy, SpillOwner

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
from agstack.genai.llm.hooks import LLMCallHook, CallMeta, StreamSummary

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

`agstack.genai.flow.guards`: mechanism in the library, policy in the app. `AgentGuards(capped_family, cap, checks, hint,
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

## Overflow recovery (3.0)

`agstack.genai.harness.overflow`: `classify_overflow(error= | finish_reason=, prompt_tokens=, completion_tokens=,
context_length=)` returns `"error"` (backend message says the context window was exceeded; rate limits are excluded),
`"silent"` (request succeeded but reported `prompt_tokens` exceed the window) or `"length"` (`finish_reason="length"`,
no output, input ≥ 99% of the window), else `None`. `OverflowPolicy(compact=, context_length=, max_recoveries=1)`
is attached to `Agent.overflow` (constructor kwarg or class attribute): when a turn hits one of the three states before
any text / tool call has been streamed, the agent yields `CUSTOM agent_overflow {agentName, kind, turn}`, awaits the
app's `compact(context, kind) -> bool` (fold / summarize / shadow `context.history` and the agent's messages), and
re-sends the same turn once if it returned True. A second overflow, an ineffective compaction or a missing policy
fall through to the original error. `OVERFLOW_RECORD` is the suggested `tool_name` for the app's execution record.

## Context compression engine (3.0)

`agstack.genai.harness.context`: `ContextEngine(source, summaries, summarize, count, context_length, model, anchor=,
policy=ContextPolicy(...), session_key=)`. `await engine.build_history(reserved_tokens)` returns the model history for
one turn: budget = `context_length × (1 − output_ratio) − reserved`; the most recent `recent_limit` rows go out whole
when they fit (fast path, `engine.history_span` records `{first_id, last_id}` for the next anchor); otherwise an
existing summary is consumed read-only (`[summary_header]\nrender(content)` + rows after it filled to the remaining
budget) and a missing / stale summary is handed to `policy.schedule(session_key, engine.refresh_summary)` — the request
path never calls the model. History tokens use the anchor when valid (same model, anchor tail still in window, anchor
head at window start or recoverable from the fetched superset) and fall back to estimation otherwise.

The application implements `HistorySource` (`recent(limit) -> (window asc, superset newest-first)`, `after(ref)`,
`all_rows()`, `count()`, `project(rows)`, `ref_of(row)`) and `SummaryStore` (`latest()`, `save(record)`), and supplies
`summarize(messages, previous) -> str | None`. `refresh_summary()` does a full summary (rows up to the last
`keep_recent`) when none exists and the row count reaches `summary_min_messages`, or an incremental one (new rows since
the summary, `previous` = old content) once `incremental_threshold` new rows accumulate. `schedule_once(key, factory)` is
the default scheduler (per-key dedup, empty `contextvars.Context`); `fill_to_budget(messages, budget, count,
user_ratio=)` is the two-pass fill (users first) usable on its own.

## Replay and crash takeover (3.1)

`agstack.genai.harness.replay` replaces "serialize the FlowContext" with "serialize the events that produced it". Long
jobs append `phase_marker(phase, state)` at phase boundaries and `attempt(reason, detail=)` for failed tries (kept for
audit, never projected into history). After a crash the reader calls `recover(events, pending_tool_calls=)` on the run's
log: it yields `RecoveryState{phase, state, marker_seq, last_seq, after, orphan_tool_calls, attempts, closed}` — the last
marker and its state, the events after it, `tool_call` rows without a matching `tool_result` (`ORPHAN_STARTED`) and
checkpoint-pending call ids with no start row (`ORPHAN_UNRECORDED`). `closers(state, reason=)` synthesizes one
`tool_result` error per orphan (the message tells the model whether the tool may already have run) plus a final
`RUN_ERROR{code: Interrupted, phase, orphan_tool_calls}`, all flagged `metadata.synthetic` (`is_synthetic`); a run that
already has `RUN_FINISHED / RUN_ERROR` is `closed` and gets no closers. Writers never truncate or rewrite; repair happens
only on the read side and is appended as ordinary events. `EventHub.resume(task_id, next_seq)` seeds the sequence for a
task this process never began (take `max(seq)+1` from storage) without rewinding a running one.

## Package layout since 3.0

The generative-AI layers live under `agstack.genai` with one-way dependencies: `agstack.genai.llm` keeps model access only
(`client`, `hooks`, `prompts`, `token`); `agstack.genai.flow` is orchestration (depends on llm); `agstack.genai.harness` is the
runtime (depends on llm and flow). The 2.x paths `agstack.llm`, `agstack.llm.flow` and `agstack.llm.harness` are gone — a
breaking change shipped with the major, no aliases. `LLMCallHook` stays in `agstack.genai.llm.hooks` (the client weaves it in
and must not depend on harness) and is re-exported from `agstack.genai.harness`. `harness.tokens` is renamed
`harness.metering` (calibration and anchors), distinct from `llm.token` (tiktoken counting).
