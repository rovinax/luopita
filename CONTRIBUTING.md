# Community

Issues and PRs are welcome. Chinese guide: [docs/contributing.md](docs/contributing.md).

## Before opening a PR

1. Do not commit `.env`, `config/identity.yaml`, NapCat QQ session, or real API keys.
2. Prefer small, focused diffs with a short “why” in the commit message.
3. Run: `uv run python -m unittest discover -s tests -p "test_*.py"` (or `./scripts/run_tests.sh`).
4. If you change the admin SPA: `cd web && npm run build` (includes `tsc --noEmit`).
5. If you change docs: `uv sync --group docs && uv run mkdocs build --strict`.

Group-chat, identity, and outbound-sanitize changes should add coverage in `tests/test_group_*.py`, `tests/test_identity.py`, or `tests/test_outbound_sanitize.py`. Commitments / scratchpad live in `tests/test_collaborator.py`.

## Local setup

See the [docs site](https://rovinax.github.io/luopita/), [docs/deploy.md](docs/deploy.md), the English Quick Start in [README.md](README.md), and [README_CN.md](README_CN.md).

```bash
uv sync --group docs
uv run mkdocs serve
```
