# Security Policy

## Reporting a vulnerability

Please open a private GitHub security advisory or email the maintainer. Do not
file public issues that include exploit details, tokens, or account credentials.

Chinese version: [docs/security.md](docs/security.md).

## Secrets you must never commit

| Item | Where it should live |
|------|----------------------|
| LLM API keys | `.env` / `LUOPITA_API_KEY` |
| Admin console token | `.env` / `LUOPITA_ADMIN_TOKEN` |
| NapCat / OneBot token | `.env` + NapCat config (not public) |
| QQ login session | `data/napcat/QQ/` (gitignored) |
| Real owner QQ numbers | `config/identity.yaml` (gitignored; copy from `identity.example.yaml`) |
| WebUI / passkey files | `data/napcat/config/passkey.json`, `webui.json` (gitignored) |

Do not put private personal data into tracked `config/person.yaml`. Point
`LUOPITA_PERSON_FILE` at an untracked file instead.

## Hardening checklist before public deploy

1. Set a strong `LUOPITA_ADMIN_TOKEN` and do not expose the admin UI without it.
2. Rotate `LUOPITA_NAPCAT_TOKEN` / `NAPCAT_WEBUI_TOKEN` away from demo defaults (`luopita` / `napcat`).
3. Do not publish ports `6099` (NapCat WebUI) or `5432` / `6379` to the internet.
4. Keep `data/napcat/QQ/` and account-specific `onebot11_<qq>.json` off git and backups that are public.
5. Prefer reverse proxy + TLS in front of `:5170`; rate-limit webhooks if exposed.
6. Tighten `COMMAND_ALLOWLIST`. Owner `run_shell` is not bash; peers do not get that tool.
7. Avoid pasting admin tokens in TUI WebSocket query strings that get screenshotted.

## Token masking

`GET /api/config` returns masked secrets. Empty values or `****` masks on `PUT`
do not overwrite existing keys. Admin routes accept `Authorization: Bearer`,
`X-Admin-Token`, or (localhost only) `admin_token` query.

Dangerous NapCat actions (cookies, credentials, logout) are blocked on both the
admin API and `qq_*` tools. Outbound sanitization strips leaked tool-call markup;
that is a display guard, not an authorization boundary.
