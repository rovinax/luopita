# 架构

```text
NapCat (QQ) ──webhook──┐
TUI WebSocket ─────────┼── FastAPI ── ChatOrchestrator ── LangGraph
Admin SPA /api/chat ───┘         │
                                 ├── PostgresSaver（短期 thread / 分片）
                                 ├── Redis（群活跃分片）
                                 ├── group_events + pgvector（冷记忆）
                                 ├── long_term_memories（主人长期记忆）
                                 ├── user_profiles（当前说话人卡片，按群）
                                 └── cron_jobs（主人定时任务，到期独立回合）
```

## 群聊五层管线

1. **触发** — `@` / 唤醒词 / 插话策略，决定是否回复  
2. **分片路由** — 按说话人 + 话题分片；引用回复仅同人继承  
3. **上下文组装** — 群记忆（旁听）+ 当前说话人卡片 + 活跃分片 + 当前消息；说话样例进 system，不是会话历史  
4. **压缩** — 分片过长时摘要，控制窗口  
5. **存储** — Redis 热 / Postgres 温 / pgvector 冷  

说话人隔离要点：裸 `@` / 短唤醒走开场，禁止主动续答旁人未完话题。群友画像只描述当前说话人怎么回，不从旁听学习，也不进主人长期记忆。

人格在 `config/person.yaml`，口吻样例在 `config/voice_examples.yaml`（按 scene/mode/relation 挑选注入）。

## 统一入口

所有平台规范化为 `InboundMessage`，再走同一个 Agent。新平台实现 `PlatformAdapter`（`enabled` + `send`）即可挂到 `AdapterRegistry`。

## 主人定时任务

只有主人能用。`/cron` 是系统命令，不经模型；自然语言里说「十分钟后提醒我」会走 owner 的 `cron` 工具。到期后在独立 thread 跑一轮 owner agent，再把回复发回当初设任务的会话。
