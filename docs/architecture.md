# 架构

```text
NapCat (QQ) ──webhook──┐
TUI WebSocket ─────────┼── FastAPI ── ChatOrchestrator ── LangGraph
Admin SPA /api/chat ───┘         │
                                 ├── PostgresSaver（短期 thread / 分片）
                                 ├── Redis（群活跃分片）
                                 ├── group_events + pgvector（冷记忆）
                                 └── long_term_memories（主人长期记忆）
```

## 群聊五层管线

1. **触发** — `@` / 唤醒词 / 插话策略，决定是否回复  
2. **分片路由** — 按说话人 + 话题分片；引用回复仅同人继承  
3. **上下文组装** — 群记忆（旁听）+ 用户画像 + 活跃分片 + 当前消息  
4. **压缩** — 分片过长时摘要，控制窗口  
5. **存储** — Redis 热 / Postgres 温 / pgvector 冷  

说话人隔离要点：裸 `@` / 短唤醒走开场，禁止主动续答旁人未完话题。

## 统一入口

所有平台规范化为 `InboundMessage`，再走同一个 Agent。新平台实现 `PlatformAdapter`（`enabled` + `send`）即可挂到 `AdapterRegistry`。
