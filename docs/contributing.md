# 贡献

欢迎 Issue 与 PR。

## 提交 PR 前

1. 不要提交 `.env`、`config/identity.yaml`、NapCat QQ 登录态或真实 API Key。
2. 尽量小而聚焦的 diff，commit message 写清「为什么」。
3. 跑测试：`uv run python -m unittest discover -s tests -p "test_*.py"`
4. 若改了文档：`uv sync --group docs && uv run mkdocs build --strict`

## 本地文档预览

```bash
uv sync --group docs
uv run mkdocs serve
```

浏览器打开 http://127.0.0.1:8000 。

## 更多

本地启动见 [快速开始](getting-started.md)，完整部署见 [部署](deploy.md)。
