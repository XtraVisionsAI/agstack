# AgStack

> Production-ready toolkit for building FastAPI and LLM applications

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-orange.svg)](https://github.com/xtravisions/agstack)

## 📖 Overview

AgStack is a comprehensive Python framework designed for building production-ready FastAPI applications with integrated LLM capabilities. It provides:

- 🚀 **FastAPI Integration** - Production-ready web framework setup
- 🤖 **LLM Flow System** - Orchestrate Agents, Tools, and Flows
- 📦 **Component Registry** - Unified registration and factory pattern
- 🔐 **Security** - Built-in authentication and authorization (Casbin)
- 🗄️ **Infrastructure** - Database, Elasticsearch, and Message Queue support
- 📊 **Schema System** - Unified Pydantic models with enhanced serialization

## 🚀 Quick Start

### Installation

```bash
pip install agstack
```

### Basic Usage

```python
from agstack.llm.flow import (
    Tool,
    FlowContext,
    registry,
    create_tool
)

# Define a tool
class MyTool(Tool):
    def __init__(self):
        super().__init__(
            name="my_tool",
            description="My custom tool",
            function=self.execute
        )
    
    async def execute(self, context: FlowContext):
        return "Tool result"

# Register the tool
registry.register_tool("my_tool", MyTool)

# Use the tool
context = FlowContext(session_id="test")
tool = create_tool("my_tool")
result = await tool.run(context)
```

## 📚 Documentation

- **[Usage Guide](docs/USAGE_GUIDE.md)** - Learn how to use AgStack in your projects
- **[Development Rules](docs/DEVELOPMENT_RULES.md)** - Guidelines for contributing to AgStack
- **API Reference** - Coming soon
- **Examples** - Coming soon

## 🏗️ Architecture

### Project Structure

```
agstack/
├── schema.py          # Base Pydantic models
├── registry.py        # Global component registry
├── exceptions.py      # Exception hierarchy
├── llm/              # LLM and AI features
│   ├── client.py     # LLM client
│   └── flow/         # Flow execution framework
│       ├── agent.py  # Agent definition
│       ├── tool.py   # Tool definition
│       ├── flow.py   # Flow orchestration
│       └── ...
├── fastapi/          # FastAPI integration
├── infra/            # Infrastructure components
│   ├── db/           # Database
│   ├── es/           # Elasticsearch
│   └── mq/           # Message Queue
└── security/         # Security features
```

### Core Concepts

- **Agent** - LLM-powered intelligent agents
- **Tool** - Functions that can be called by agents
- **Flow** - Orchestration of multiple agents and tools
- **Registry** - Centralized component management
- **BaseSchema** - Enhanced Pydantic models with unified configuration

## 🛠️ Development

### Requirements

- Python >= 3.12
- Dependencies listed in `pyproject.toml`

### Setup

```bash
# Clone the repository
git clone https://github.com/xtravisions/agstack.git
cd agstack

# Install dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

### Code Quality

```bash
# Format code
ruff format .

# Lint
ruff check .

# Type check
pyright
```

### Contributing

Please read [DEVELOPMENT_RULES.md](docs/DEVELOPMENT_RULES.md) for development guidelines and coding standards.

## 📦 Features

### LLM Flow System

- **Agent System** - Create and manage LLM-powered agents
- **Tool System** - Define reusable tools for agents
- **Flow Orchestration** - Chain multiple agents and tools
- **Context Management** - Maintain state across execution
- **Event System** - AG-UI protocol support

### FastAPI Integration

- Production-ready setup
- Middleware support
- Exception handling
- Request/Response schemas

### Infrastructure

- **Database** - PostgreSQL with async support (asyncpg)
- **Elasticsearch** - Full-text search integration
- **Message Queue** - Async message processing (aio-pika)

### Security

- JWT authentication
- Casbin authorization
- Password hashing (bcrypt)

## 🤝 Contributing

We welcome contributions! Please see our [Development Rules](docs/DEVELOPMENT_RULES.md) for guidelines.

### Key Guidelines

- Use relative imports (not `from agstack...`)
- Inherit from `BaseSchema` for Pydantic models
- Use `ruff` and `pyright` for code quality
- Follow Python 3.12+ standards
- Write tests for new features

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 👥 Authors

- **XtraVisions** - [gitadmin@xtravisions.com](mailto:gitadmin@xtravisions.com)
- **Chen Hao** - [chenhao@xtravisions.com](mailto:chenhao@xtravisions.com)

## 🔗 Links

- **Documentation**: [docs/](docs/)
- **GitHub**: (TBD)
- **Issues**: (TBD)
- **PyPI**: (TBD)

## 📝 Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history.

---

**Note**: AgStack is under active development. APIs may change before the 1.0 stable release.
