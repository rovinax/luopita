# Luopita

**会像人一样插话的 QQ 群聊机器人内核** — NapCat / OneBot 接入，LangGraph Agent 驱动，群聊按说话人分片。热上下文进 Redis，温数据落 Postgres，冷检索走 pgvector（本地哈希向量，不调外部 embedding API）。

> 不是又一个「整群共用一条会话」的 bot。Luopita 把触发、路由、组装、压缩和存储拆开，尽量避免 A 的话题被答给 B。

当前版本 **0.2.0**。源码：[github.com/rovinax/luopita](https://github.com/rovinax/luopita)

## 为什么用它

| 痛点 | Luopita 怎么处理 |
|------|------------------|
| 群里串话、抢别人话题 | 按说话人分片 + 旁听背景隔离 + 裸 `@` 走开场 |
| 上下文又贵又乱 | Redis 热分片 / Postgres 温存储 / pgvector 冷检索 |
| 多入口各写一套逻辑 | NapCat、TUI、管理台统一成 `InboundMessage` → 同一编排器 |
| 主人私事漏给群友 | `owner` / `user` 两套图；shell、QQ 工具、定时任务只给主人 |
| 落地麻烦 | 一份 Compose：Postgres + Redis + NapCat + App |

## 现在能做什么

- **群聊管线**：触发 → 分片路由 → 上下文组装 → 压缩 → 热/温/冷存储
- **主人命令**：`/help` `/ping` `/status` `/time` `/whoami` `/model` `/allow` `/clear` `/cron`
- **定时任务**：斜杠命令或自然语言「每天早上八点查天气」；到期在独立 thread 跑一轮再回原会话
- **开放承诺**：从对话抽出「明天提醒我交周报」，心跳到期后限频跟进
- **人格与样例**：`person.yaml` 是身份，`voice_examples.yaml` 按场景注入口吻，不是记忆
- **管理台**：总览、模型/身份/人格/样例、会话对话、说话人画像

## 从这里开始

- [快速开始](getting-started.md) — 本地 mock、控制台、TUI
- [部署](deploy.md) — Docker + NapCat 扫码上线
- [架构](architecture.md) — 入口、双图、五层管线、记忆与承诺
- [配置](configuration.md) — 环境变量与文件落点
- [安全](security.md) — 密钥与加固清单
- [贡献](contributing.md) — 测试与文档构建
