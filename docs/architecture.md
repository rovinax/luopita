# 架构

当前实现（0.2.0）把「怎么接消息」和「怎么当群友说话」拆开：平台只负责规范化入站，编排器按身份选图，群聊再走五层管线。

```text
NapCat (QQ) ──webhook──┐
TUI WebSocket ─────────┼── FastAPI ── ChatOrchestrator
Admin SPA /api/chat ───┘         │
                                 ├── owner_graph / user_graph / cron_graph
                                 ├── PostgresSaver（短期 thread / 分片）
                                 ├── Redis HotStore（活跃分片、实体栈）
                                 └── Postgres
                                       group_events + pgvector
                                       user_profiles / long_term_memories
                                       cron_jobs / commitments / owner_scratchpads
```

## 仓库怎么拆

| 目录 | 职责 |
|------|------|
| `app/` | FastAPI 路由、管理鉴权、`Runtime` 生命周期 |
| `agent/` | LangGraph 工具：`run_shell`、`qq_*`、`cron` |
| `core/` | 编排、群聊管线、身份、记忆、定时、承诺、出站清洗 |
| `interface/` | LLM 工厂与平台适配器（NapCat / TUI / Admin） |
| `web/` | 管理台 SPA |
| `tui/` | Textual 客户端 |
| `config/` | `sys.yaml`、`person.yaml`、`identity.yaml`、说话样例 |
| `msg/` | `InboundMessage` 等 schema |
| `tests/` | unittest |

入口：`main.py` → `uvicorn app.main:app`。进程启动时 `Runtime.start()` 连库、编图、挂上 cron 心跳。

## 统一入口

所有平台规范化为 `InboundMessage`，再交给 `ChatOrchestrator.handle`。

当前适配器：

- `napcat` — OneBot HTTP webhook `/webhooks/napcat`，出站走 NapCat API
- `tui` — `/ws/tui`
- `admin` — `/api/chat`（控制台，不投递到 QQ）

新平台实现 `PlatformAdapter`（`enabled` + `send`）即可挂到 `AdapterRegistry`。发送前会做出站清洗（见下文）。

身份由 `config/identity.yaml` 决定：匹配 `platform` + `user_id` 的是 **owner**，其余是 **user**。控制台 `admin` 与 TUI `tui` 是内置主人。

## 三套 LangGraph

`Runtime` 编三张图，checkpoint 共用（Postgres 上是 `PostgresSaver`，`memory://` 时是内存 saver）：

| 图 | 谁用 | 工具 |
|----|------|------|
| `owner_graph` | 主人日常对话 | `get_current_date`、`run_shell`、`qq_*`、`cron` |
| `user_graph` | 群友 / 普通私聊 | 只有 `get_current_date` |
| `cron_graph` | 定时任务到期回合 | 与主人相同，但**不再挂 cron 工具**，避免任务里套任务 |

窗口策略：短期消息条数由 `agent.short_term_messages` 控制；群聊会丢掉末尾多出来的 Human，避免把旁人的话卷进当前 thread。超出窗口的主人消息可归档进长期记忆。

系统提示由 `compose_system_prompt` 按角色、群/私聊、是否插话、是否裸唤醒、是否定时任务现场拼出。说话样例和工作态（scratchpad）只给主人、且不进 cron 回合。

## 群聊五层管线

群消息即使最终不回，也会写入群共享记忆。是否开口由第一层决定。

1. **触发** — `@` / 唤醒词 / 回复机器人 / 主人斜杠命令 → `direct`；语义接近当前分片或技术闲聊概率命中 → `chime`；灌水、冷却中、接不上 → `ignore`。
2. **分片路由** — 按「这个群里的这个人 + 当前话题」分片。引用回复只在**同一说话人**时继承分片；话题漂移则新开一片。
3. **上下文组装** — 群记忆（旁听）+ 当前说话人卡片 + 分片摘要 + 活跃回合 + 实体/代词提示 + 当前消息。说话样例进 system，不是会话历史。
4. **压缩** — 分片过长时摘要，控制窗口。
5. **存储** — Redis 热（约 30 分钟会话 / 7 天群记忆） / Postgres 温（分片、回合、画像） / pgvector 冷（群事件与主人长期记忆）。

说话人隔离要点：裸 `@` / 短唤醒走开场，禁止主动续答旁人未完话题。群友画像只描述当前说话人怎么回，不从旁听学习，也不进主人长期记忆。

插话策略在 `identity.yaml`：`group_require_at`、`wake_keywords`、`group_tech_chance`、`group_chatty_chance`、冷却与接话条数。

人格在 `config/person.yaml`，口吻样例在 `config/voice_examples.yaml`（按 `scene` / `mode` / `relation` 挑选注入）。

## 记忆分层

| 层 | 存什么 | 谁能用 |
|----|--------|--------|
| LangGraph checkpoint | 当前 thread 短对话 | 该分片 / 私聊会话 |
| Redis HotStore | 活跃分片回合、群近期事件、实体栈 | 群聊组装 |
| `group_events` | 群全量事件 + 向量 | 冷检索旁听背景 |
| `user_profiles` | 当前说话人卡片（按群） | 只描述怎么回这个人 |
| `long_term_memories` | 主人偏好、项目、提醒类事实 | 仅 owner 召回 |
| `owner_scratchpads` | 焦点、开放承诺摘要、近期注意 | 仅 owner 注入 |

向量列是 **1536 维本地哈希 embedding**（`hash_embed`），存在 pgvector 里做 `<=>` 检索，不调用外部 embedding 接口。`memory://` 模式下用同样的余弦打分走内存实现。

主人纠正口吻（「别客服腔」「就这样」）会写成策略事实，并写进 scratchpad 的 `reflect_notes`。

## 定时任务 vs 开放承诺

两套机制不要混：

| | 定时任务 `cron_jobs` | 开放承诺 `commitments` |
|--|----------------------|------------------------|
| 怎么设 | `/cron add …` 或主人调用 `cron` 工具 | 从自然语言抽出（「明天提醒我交周报」） |
| 到期后 | 独立 thread 跑 `cron_graph`，把回复发回原会话 | 心跳（默认 90s）触发一轮跟进，说完记为 done |
| 限制 | 主人专属；任务回合不能再设新 cron | 每主人每小时最多 3 次主动、约 50 分钟冷却 |

`/clear` 会清当前会话，并取消该主人仍开放的承诺。

## 出站 {#outbound}

`AdapterRegistry.send` 先 `sanitize_outbound_text`：去掉 Markdown，拦截泄漏的 tool-call / DSML 标记。空结果直接丢弃，不当聊天发出去。

通过后按空行 / `---` 拆成气泡。提示词要求群聊尽量两条、私聊三条；切分上限在插话时 3 条、其它 4 条。NapCat 出站会做短延迟，避免一条长文砸出去。

模型若只输出 `[SILENCE]`，视为本轮不回。

## HTTP 表面

| 路径 | 作用 |
|------|------|
| `GET /health` | 进程、模型、数据库、Redis |
| `/api/config` `GET`/`PUT` | 运行时配置（密钥掩码） |
| `/api/persona` `/api/identity` `/api/examples` | 人格、身份、说话样例 |
| `/api/profiles` | 说话人画像列表 / 删除 |
| `/api/sessions` | 会话与消息 |
| `POST /api/chat` | 控制台对话 |
| `POST /webhooks/napcat` | OneBot 入站（校验 token） |
| `/ws/tui` | TUI |
| `POST /api/napcat/{action}` | 管理端调白名单动作；危险动作 403 |

管理 API 在配置了 `LUOPITA_ADMIN_TOKEN` 后一律要带 token。
