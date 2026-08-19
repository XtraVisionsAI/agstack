## 1.25.1 (2026-08-19)

## 1.25.0 (2026-07-31)

### Feat

- **llm**: add usage callback hook to LLMClient

## 1.24.1 (2026-06-01)

### Fix

- **agent**: make tool_choice configurable via instance attribute

## 1.24.0 (2026-05-29)

### Feat

- **flow**: add output_mode append, iterator state cleanup, and $items interpolation

## 1.23.0 (2026-05-28)

### Feat

- **flow**: add iterator node type for edge-driven array traversal

## 1.22.1 (2026-05-27)

### Fix

- **flow**: add stepId to STEP_STARTED/STEP_FINISHED events for instance-level pairing

## 1.22.0 (2026-05-27)

### Feat

- **config**: add optional YAML config file support

## 1.21.1 (2026-05-26)

### Fix

- **flow**: use ensure_ascii=False in json.dumps to preserve unicode output

## 1.21.0 (2026-05-24)

### Feat

- **flow**: add skill_progress start/end paired events for tool execution

## 1.20.0 (2026-05-21)

### Feat

- **flow**: persist tool result summary into message context

## 1.19.0 (2026-05-21)

### Feat

- **flow**: add FlowTrace structured execution tracing and declarative tool contracts

## 1.18.0 (2026-05-20)

### Feat

- **flow**: add tool execution observability via structured audit events

## 1.17.1 (2026-05-17)

### Fix

- **messagebus**: auto-start RedisSubscription on __aenter__ and __anext__

## 1.17.0 (2026-05-17)

### Feat

- add cache and messagebus modules with memory/redis backends

### Refactor

- **deps**: move infra dependencies to optional extras

## 1.16.2 (2026-05-16)

### Fix

- **security**: truncate HMAC result to 72 bytes for bcrypt 5.0 compatibility

## 1.16.1 (2026-05-13)

### Fix

- **flow**: message node reads label/echo from config instead of hardcoding

## 1.16.0 (2026-05-12)

### Feat

- **flow**: structured output, AG-UI visibility metadata, variable reference resolution

## 1.15.0 (2026-05-12)

### Feat

- **flow**: expression-based edge conditions, graph-level cycles, parallel auto-merge

## 1.14.0 (2026-05-08)

### Feat

- **flow**: support agent parameter overrides from flow node config

## 1.13.0 (2026-05-08)

### Feat

- **flow**: add echo node handler for streaming text without LLM calls

## 1.12.0 (2026-05-07)

### Feat

- **flow**: replace display_name with label/echo for AG-UI event visibility control

## 1.11.0 (2026-05-07)

### Feat

- **flow**: add display_name support to Tool, Agent, and FlowRegistry

## 1.10.2 (2026-05-05)

### Fix

- **flow**: use single namespace in python node sandbox so imports are visible to main()

## 1.10.1 (2026-05-05)

### Fix

- **flow**: expose common Python builtins in sandbox environment

## 1.10.0 (2026-05-05)

### Feat

- **flow**: add switch and subflow node types

## 1.9.0 (2026-03-27)

### Feat

- **flow**: llm_embed_node supports dynamic model via inputs
- **flow**: llm_rerank_node supports dynamic model/top_n via inputs
- **flow**: llm_chat_node supports dynamic model/temperature/max_tokens via inputs
- **flow**: detect_node supports dynamic instruction/options/model/temperature via inputs

### Fix

- **chore**: update sqlobjects to 1.9.0
- **flow**: llm_chat_node max_tokens fallback uses is not None
- **flow**: detect_node options fallback uses is not None to handle empty list

## 1.8.4 (2026-03-26)

### Fix

- **flow**: tool node now raises on failure instead of returning None

## 1.8.3 (2026-03-20)

### Fix

- replace passlib with bcrypt to fix __about__ attribute error

## 1.8.2 (2026-03-18)

### Fix

- **fastapi**: disable docs, redoc, and openapi.json endpoints in non-debug mode

## 1.8.1 (2026-03-18)

### Fix

- **events**: log exceptions from event handlers instead of silently swallowing them

## 1.8.0 (2026-03-16)

### Feat

- **registry**: replace global Registry with self-contained FlowRegistry

## 1.7.0 (2026-03-12)

### Feat

- **llm/flow**: unify node input/output with $v./$o. references and dict returns

## 1.6.0 (2026-03-11)

### Feat

- **llm/flow**: add NodeHandler plugin system with built-in capability nodes

## 1.5.0 (2026-03-11)

### Feat

- **llm/flow**: add node-level retry with error events, routing and python sandbox

## 1.4.0 (2026-03-11)

### Feat

- **llm/flow**: add parallel, iteration, loop, and python node types

## 1.3.0 (2026-03-10)

### Feat

- **llm/flow**: add edge-driven execution with condition and message node support

## 1.2.3 (2026-03-10)

### Fix

- **security/casbin**: correct table name to system_casbin_rules
- **llm/client**: add retry and proper error handling for rerank endpoints

## 1.2.2 (2026-03-10)

### Refactor

- **llm/flow**: replace ag-ui-protocol dep with internal event module

## 1.2.1 (2026-03-09)

### Fix

- **llm/flow**: inject tool arguments into context and handle pre-instantiated tools

## 1.2.0 (2026-03-08)

### Feat

- add contexts module and propagate request_id to LLM calls

## 1.1.0 (2026-03-02)

### Feat

- **decorators**: add type annotations to with_session decorator

## 1.0.8 (2026-02-26)

### Fix

- **config/logger**: remove redundant child logger cleanup in setup_logger

## 1.0.7 (2026-02-26)

### Fix

- **db**: prevent SQLAlchemy from auto-adding StreamHandler
- **security/casbin**: use SQLObjects syntax to query data
- **infra/es**: explicitly set connection alias and handle shutdown errors

## 1.0.6 (2026-02-25)

## 1.0.5 (2026-02-25)

### Fix

- **common/logger**: wrong type define for loguru type check

## 1.0.4 (2026-02-25)

### Fix

- **infra/db**: prevent SQLAlchemy from adding default StreamHandler

## 1.0.3 (2026-02-14)

## 1.0.2 (2026-02-09)

### Feat

- implement NebulaGraph operations

## 1.0.1 (2026-01-30)

### Feat

- add nebula support and infra events
- init commit

### Refactor

- correct typo of method name

### Perf

- improve casbin adapter init performance
