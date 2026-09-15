# 安全

仓库根目录也保留 [SECURITY.md](https://github.com/rovinax/luopita/blob/master/SECURITY.md) 供 GitHub 安全策略识别。

## 报告漏洞

请通过 GitHub Security Advisory 私密报告，或联系维护者。不要在公开 Issue 里贴 exploit、token 或账号凭证。

## 绝不要提交的内容

| 项目 | 应放在哪里 |
|------|------------|
| LLM API keys | `.env` / `LUOPITA_API_KEY` |
| Admin console token | `.env` / `LUOPITA_ADMIN_TOKEN` |
| NapCat / OneBot token | `.env` + NapCat 配置（勿公开） |
| QQ 登录态 | `data/napcat/QQ/`（已 gitignore） |
| 真实主人 QQ | `config/identity.yaml`（已 gitignore） |
| WebUI / passkey | `data/napcat/config/passkey.json`、`webui.json`（已 gitignore） |

`config/person.yaml` 会进 git；不要把真人隐私写进人格文件。本地覆盖可用 `LUOPITA_PERSON_FILE` 指到未跟踪路径。

## 公网部署前清单

1. 设置强 `LUOPITA_ADMIN_TOKEN`，没有 token 不要暴露管理台。
2. 把 `LUOPITA_NAPCAT_TOKEN` / `NAPCAT_WEBUI_TOKEN` 从演示默认值（`luopita` / `napcat`）改掉。
3. 不要把 `6099`（NapCat WebUI）、`5432`、`6379` 暴露到公网。
4. 保持 `data/napcat/QQ/` 与账号级 `onebot11_<qq>.json` 不进公开仓库/备份。
5. `:5170` 前建议反代 + TLS；对外 webhook 可加限流。
6. 收紧 `COMMAND_ALLOWLIST`。主人 `run_shell` 只能跑白名单程序，且不是 bash；群友拿不到这套工具。
7. 不要把 admin token 长期写在 TUI WebSocket URL 的查询串里再截图外传。

## Token 掩码与鉴权

`GET /api/config` 返回掩码后的密钥。`PUT` 时空值或 `****` 掩码不会覆盖已有密钥。

管理接口在配置了 token 后接受：

- `Authorization: Bearer <token>`
- `X-Admin-Token`
- 查询参数 `admin_token`（仅建议本机）

NapCat webhook 用 OneBot `access_token` / `Authorization` / `x-signature` 校验，失败 401。

## 默认拒绝的能力

- 危险 NapCat 动作（cookies、凭证、退出登录等）管理 API 与 `qq_*` 工具都会拦。
- 普通用户要求改配置、动本机、看主人私事时，模型侧被要求回答「这我做不了」，且根本没有 shell / cron / qq 工具。
- 出站清洗会丢掉 tool-call 标记，避免把内部协议泄漏到群里；这是展示层防护，不是权限边界。
