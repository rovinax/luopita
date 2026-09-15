# Luopita

**English** | [简体中文](README_CN.md)

[![CI](https://github.com/rovinax/luopita/actions/workflows/ci.yml/badge.svg)](https://github.com/rovinax/luopita/actions/workflows/ci.yml)
[![Docker](https://github.com/rovinax/luopita/actions/workflows/docker.yml/badge.svg)](https://github.com/rovinax/luopita/actions/workflows/docker.yml)
[![Docs](https://github.com/rovinax/luopita/actions/workflows/docs.yml/badge.svg)](https://github.com/rovinax/luopita/actions/workflows/docs.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/agent-LangGraph-violet.svg)](https://github.com/langchain-ai/langgraph)
[![Docs Site](https://img.shields.io/badge/docs-MkDocs%20Material-teal.svg)](https://rovinax.github.io/luopita/)

**QQ group chatbot kernel** for [NapCat](https://github.com/NapNeko/NapCatQQ) / [OneBot 11](https://github.com/botuniverse/onebot-11): a [LangGraph](https://github.com/langchain-ai/langgraph) agent on [FastAPI](https://fastapi.tiangolo.com/), with **per-speaker context shards** so the bot does not answer B with A’s unfinished thread.

Hot context lives in **Redis**. Warm state lives in **Postgres**. Cold recall uses **pgvector** with a local hash embedding (no external embedding API).

> Not another “one shared session for the whole group” bot. Luopita splits trigger, routing, assembly, compression, and storage.

Version **0.2.0**. Full guide (Chinese): **[docs site](https://rovinax.github.io/luopita/)**. 中文 README：[README_CN.md](README_CN.md).

## Why Luopita

| Pain | What Luopita does |
|------|-------------------|
| Cross-talk in QQ groups | Per-speaker shards, eavesdrop-only background, bare `@` as a greeting |
| Context is expensive and messy | Redis hot shards / Postgres warm store / pgvector cold search |
| Each inbox reimplements the agent | NapCat, TUI, and admin console all become `InboundMessage` |
| Owner secrets leak into the group | Separate `owner` / `user` graphs; shell, QQ tools, and cron are owner-only |
| Painful deploy | One Compose file: Postgres + Redis + NapCat + app |

## Features

- **Group-chat pipeline:** trigger → shard routing → context assembly → compression → hot/warm/cold storage
- **Owner slash commands** and natural-language cron (`/cron` is handled by the system, not the model)
- **Commitments:** extracts “remind me to file the weekly report tomorrow” and follows up on a rate-limited heartbeat
- **Voice examples** inject tone into the system prompt (they are not chat memory); **speaker profiles** describe only the current person
- **Outbound sanitizing:** strip Markdown and leaked tool-call markup, then split into QQ message bubbles
- **Admin SPA:** overview, config, console chat, read-only profiles

## Architecture

```text
NapCat (QQ) ──webhook──┐
TUI WebSocket ─────────┼── FastAPI ── ChatOrchestrator
Admin SPA /api/chat ───┘         │
                                 ├── owner_graph / user_graph / cron_graph
                                 ├── PostgresSaver (short thread / shard)
                                 ├── Redis HotStore (active shards, entity stack)
                                 └── Postgres
                                       group_events + pgvector
                                       user_profiles / long_term_memories
                                       cron_jobs / commitments / owner_scratchpads
```

```text
app/          FastAPI, auth, Runtime
agent/        LangGraph tools (run_shell, qq_*, cron)
core/         orchestration, group pipeline, identity, memory, cron, commitments
interface/    LLM factory and platform adapters
web/          admin SPA (Vite + React)
tui/          Textual terminal client
config/       persona / identity / sys yaml
```

## Quick start

Needs [uv](https://github.com/astral-sh/uv) and Python 3.12+.

```bash
git clone git@github.com:rovinax/luopita.git
cd luopita
cp .env.example .env
cp config/identity.example.yaml config/identity.yaml

export LUOPITA_DATABASE_URL=memory://
export LUOPITA_REDIS_URL=memory://
export LUOPITA_PROVIDER=mock
export LUOPITA_NAPCAT_ENABLED=false

uv sync
uv run python main.py
```

Open http://127.0.0.1:5170 — mock mode needs no API key. Without a frontend build the root path returns JSON only; run `cd web && npm install && npm run build` for the admin console.

Install, Docker, and QQ login: **[docs](https://rovinax.github.io/luopita/)** ([deploy](docs/deploy.md)).

## Docker (QQ via NapCat)

```bash
cp .env.example .env
# Set LUOPITA_API_KEY, LUOPITA_ADMIN_TOKEN, and rotate demo tokens
cp config/identity.example.yaml config/identity.yaml
# Add your QQ number under owners

export NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g)
COMPOSE_FILE=docker-compose.yml docker compose up --build -d
```

| Service | URL |
|---------|-----|
| Admin console | http://localhost:5170 |
| NapCat WebUI | http://localhost:6099/webui |
| Health | `GET /health` |

Local hot reload: keep `COMPOSE_FILE=...:docker-compose.dev.yml` in `.env`, then `docker compose up --build -d`.

## Configuration

| Variable / file | Role |
|-----------------|------|
| `LUOPITA_API_KEY` | LLM key; with `PROVIDER=mock` you can leave it empty |
| `LUOPITA_ADMIN_TOKEN` | Admin API / console; required on a public host |
| `LUOPITA_DATABASE_URL` | `postgresql://...` or `memory://` |
| `LUOPITA_REDIS_URL` | `redis://...` or `memory://` |
| `config/identity.yaml` | Owners, wake words, group chime policy (gitignored) |
| `config/person.yaml` | Persona; start from `person.example.yaml` |
| `config/voice_examples.yaml` | Tone examples (injected by scene, not stored as memory) |

Secrets: [SECURITY.md](SECURITY.md) / [docs · security](https://rovinax.github.io/luopita/security/). **Do not commit real QQ numbers, `.env`, or NapCat login state.**

## Development

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
# or ./scripts/run_tests.sh
uv run python tui/ui.py          # start the backend first
cd web && npm install && npm run dev   # Vite :5173 → proxy :5170

uv sync --group docs
uv run mkdocs serve
```

CI runs tests, the frontend build, and MkDocs on every push / PR. `master` publishes the [docs site](https://rovinax.github.io/luopita/) and `ghcr.io/rovinax/luopita`.

```bash
docker pull ghcr.io/rovinax/luopita:latest
```

Private GHCR packages need `docker login ghcr.io`. After the repo and package are public, pull works without login.

## Extending

- New IM: implement `PlatformAdapter` (`enabled` + `send`) and parse inbound as `InboundMessage`
- NapCat WebSocket, Feishu, or Telegram can hang off the same `AdapterRegistry` (built-in: `napcat` / `tui` / `admin`)
- `run_shell` is an allowlist, not bash; dangerous NapCat actions (cookies / logout) are denied
- Owner slash commands: `/help` `/ping` `/status` `/time` `/whoami` `/model` `/allow` `/clear` `/cron`
- System prompts are composed by `compose_system_prompt` in `core/identity.py`; `prompt/agent.md` is not the old tag protocol

## License

[MIT](LICENSE) © rovina

## Topics

QQ bot · NapCat · OneBot 11 · LangGraph agent · FastAPI chatbot · QQ group chat · per-speaker context · Redis · Postgres · pgvector · Python 3.12
