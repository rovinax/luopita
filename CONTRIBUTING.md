# Community

Issues and PRs are welcome.

## Before opening a PR

1. Do not commit `.env`, `config/identity.yaml`, NapCat QQ session, or real API keys.
2. Prefer small, focused diffs with a short “why” in the commit message.
3. Run: `uv run python -m unittest discover -s tests -p "test_*.py"`
4. If you change docs: `uv sync --group docs && uv run mkdocs build --strict`

## Local setup

See the [docs site](https://rovinax.github.io/luopita/), [docs/deploy.md](docs/deploy.md), and the Quick Start in [README.md](README.md).

```bash
uv sync --group docs
uv run mkdocs serve
```
