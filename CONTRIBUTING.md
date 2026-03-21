# Contributing to AgenticOrg

We welcome contributions from the community. This guide covers everything you need to get started.

## Development Setup

### Prerequisites

- Python 3.12+
- Node.js 20+ (for UI)
- Docker & Docker Compose
- Git

### Getting Started

```bash
# Clone the repo
git clone https://github.com/your-org/agenticorg.git
cd agenticorg

# Set up Python environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"

# Start infrastructure
docker compose up -d postgres redis minio

# Copy and configure environment
cp .env.example .env
# Edit .env with your keys

# Run tests to verify setup
pytest tests/unit/
```

### UI Development

```bash
cd ui
npm install
npm run dev    # http://localhost:5173
```

## Contribution Workflow

1. **Fork** the repository
2. **Create a feature branch**: `git checkout -b feature/my-feature`
3. **Make changes** following the code standards below
4. **Write tests** for all new functionality
5. **Run the full test suite**: `pytest tests/`
6. **Submit a Pull Request** against `main`

## Code Standards

### Python (Backend)

- **Linter**: `ruff check .` (zero violations required)
- **Type checking**: `mypy --ignore-missing-imports .`
- **Formatting**: ruff format (line length 100)
- **Async**: All I/O operations must be async
- **Error codes**: Use the E-series taxonomy from `core/schemas/errors.py` — no ad-hoc error strings

### TypeScript (Frontend)

- **Linter**: `eslint .`
- **Type checking**: `tsc --noEmit`
- **Components**: Functional components with TypeScript interfaces
- **Styling**: Tailwind CSS + Shadcn/ui components

### Tests

- Minimum **80% code coverage** enforced in CI
- All PRD test IDs (FT-FIN-xxx, SEC-AUTH-xxx, etc.) must pass
- Use `pytest-asyncio` for async tests
- Mock external services, not internal modules

## Agent Development

### Creating a New Agent

1. Create the prompt file: `core/agents/prompts/{agent_type}.prompt.txt`
   - Must include: Token scope, `<processing_sequence>`, `<escalation_rules>`, `<anti_hallucination>`, `<output_format>`
2. Create the agent class: `core/agents/{domain}/{agent_type}.py`
   - Set `agent_type`, `domain`, `confidence_floor`, `prompt_file`
3. Register in `core/agents/registry.py`
4. Add authorized tool scopes in agent config
5. Write tests covering all processing steps

### Agent Lifecycle Rules

- All new agents **must start in shadow mode** — no exceptions for production
- Shadow mode requires minimum 100 samples and 95% accuracy before promotion
- Clone agents inherit parent scopes — cannot elevate permissions
- Kill switch must work in <30 seconds

## Connector Development

### Adding a New Connector

1. Create `connectors/{category}/{connector_name}.py`
2. Extend `BaseConnector`
3. Implement `_register_tools()` and `_authenticate()`
4. Use `self._get_secret(key)` for credentials — **never hardcode tokens**
5. Set `rate_limit_rpm` appropriate to the API's limits
6. Register in `connectors/registry.py`

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Include the relevant PRD test IDs in the PR description
- Update CHANGELOG.md for user-facing changes
- Security-sensitive changes require review from a security team member
- All CI checks must pass before merge

## Reporting Issues

- Use GitHub Issues for bugs and feature requests
- For security vulnerabilities, see [SECURITY.md](SECURITY.md)

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
