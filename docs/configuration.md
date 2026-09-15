# 配置

优先级大致是：**环境变量 > 磁盘 yaml > 库里缓存的运行时设置**。人格与身份以磁盘为准，不会把 Postgres 当唯一真相；控制台改完会写回对应 yaml。

完整示例见仓库 [`.env.example`](https://github.com/rovinax/luopita/blob/master/.env.example)。

## 服务与存储

| 变量 | 说明 |
|------|------|
| `LUOPITA_HOST` / `LUOPITA_PORT` | 监听地址，默认 `0.0.0.0:5170` |
| `LUOPITA_DATABASE_URL` | `postgresql://...` 或 `memory://` |
| `LUOPITA_REDIS_URL` | `redis://...` 或 `memory://`（库也是 memory 时会强制内存 Redis） |
| `LUOPITA_ADMIN_TOKEN` | 管理 API / 控制台；空则本机开发不鉴权 |
| `LUOPITA_LOG_LEVEL` / `LUOPITA_LOG_DIR` / `LUOPITA_LOG_FILE` | 日志 |
| `LUOPITA_SHORT_TERM_MESSAGES` | 短期窗口条数，默认 16 |

## 模型

| 变量 | 说明 |
|------|------|
| `LUOPITA_PROVIDER` | `deepseek` / `openai` / `mock`（以及 `fake`、`offline`） |
| `LUOPITA_MODEL` | 文本模型，默认 `deepseek-chat` |
| `LUOPITA_VISION_MODEL` | 视觉模型，默认 `deepseek-flash` |
| `LUOPITA_BASE_URL` | OpenAI 兼容网关 |
| `LUOPITA_API_KEY` | 密钥；`mock` 可不填 |

`sys.yaml` 里也有同名默认项；环境变量会覆盖。

## NapCat / TUI

| 变量 | 说明 |
|------|------|
| `LUOPITA_NAPCAT_ENABLED` | 是否启用适配器 |
| `LUOPITA_NAPCAT_URL` 或 `LUOPITA_BOT_URL` | OneBot HTTP，Compose 内是 `http://napcat:3000` |
| `LUOPITA_NAPCAT_TOKEN` | 与 NapCat `access_token` 一致 |
| `LUOPITA_BOT_ID` | 机器人 QQ；启动时会尝试 `get_login_info` 回填 |
| `LUOPITA_TUI_ENABLED` | TUI WebSocket，默认开 |
| `LUOPITA_TUI_WS` | TUI 客户端连接地址 |

Docker / NapCat 容器：

| 变量 | 说明 |
|------|------|
| `NAPCAT_UID` / `NAPCAT_GID` | 容器文件权限，建议 `$(id -u)` / `$(id -g)` |
| `NAPCAT_WEBUI_TOKEN` | NapCat WebUI 登录 token（改掉默认值） |
| `NAPCAT_HTTP_PORT` / `NAPCAT_WS_PORT` / `NAPCAT_WEBUI_PORT` | 宿主机端口 |

## 文件落点

| 文件 / 变量 | 作用 |
|-------------|------|
| `.env` | 密钥与 Compose 连接串（勿提交） |
| `config/sys.yaml` | 非密钥默认项（host、平台开关、shell 白名单） |
| `config/person.yaml` | 人格；`LUOPITA_PERSON_FILE` 可改路径。模板：`person.example.yaml` |
| `config/voice_examples.yaml` | 说话样例；`LUOPITA_EXAMPLES_FILE` 可改路径。控制台「样例」页编辑 |
| `config/identity.yaml` | 主人 QQ、唤醒词、群聊策略（gitignore；`LUOPITA_IDENTITY_FILE` 可改路径） |
| Postgres | 会话、群事件、向量、画像、cron、承诺、scratchpad；控制台「画像」页只读 |
| Redis | 群活跃分片热上下文 |

运行时配置可通过管理端写入数据库（密钥 GET 时掩码；留空 PUT 不会覆盖原密钥）。`identity` / `persona` 同时写磁盘，编排器会按文件 mtime 热加载。

## 身份与群聊策略

`config/identity.example.yaml`：

```yaml
owners:
- platform: admin
  user_id: admin
- platform: tui
  user_id: tui
# - platform: napcat
#   user_id: "你的QQ号"
group_require_at: true
wake_keywords: [小lu, 小Lu, luopita, 小陆, 小路]
group_tech_chance: 0.22
group_chatty_chance: 0.1
group_chime_cooldown_sec: 45
group_engage_sec: 90
group_engage_replies: 2
```

| 字段 | 含义 |
|------|------|
| `owners` | 主人列表。`admin` / `tui` 即使漏写也会被补上 |
| `group_require_at` | 群里是否默认要 `@` / 唤醒才回 |
| `wake_keywords` | 点名用的名字 |
| `group_tech_chance` | 技术闲聊时插话概率 |
| `group_chatty_chance` | 普通闲聊插话概率 |
| `group_chime_cooldown_sec` | 插话冷却 |
| `group_engage_sec` / `group_engage_replies` | 同一说话人接话窗口与条数 |

## Agent 白名单

`config/sys.yaml` 的 `AGENT.COMMAND_ALLOWLIST`（可用控制台 Agent 页改）限制 `run_shell` 能跑的程序。默认含 `ls`、`pwd`、`python`、`uv` 等；**不是 bash**，不能管道、重定向、`&&`。

## 说话样例维度

每条样例：

- `scene`：`tech` / `chat` / `image` / `silence`
- `mode`：`direct` / `chime` / `bare_wake`
- `relation`：`owner` / `peer`
- `good` / `bad`：愿意说的 vs 客服腔

按当前回合推断 scene/mode/relation，最多注入 4 条、约 800 字。
