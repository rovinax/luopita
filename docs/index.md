# Luopita

**会像人一样插话的 QQ 群聊机器人内核** — NapCat / OneBot 接入，LangGraph Agent 驱动，群聊按说话人分片，热上下文进 Redis，冷记忆落 Postgres + pgvector。

> 不是又一个「整群共用一条会话」的 bot。Luopita 把触发、路由、组装、压缩和存储拆开，尽量避免 A 的话题被答给 B。

## 为什么用它

| 痛点 | Luopita 怎么处理 |
|------|------------------|
| 群里串话、抢别人话题 | 按说话人分片 + 旁听背景隔离 + 裸 `@` 走开场 |
| 上下文又贵又乱 | Redis 热分片 / Postgres 温存储 / 向量冷检索 |
| 多入口各写一套逻辑 | NapCat、TUI、管理台统一成 `InboundMessage` → 同一 Agent |
| 落地麻烦 | 一份 Compose：Postgres + Redis + NapCat + App |

## 从这里开始

- [快速开始](getting-started.md) — 本地 mock 30 秒摸到界面
- [部署](deploy.md) — Docker + NapCat 扫码上线
- [架构](architecture.md) — 群聊五层管线
- [配置](configuration.md) — 环境变量与文件落点
- [安全](security.md) — 密钥与加固清单

源码：[github.com/rovinax/luopita](https://github.com/rovinax/luopita)
