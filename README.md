# Agent Workbench

A multi-agent orchestration workbench — spawn, manage, and chat with multiple AI agents in one web UI. Agents can use tools (shell, Hermes, file I/O) and collaborate in typed sessions (Chat, Research, Work).

## Quick Start

```bash
# Install dependencies
uv sync

# Run the server
./start_workbench.sh

# Open in browser
open http://127.0.0.1:5000
```

## Features

- **Multi-agent chat** — add multiple agents to a session, each with their own provider, model, role, and tool harness
- **Workspace and channel organization** — group typed sessions, teams, labels, and assets by workspace
- **Reusable agent teams** — bind configured agents to sessions and transfer participants between sessions
- **Typed sessions** — Chat (5 tool iterations), Research (10), Work (25) — configurable per session
- **Tool harnesses** — shell commands, Hermes agent sessions, file I/O, SSH, OpenCode
- **Live work visualization** — see what each agent is doing in real time, inspect tool calls and results, stop runaway agents
- **Provider presets** — OpenAI, Ollama Cloud, or any OpenAI-compatible endpoint with API key validation and model discovery
- **Session operations** — create, rename, configure, export, upload files, page long histories, and delete with a full data cascade
- **Operational safeguards** — CSRF protection, local identity, negotiated tool permissions, health/readiness endpoints, and bounded live payloads
- **Dark theme** — built for extended use

## Architecture

```
┌─────────────────────────────────────────────┐
│                  Web UI (Flask)              │
│  Workspaces · Channels · Sessions · Settings │
├─────────────────────────────────────────────┤
│              Services Layer                  │
│  AgentRuntime · ToolRegistry · Routing       │
│  SessionService · Teams · Assets · Identity  │
├─────────────────────────────────────────────┤
│              Adapter Layer                    │
│  Shell · Hermes · OpenCode · SSH · Discussion│
├─────────────────────────────────────────────┤
│              Data Layer (SQLite)              │
│  Sessions · Providers · Profiles · Tools     │
│  Messages · Invocations · Runs · Teams       │
└─────────────────────────────────────────────┘
```

## Configuration

### Providers
Configured via `/settings/providers`:
- **OpenAI** — preset: `https://api.openai.com/v1`
- **Ollama Cloud** — preset: `https://ollama.com/v1`
- **Local** — any OpenAI-compatible endpoint (llama.cpp, vLLM, etc.)
- API keys stored locally in `.workbench.secrets.env`, no server restart needed

### Agents
Configured via `/settings/agents`:
- Provider + Model binding
- Role (assistant, researcher, critic, implementer, reviewer)
- Perspective (e.g. "strict", "pragmatic")
- Harness type (hermes, shell, opencode, ssh, discussion)

### Sessions
- **Chat** — general conversation, 5 tool iterations
- **Research** — deep investigation, 10 tool iterations
- **Work** — structured execution, 25 tool iterations
- Limits configurable per session on the config page

## Development

```bash
# Run tests
.venv/bin/python -m pytest

# Run a single test file
.venv/bin/python -m pytest tests/test_settings_ui.py

# Start with debug mode
WORKBENCH_ENV=development ./start_workbench.sh
```

### Project structure
```
src/agent_workbench/
├── adapters/          # Harness adapters (shell, hermes, opencode, ssh)
├── db/                # Database connection + migrations
│   └── migrations/    # Versioned schema migrations (001-016)
├── models/            # SQLAlchemy-free dataclass models + repositories
├── services/          # Business logic (runtime, routing, sessions, tools)
└── web/               # Flask blueprints + Jinja2 templates
    └── templates/     # HTML templates (dark theme)
documentation/         # Specs, architecture docs, implementation plans
tests/                # pytest test suite (1,180+ tests)
```

## Documentation

See the `documentation/` directory for:
- Architecture and domain model specs
- Implementation plans and phase contracts
- Operator runbook and release checklist
- Feature specs (agent work visualization, provider presets, etc.)

## License

Internal project — Nous Research / Agent Workbench
