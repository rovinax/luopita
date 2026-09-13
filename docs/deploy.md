# 部署

本页是从零到可回消息的部署说明。功能概览见 [首页](index.md)。

## 0. 准备

- Docker + Docker Compose v2
- 一台能出网的机器（拉镜像 / 调 LLM）
- （可选）已准备好的 LLM API Key（DeepSeek / OpenAI 兼容）

```bash
git clone git@github.com:rovinax/luopita.git
cd luopita
cp .env.example .env
cp config/identity.example.yaml config/identity.yaml
# 可选：从示例改人格
# cp config/person.example.yaml config/person.yaml
```

编辑 `.env`：

1. 填入 `LUOPITA_API_KEY`
2. 设置 `LUOPITA_ADMIN_TOKEN`（公网必填）
3. 把 `LUOPITA_NAPCAT_TOKEN`、`NAPCAT_WEBUI_TOKEN` 改成自己的随机串
4. 同步改 `data/napcat/config/onebot11.json` 里两处 `token`，与 `LUOPITA_NAPCAT_TOKEN` 一致

编辑 `config/identity.yaml`：取消注释并填入你的 QQ 号作为 `owners`。

## 1. 一键启动（推荐）

```bash
export NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g)
# 生产：不要挂开发热重载
COMPOSE_FILE=docker-compose.yml docker compose up --build -d
```

本地改代码调试：

```bash
# .env 里默认 COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml
docker compose up --build -d
```

| 入口 | URL |
|------|-----|
| Luopita 控制台 | http://localhost:5170 |
| NapCat WebUI（扫码） | http://localhost:6099/webui |
| 健康检查 | http://localhost:5170/health |

首次登录 WebUI 看：

```bash
docker compose logs napcat | head
```

## 2. 接通 QQ（NapCat）

1. 浏览器打开 WebUI，用 `NAPCAT_WEBUI_TOKEN` 登录并扫码。
2. 确认网络适配器上报地址为 `http://app:5170/webhooks/napcat`（账号级文件可能是 `onebot11_<QQ>.json`）。
3. 在群里 `@机器人` 或唤醒词（默认含「小Lu」）试一句。
4. 若无回包：`docker compose logs -f app napcat`，核对 token、bot 是否在线、群是否要求 @。

## 3. 无 Docker 的纯本地开发

适合跑单测 / mock，见 [快速开始](getting-started.md)。

## 4. 配置落点

详见 [配置](configuration.md)。

## 5. 生产注意

- 不要把 `6099`、`5432`、`6379` 暴露公网。
- 必须设置 `LUOPITA_ADMIN_TOKEN`。
- WSL2：NapCat 放在 Linux 容器即可；若用宿主机 NapCat，把 webhook 指到 WSL IP，而不是容器名 `app`。
- 升级：`git pull` 后 `docker compose up --build -d`；QQ 登录态在 volume `data/napcat/QQ/`，一般可保留。
- 更多加固见 [安全](security.md)。

## 6. 验证清单

- [ ] `GET /health` 返回 ok
- [ ] 控制台能登录（若配置了 admin token）
- [ ] mock 或真实模型能在控制台对话
- [ ] NapCat 扫码成功，群 @ 有回复
- [ ] `.env` / `identity.yaml` / QQ 目录未进入 git

## 7. 预构建镜像（GitHub Actions）

`master` 推送后可从 GHCR 拉镜像（无需本地 `docker compose build` 编译前端）：

```bash
docker pull ghcr.io/rovinax/luopita:latest
```

Compose 仍可用仓库内 `Dockerfile` 本地构建；CI 镜像适合快速试用或自建部署。
