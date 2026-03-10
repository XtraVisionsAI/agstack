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
