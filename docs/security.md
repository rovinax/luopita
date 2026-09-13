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

## 公网部署前清单

1. 设置强 `LUOPITA_ADMIN_TOKEN`，没有 token 不要暴露管理台。
2. 把 `LUOPITA_NAPCAT_TOKEN` / `NAPCAT_WEBUI_TOKEN` 从演示默认值（`luopita` / `napcat`）改掉。
3. 不要把 `6099`（NapCat WebUI）、`5432`、`6379` 暴露到公网。
4. 保持 `data/napcat/QQ/` 与账号级 `onebot11_<qq>.json` 不进公开仓库/备份。
5. `:5170` 前建议反代 + TLS；对外 webhook 可加限流。

## Token 掩码

`GET /api/config` 返回掩码后的密钥。`PUT` 时空值不会覆盖已有密钥。
