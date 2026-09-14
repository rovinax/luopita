# 配置

## 常用环境变量

| 变量 | 说明 |
|------|------|
| `LUOPITA_API_KEY` | 模型密钥；空则 mock |
| `LUOPITA_PROVIDER` / `LUOPITA_MODEL` / `LUOPITA_BASE_URL` | 供应商 |
| `LUOPITA_ADMIN_TOKEN` | 管理 API / 控制台；公网必填 |
| `LUOPITA_DATABASE_URL` | `postgresql://...` 或 `memory://` |
| `LUOPITA_REDIS_URL` | `redis://...` 或 `memory://` |
| `LUOPITA_NAPCAT_URL` / `LUOPITA_NAPCAT_TOKEN` / `LUOPITA_NAPCAT_ENABLED` | NapCat OneBot |
| `NAPCAT_UID` / `NAPCAT_GID` | NapCat 容器文件权限，建议 `$(id -u)` / `$(id -g)` |
| `NAPCAT_WEBUI_TOKEN` | NapCat WebUI 登录 token（改掉默认值） |
| `LUOPITA_TUI_WS` | TUI 客户端 WebSocket 地址 |

完整示例见仓库 [`.env.example`](https://github.com/rovinax/luopita/blob/master/.env.example)。

## 文件落点

| 文件 / 变量 | 作用 |
|-------------|------|
| `.env` | 密钥与 Compose 连接串（勿提交） |
| `config/sys.yaml` | 非密钥默认项 |
| `config/person.yaml` | 人格（可用控制台改；见 `person.example.yaml`） |
| `config/voice_examples.yaml` | 说话样例；控制台「样例」页编辑，按场景注入 system，不是记忆 |
| `config/identity.yaml` | 主人 QQ、唤醒词、群聊策略（gitignore；见 `identity.example.yaml`） |
| Postgres | 会话 / 群事件 / 向量冷记忆 / 当前说话人画像卡片（控制台「画像」页只读查看） |
| Redis | 群活跃分片热上下文 |

运行时配置可通过管理端写入数据库（密钥 GET 时掩码；留空 PUT 不会覆盖原密钥）。
