# AgStack Development Rules

> **Version**: 1.0.0  
> **Last Updated**: 2026-01-27  
> **Purpose**: 规范 AgStack 项目的开发过程，确保代码质量和一致性

## 📋 Table of Contents

- [1. Import Rules](#1-import-rules)
- [2. Type System](#2-type-system)
- [3. Module Design](#3-module-design)
- [4. Code Quality](#4-code-quality)
- [5. Naming Conventions](#5-naming-conventions)
- [6. Error Handling](#6-error-handling)
- [7. Testing](#7-testing)

---

## 1. Import Rules

### 1.1 使用相对导入

**规则**: 项目内部模块必须使用相对导入，不依赖包名。

**原因**: 支持包重命名，提高可移植性。

```python
# ✅ 正确
from ...schema import BaseSchema
from .registry import registry
from ..exceptions import AppException

# ❌ 错误
from agstack.schema import BaseSchema
from agstack.llm.flow.registry import registry
```

### 1.2 导入位置

**规则**: 所有运行时导入必须在文件顶部。

```python
# ✅ 正确
from typing import TYPE_CHECKING, Any
from pydantic import Field
from ...schema import BaseSchema

if TYPE_CHECKING:
    from .context import FlowContext  # 仅类型提示

class MyClass:
    def method(self):
        # 不要在这里导入
        pass
```

**例外**: 仅在需要避免循环导入时，才在 `TYPE_CHECKING` 块中导入。

### 1.3 导入顺序

**规则**: 按以下顺序组织导入，每组之间空一行。

1. 标准库
2. 第三方库  
3. 项目内模块（使用相对导入）
4. `TYPE_CHECKING` 块

```python
# 标准库
import uuid
from datetime import datetime
from typing import Any

# 第三方库
from pydantic import Field

# 项目内模块
from ...schema import BaseSchema
from .events import EventType

# 类型提示
if TYPE_CHECKING:
    from .context import FlowContext
```

---

## 2. Type System

### 2.1 选择正确的数据类型

| 场景 | 使用 | 原因 |
|------|------|------|
| 轻量级内部数据传递 | `@dataclass` | 性能最优，无验证开销 |
| 需要验证和序列化的实体 | `BaseSchema` | 统一配置，自动序列化 |
| API 请求/响应模型 | `BaseSchema` | datetime/UUID 自动编码 |

### 2.2 BaseSchema vs BaseModel

**规则**: 所有 Pydantic 模型必须继承 `BaseSchema`，而非直接继承 `BaseModel`。

```python
# ✅ 正确
from ...schema import BaseSchema

class Record(BaseSchema):
    """统一执行记录"""
    id: str
    start_time: datetime

# ❌ 错误
from pydantic import BaseModel

class Record(BaseModel):  # 缺少项目统一配置
    id: str
    start_time: datetime
```

**原因**:
- 统一的 datetime 编码（ISO 8601）
- 统一的 UUID 处理
- `extra="ignore"` 容错性
- `from_attributes=True` ORM 支持

### 2.3 dataclass 使用场景

**规则**: 仅用于内部数据传递，不需要序列化的场景。

```python
# ✅ 正确 - 工具执行结果（内部传递）
from dataclasses import dataclass

@dataclass
class ToolResult:
    name: str
    success: bool
    result: Any

# ❌ 错误 - 需要序列化到 API 或数据库
@dataclass  # 应该用 BaseSchema
class Record:
    id: str
    data: dict
```

---

## 3. Module Design

### 3.1 Registry Pattern

**规则**: 使用统一的 registry 注册和管理组件。

```python
# 注册组件
from agstack.llm.flow import registry

registry.register_tool("my_tool", MyToolClass)
registry.register_agent("my_agent", MyAgentClass)

# 创建实例（返回 None 如果不存在）
tool = registry.create_tool("my_tool")
if tool:
    await tool.run(context)
```

### 3.2 Factory Pattern

**规则**: 使用 factory 函数用于确信组件存在的场景。

```python
from agstack.llm.flow import create_tool, create_agent

# 失败时抛出 RuntimeError
tool = create_tool("my_tool")  # 确信存在
agent = create_agent("my_agent")
```

**对比**:
- `registry.create_*()`: 返回 None，需要检查
- `create_*()`: 抛出异常，快速失败

### 3.3 模块职责

**规则**: 每个模块应有单一明确的职责。

- `registry.py`: 组件注册和创建
- `factory.py`: 便捷工厂函数
- `exceptions.py`: 异常定义
- `schema.py`: 数据模型基类

---

## 4. Code Quality

### 4.1 工具配置

**必需工具**:
- `ruff`: 代码格式化和 linting
- `pyright`: 类型检查
- `pre-commit`: 提交前检查

### 4.2 代码规范

```toml
# 已配置在 pyproject.toml
[tool.ruff]
line-length = 120
target-version = "py312"

[tool.pyright]
pythonVersion = "3.12"
```

**关键规则**:
- 行长度: 最大 120 字符
- Python 版本: 3.12+
- 类型提示: 必须
- Docstring: 公共 API 必须

### 4.3 提交前检查

```bash
# 运行所有检查
ruff check .
ruff format .
pyright
```

---

## 5. Naming Conventions

### 5.1 命名风格

| 类型 | 风格 | 示例 |
|------|------|------|
| 模块/包 | snake_case | `llm_flow`, `tool_result` |
| 类 | PascalCase | `BaseSchema`, `FlowContext` |
| 函数/方法 | snake_case | `create_tool`, `execute_async` |
| 常量 | UPPER_CASE | `MAX_RETRIES` |
| 私有变量 | _snake_case | `_internal_state` |

### 5.2 类型提示变量

```python
# 泛型类型变量
DataT = TypeVar("DataT")
ModelT = TypeVar("ModelT")

# 避免使用单字母（除非是通用泛型）
# ✅ 正确
UserT = TypeVar("UserT")

# ❌ 错误
T = TypeVar("T")  # 太通用
```

---

## 6. Error Handling

### 6.1 异常层次

**规则**: 使用项目定义的异常层次。

```
AppException (agstack.exceptions)
└── FlowError (agstack.llm.flow.exceptions)
    ├── AgentError
    │   ├── ToolExecutionError
    │   └── ModelError
    ├── FlowConfigError
    ├── FlowExecutionError
    └── NodeExecutionError
```

### 6.2 抛出异常

```python
# ✅ 正确 - 使用具体的异常类型
from .exceptions import FlowError

if not config:
    raise FlowError("MISSING_CONFIG", 400, {"field": "config"})

# ❌ 错误 - 使用通用异常
if not config:
    raise ValueError("Missing config")  # 难以捕获和处理
```

### 6.3 异常处理

```python
# ✅ 正确 - 捕获具体异常
from .exceptions import ToolExecutionError

try:
    result = await tool.run(context)
except ToolExecutionError as e:
    logger.error(f"Tool execution failed: {e}")
    # 处理工具执行错误

# ❌ 错误 - 捕获过于宽泛
try:
    result = await tool.run(context)
except Exception:  # 太宽泛
    pass
```

---

## 7. Testing

### 7.1 测试文件组织

```
tests/
├── unit/
│   ├── test_registry.py
│   └── test_factory.py
├── integration/
│   └── test_flow_execution.py
└── conftest.py
```

### 7.2 测试命名

```python
# 测试函数命名: test_<功能>_<场景>_<期望结果>
def test_create_tool_with_valid_name_returns_tool():
    pass

def test_create_tool_with_invalid_name_raises_error():
    pass
```

### 7.3 Fixture 使用

```python
import pytest

@pytest.fixture
def flow_context():
    """创建测试用 FlowContext"""
    return FlowContext(session_id="test")

def test_tool_execution(flow_context):
    tool = create_tool("test_tool")
    result = await tool.run(flow_context)
    assert result.success
```

---

## 📚 Quick Reference

### 常见模式速查

```python
# 1. 创建 Pydantic 模型
from ...schema import BaseSchema

class MyModel(BaseSchema):
    field: str

# 2. 创建内部数据类
from dataclasses import dataclass

@dataclass
class InternalData:
    value: Any

# 3. 注册组件
from .registry import registry

registry.register_tool("name", ToolClass)

# 4. 创建组件（安全）
tool = registry.create_tool("name")
if tool:
    await tool.run(context)

# 5. 创建组件（快速失败）
from .factory import create_tool

tool = create_tool("name")  # 抛异常如果不存在
await tool.run(context)

# 6. 相对导入
from ...module import Class     # 向上 3 层
from ..module import Class      # 向上 2 层
from .module import Class       # 同级
```

---

## ✅ Checklist

在提交代码前，确认：

- [ ] 使用相对导入（无 `from agstack.xxx`）
- [ ] 导入在文件顶部（无方法内导入）
- [ ] Pydantic 模型继承 `BaseSchema`
- [ ] 添加了类型提示
- [ ] 运行了 `ruff check` 和 `ruff format`
- [ ] 运行了 `pyright` 类型检查
- [ ] 添加了必要的 docstring
- [ ] 使用正确的异常类型

---

**Note**: 这些规则基于项目的实际架构和最佳实践，持续改进中。
