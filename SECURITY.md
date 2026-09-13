# Security Policy

## Reporting a vulnerability

Please open a private GitHub security advisory or email the maintainer. Do not
file public issues that include exploit details, tokens, or account credentials.

## Secrets you must never commit

| Item | Where it should live |
|------|----------------------|
| LLM API keys | `.env` / `LUOPITA_API_KEY` |
| Admin console token | `.env` / `LUOPITA_ADMIN_TOKEN` |
| NapCat / OneBot token | `.env` + NapCat config (not public) |
| QQ login session | `data/napcat/QQ/` (gitignored) |
| Real owner QQ numbers | `config/identity.yaml` (gitignored; copy from `identity.example.yaml`) |
| WebUI / passkey files | `data/napcat/config/passkey.json`, `webui.json` (gitignored) |

## Hardening checklist before public deploy

1. Set a strong `LUOPITA_ADMIN_TOKEN` and do not expose the admin UI without it.
2. Rotate `LUOPITA_NAPCAT_TOKEN` / `NAPCAT_WEBUI_TOKEN` away from demo defaults (`luopita` / `napcat`).
3. Do not publish ports `6099` (NapCat WebUI) or `5432` / `6379` to the internet.
4. Keep `data/napcat/QQ/` and account-specific `onebot11_<qq>.json` off git and backups that are public.
5. Prefer reverse proxy + TLS in front of `:5170`; rate-limit webhooks if exposed.

## Token masking

`GET /api/config` returns masked secrets. Empty values on `PUT` do not overwrite existing keys.
