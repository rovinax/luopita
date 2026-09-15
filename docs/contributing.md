# 贡献

欢迎 Issue 与 PR。英文版清单见仓库根目录 [CONTRIBUTING.md](https://github.com/rovinax/luopita/blob/master/CONTRIBUTING.md)。

## 提交 PR 前

1. 不要提交 `.env`、`config/identity.yaml`、NapCat QQ 登录态或真实 API Key。
2. 尽量小而聚焦的 diff，commit message 写清「为什么」。
3. 跑测试：`uv run python -m unittest discover -s tests -p "test_*.py"`（或 `./scripts/run_tests.sh`）
4. 若改了前端：`cd web && npm run build`（含 `tsc --noEmit`）
5. 若改了文档：`uv sync --group docs && uv run mkdocs build --strict`

改群聊管线、身份或出站清洗时，优先补 `tests/test_group_*.py`、`tests/test_identity.py`、`tests/test_outbound_sanitize.py`。承诺 / 工作态相关看 `tests/test_collaborator.py`。

## 本地文档预览

```bash
uv sync --group docs
uv run mkdocs serve
```

浏览器打开 http://127.0.0.1:8000 。

## 更多

本地启动见 [快速开始](getting-started.md)，完整部署见 [部署](deploy.md)，模块边界见 [架构](architecture.md)。
