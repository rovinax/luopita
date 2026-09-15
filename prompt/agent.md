# Agent 提示怎么拼

本文件不是运行时加载的协议。旧版「每次只输出一个 `[text]` / `[command]` 标签」的状态机已经废弃。

当前系统提示由 `core/identity.py` 的 `compose_system_prompt` 在每一轮现场拼出，再交给 LangGraph。工具由 `agent/tools.py` 按角色挂上，模型用标准 tool-call，而不是在正文里嵌协议标签。

## 拼进去的块（按顺序）

1. `config/person.yaml`：名字、口吻、禁忌、关系、`system_prompt`
2. 北京时间
3. 角色：`owner` 可调用工具；`user` 没有 shell / QQ / cron
4. 通道：群聊强调说话人隔离；私聊按气泡拆条
5. 模式：`direct` / `chime`（插话，默认可 `[SILENCE]`）/ `bare_wake`（只开场，不抢旁人话题）
6. 说话样例：`config/voice_examples.yaml` 按 scene / mode / relation 选，最多 4 条
7. 主人工作态：scratchpad（焦点、开放承诺、近期注意），cron 回合不注入
8. 长期记忆召回、群上下文块（管线组装）由编排器另附

定时任务回合会额外说明「这不是主人刚发的话，且不能再设新 cron」。

## 工具边界

| 角色 | 工具 |
|------|------|
| owner | `get_current_date`、`run_shell`（白名单，非 bash）、`qq_*`、`cron` |
| user | 仅 `get_current_date` |
| cron 到期回合 | 与 owner 相同，但不挂 `cron` |

斜杠命令（`/help` `/cron` 等）由 `core/commands.py` 拦截，不经过模型。

改口吻优先改 yaml 和样例，而不是把规则写回这个文件。
