# Luopita

[![CI](https://github.com/rovinax/luopita/actions/workflows/ci.yml/badge.svg)](https://github.com/rovinax/luopita/actions/workflows/ci.yml)
[![Docker](https://github.com/rovinax/luopita/actions/workflows/docker.yml/badge.svg)](https://github.com/rovinax/luopita/actions/workflows/docker.yml)
[![Docs](https://github.com/rovinax/luopita/actions/workflows/docs.yml/badge.svg)](https://github.com/rovinax/luopita/actions/workflows/docs.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/agent-LangGraph-violet.svg)](https://github.com/langchain-ai/langgraph)
[![Docs Site](https://img.shields.io/badge/docs-MkDocs%20Material-teal.svg)](https://rovinax.github.io/luopita/)

**会像人一样插话的 QQ 群聊机器人内核** — NapCat / OneBot 接入，LangGraph Agent 驱动，群聊按说话人分片，热上下文进 Redis，冷记忆落 Postgres + pgvector。

> 不是又一个「整群共用一条会话」的 bot。Luopita 把触发、路由、组装、压缩和存储拆开，尽量避免 A 的话题被答给 B。

## 为什么用它

| 痛点 | Luopita 怎么处理 |
|------|------------------|
| 群里串话、抢别人话题 | 按说话人分片 + 旁听背景隔离 + 裸 `@` 走开场 |
| 上下文又贵又乱 | Redis 热分片 / Postgres 温存储 / 向量冷检索 |
| 多入口各写一套逻辑 | NapCat、TUI、管理台统一成 `InboundMessage` → 同一 Agent |
| 落地麻烦 | 一份 Compose：Postgres + Redis + NapCat + App |

## 架构一览

```text
NapCat (QQ) ──webhook──┐
TUI WebSocket ─────────┼── FastAPI ── ChatOrchestrator ── LangGraph
Admin SPA /api/chat ───┘         │
                                 ├── PostgresSaver（短期 thread / 分片）
                                 ├── Redis（群活跃分片）
                                 ├── group_events + pgvector（冷记忆）
                                 └── long_term_memories（主人长期记忆）
```

群聊五层管线：**触发 → 分片路由 → 上下文组装 → 压缩 → 热/温/冷存储**。

## 快速开始（30 秒摸到界面）

需要：[uv](https://github.com/astral-sh/uv)、Python 3.12+。

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

打开 http://127.0.0.1:5170 — mock 模式下无需 API Key 也能走通对话链路。

更完整的安装、Docker、扫码登录 QQ 见 **[文档站](https://rovinax.github.io/luopita/)**（[部署教程](docs/deploy.md)）。

## Docker 一键（接 QQ）

```bash
cp .env.example .env
# 编辑 .env：LUOPITA_API_KEY、LUOPITA_ADMIN_TOKEN，并改掉默认 token
cp config/identity.example.yaml config/identity.yaml
# 在 identity.yaml 里填入你的 QQ 作为 owners

export NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g)
COMPOSE_FILE=docker-compose.yml docker compose up --build -d
```

| 服务 | 地址 |
|------|------|
| 控制台 | http://localhost:5170 |
| NapCat WebUI | http://localhost:6099/webui |
| 健康检查 | `GET /health` |

本地热重载开发：保留 `.env` 里的 `COMPOSE_FILE=...:docker-compose.dev.yml` 后 `docker compose up --build -d`。

## 配置要点

| 变量 / 文件 | 说明 |
|-------------|------|
| `LUOPITA_API_KEY` | 模型密钥；空则 mock |
| `LUOPITA_ADMIN_TOKEN` | 管理 API / 控制台；公网必填 |
| `LUOPITA_DATABASE_URL` | `postgresql://...` 或 `memory://` |
| `LUOPITA_REDIS_URL` | `redis://...` 或 `memory://` |
| `config/identity.yaml` | 主人、唤醒词、群聊策略（gitignore） |
| `config/person.yaml` | 人格；可用 `person.example.yaml` 覆盖 |

密钥相关约定见 [SECURITY.md](SECURITY.md) / [文档·安全](https://rovinax.github.io/luopita/security/)。**不要把真实 QQ、`.env`、NapCat 登录态提交进仓库。**

## 开发与测试

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
uv run python tui/ui.py          # 需先起后端
cd web && npm install && npm run dev   # Vite :5173 → 代理 :5170

# 本地预览文档站
uv sync --group docs
uv run mkdocs serve
```

CI 会在每次 push / PR 跑单元测试、前端构建与文档构建；`master` 会部署 [GitHub Pages 文档站](https://rovinax.github.io/luopita/)，并推送镜像到 `ghcr.io/rovinax/luopita`。

```bash
docker pull ghcr.io/rovinax/luopita:latest
```

首次拉取私有包需 `docker login ghcr.io`；仓库设为 Public 且 Package 可见性为 public 后可直接拉取。

## 扩展

- 新平台：实现 `PlatformAdapter`（`enabled` + `send`），入站解析为 `InboundMessage`
- NapCat WebSocket、飞书、Telegram 可挂到同一 `AdapterRegistry`
- 命令走白名单 `run_shell`，危险 NapCat 动作（cookies / 退出登录等）默认拒绝
- 主人斜杠命令：`/help` `/ping` `/status` `/time` `/whoami` `/model` `/allow` `/clear`

## 许可证

[MIT](LICENSE) © rovina

---

关键词：QQ 机器人、NapCat、OneBot、LangGraph、群聊上下文、FastAPI chatbot、pgvector memory
