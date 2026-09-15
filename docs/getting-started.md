# 快速开始

需要：[uv](https://github.com/astral-sh/uv)、Python 3.12+。可选：Node 22（开发管理台）。

## 1. mock 模式摸到链路

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

打开 http://127.0.0.1:5170 。

- `GET /health` 应返回 `ok: true`，`provider` 为 `mock`，`database` / `redis` 为 `memory`。
- 未构建 `web/dist` 时，根路径只返回 JSON `{"ui": false}`。要看控制台：

```bash
cd web && npm install && npm run build
```

然后重启 `uv run python main.py`。

`admin` 与 `tui` 用户默认就是主人（`config/identity.example.yaml` 里的内置 owners）。真实 QQ 主人需要在 `identity.yaml` 里自己加一条 `platform: napcat`。

## 2. 管理台

控制台四个页面：

| 页 | 做什么 |
|----|--------|
| 总览 | 模型、数据库、Redis、平台开关、群聊策略快照 |
| 配置 | 模型 / Agent / 平台 / 身份 / 人格 / 样例 / 系统；改完即时生效 |
| 会话 | 历史会话 + 在控制台直接对话（走 `admin` 主人通道） |
| 画像 | 只读查看当前说话人卡片，可删除一条 |

密钥留空保存不会覆盖原值。若设置了 `LUOPITA_ADMIN_TOKEN`，先在页面填口令；请求头用 `Authorization: Bearer …`，也可用 `X-Admin-Token`。

开发前端（热更新）：

```bash
cd web && npm install && npm run dev
```

Vite 在 `:5173`，把 `/api`、`/health`、`/chat` 代理到后端 `:5170`。后端需已启动。

## 3. TUI

后端起来之后：

```bash
uv run python tui/ui.py
```

默认连 `ws://127.0.0.1:5170/ws/tui?chat_id=local&user_id=tui`。若开了管理口令，把 token 接到 URL：

```bash
export LUOPITA_TUI_WS="ws://127.0.0.1:5170/ws/tui?chat_id=local&user_id=tui&admin_token=你的口令"
uv run python tui/ui.py
```

## 4. 跑测试

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
# 或
./scripts/run_tests.sh
```

CI 同样用 `LUOPITA_PROVIDER=mock` 和 `memory://` 数据库 / Redis。

## 5. 主人命令（系统直执行）

只有主人、且以 `/` 开头才算命令，不经模型：

| 命令 | 作用 |
|------|------|
| `/help` | 命令表 |
| `/ping` | 在不在 |
| `/status` | 模型、数据库、Redis、NapCat |
| `/time` | 北京时间 |
| `/whoami` | 当前身份 |
| `/model` | 当前文本 / 视觉模型 |
| `/allow` | `run_shell` 白名单 |
| `/clear` | 忘掉这轮对话（并取消该主人未完成承诺） |
| `/cron` | 定时任务：`add` / `get` / `on` / `off` / `run` / `rm` |

别名例如 `/h`、`/?`、`/reset`、`/job`。自然语言「十分钟后提醒我」会走 owner 的 `cron` 工具；「明天提醒我交周报」这类承诺由心跳跟进，不必先设 cron。

## 下一步

要接真实 QQ / 生产部署，见 [部署教程](deploy.md)。配置项见 [配置](configuration.md)。
