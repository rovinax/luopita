# 快速开始

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

## 开发前端

```bash
cd web && npm install && npm run dev
```

Vite 在 `:5173`，代理到后端 `:5170`。

## 跑测试

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
```

## 下一步

要接真实 QQ / 生产部署，见 [部署教程](deploy.md)。

主人可用 `/cron` 管理定时任务（`/cron add 20m 提醒喝水`），或直接说「每天早上八点查天气」。到期后会自己跑一轮再回原会话。只有主人能设。
