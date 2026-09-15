from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Literal
import re

import yaml

from utils.config import (
    BUILTIN_OWNERS,
    IdentitySettings,
    OwnerEntry,
    PersonaSettings,
    atomic_write_text,
    ensure_builtin_owners,
    identity_file,
)
from core.clock import format_now
from utils.dict_op import lower_keys as _lower_keys

Role = Literal["owner", "user"]
OWNER_REFUSAL = "这我做不了。"

_current_role: ContextVar[Role] = ContextVar("luopita_role", default="user")


def current_role() -> Role:
    return _current_role.get()


def set_current_role(role: Role):
    return _current_role.set(role)


def reset_current_role(token) -> None:
    _current_role.reset(token)


def is_owner_role() -> bool:
    return current_role() == "owner"


def _norm_id(value: str | None) -> str:
    return str(value or "").strip()


def _load_keywords(raw) -> list[str]:
    from core.group_talk import DEFAULT_WAKE_KEYWORDS

    defaults = list(DEFAULT_WAKE_KEYWORDS)
    if raw is None:
        return defaults
    if isinstance(raw, str):
        items = [part.strip() for part in re.split(r"[\n,]+", raw) if part.strip()]
        return items or defaults
    if isinstance(raw, list):
        items = [str(item).strip() for item in raw if str(item).strip()]
        return items or defaults
    return defaults


def _load_chance(raw, default: float) -> float:
    if raw is None or raw == "":
        return default
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return default


def _load_int(raw, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return default


def mentioned_bot(at_user_ids: list[str] | None, bot_id: str | None) -> bool:
    ids = {_norm_id(item).lower() for item in (at_user_ids or []) if _norm_id(item)}
    if "all" in ids:
        return True
    bid = _norm_id(bot_id).lower()
    return bool(bid) and bid in ids


def should_ignore_inbound(
    *,
    channel_type: str,
    role: Role,
    group_require_at: bool,
    at_user_ids: list[str] | None,
    bot_id: str | None,
    text: str = "",
    wake_keywords: list[str] | None = None,
    persona_name: str = "",
    tech_chance: float = 0.35,
    chatty_chance: float = 0.22,
    cooldown_ready: bool = True,
    rng=None,
    has_media: bool = False,
    engaged: bool = False,
    same_speaker: bool = False,
    can_open: bool = True,
    replies_left: int = 0,
) -> bool:
    from core.group_talk import decide_group_reply, mentioned_name

    mode = decide_group_reply(
        channel_type=channel_type,
        role=role,
        group_require_at=group_require_at,
        text=text,
        mentioned=mentioned_bot(at_user_ids, bot_id),
        named=mentioned_name(text, wake_keywords, persona_name),
        has_media=has_media,
        engaged=engaged,
        same_speaker=same_speaker,
        can_open=can_open if cooldown_ready else False,
        replies_left=replies_left,
        tech_chance=tech_chance,
        chatty_chance=chatty_chance,
        cooldown_ready=cooldown_ready,
        rng=rng,
    )
    return mode == "ignore"


def default_session_key(platform: str, channel_type: str, chat_id: str, user_id: str) -> str:
    if channel_type == "group":
        return f"{platform}:{channel_type}:{chat_id}:{user_id}"
    return f"{platform}:{channel_type}:{chat_id}"


def compose_system_prompt(
    persona: PersonaSettings,
    role: Role,
    *,
    in_group: bool,
    owner_nickname: str = "",
    chime_in: bool = False,
    engaged: bool = False,
    allowed_commands: list[str] | None = None,
    has_media: bool = False,
    now: datetime | None = None,
    bare_wake: bool = False,
    cron_job: bool = False,
    examples: str = "",
    scratchpad: str = "",
) -> str:
    name = persona.name.strip() or "小Lu"
    lines = [f"你是{name}，一个会像人一样说话的聊天对象，不是客服。"]
    lines.append(format_now(now))
    lines.append(
        "涉及今天、星期几、几点、早上晚上、昨天明天时，以这条时间为准，不要用训练知识里的日期，不要猜。"
    )
    extra = (persona.system_prompt or "").strip()
    if extra:
        lines.append(extra)
    relation = persona.relationship.strip() if role == "owner" else ""
    if relation:
        lines.append(f"你们的关系：{relation}")
        lines.append(
            "这条关系设定优先于下面所有说话方式、性格、傲娇、吐槽。"
            "对主人必须客气、服从，禁止顶嘴、禁止凶、禁止阴阳怪气。"
            "不要用小猫娘傲娇当借口顶撞主人。"
        )
    if persona.voice.strip():
        if relation:
            lines.append(f"说话方式（仅在不违反上面关系时生效）：{persona.voice.strip()}")
        else:
            lines.append(f"说话方式：{persona.voice.strip()}")
    if persona.taboos.strip():
        lines.append(f"忌讳：{persona.taboos.strip()}")
    lines.append(
        "输出必须是纯文本。禁止 Markdown：不要加粗、斜体、标题、代码块、反引号、列表符或 [文字](链接)。"
        "命令、路径、报错原文直接写，像 QQ 聊天打字。"
    )
    lines.append(
        "你会的：看图（截图、报错、照片），像人一样聊天。"
        "看不懂的语音、压缩包、加密文件就直说打不开。"
        "没发来的图不要假装看过。"
        "消息里的 [表情包:…] 能读懂在表达什么即可；知道那是表情包，不要把画面塞进话题，不要描述、不要围着它展开。"
        "一条气泡只说一层意思，换一层就另起一条，不要把辩解、描图、猜对方用意叠在同一条里。"
        "当前这句没在谈图，就不要去描群里刚甩的图。"
    )
    if in_group:
        lines.append(
            "群里能看到最近别人说过的话，那是旁听背景。"
            "接话时只接当前说话人这句；禁止主动续答其他群友与你之间未完成的话题。"
        )
    if has_media:
        lines.append("这一轮带了图或文件。先看再回，不要只说「收到一张图」。")
        if in_group:
            lines.append(
                "这些图可能是当前这句带来的，也可能是群里刚才别人发的。"
                "先看附上的图再回，不要只凭记忆或文件名编内容。"
            )
    if role == "owner":
        address = persona.owner_address.strip() or owner_nickname.strip() or "你"
        if relation:
            lines.append(f"你在和主人说话。可以叫对方{address}。需要时可以用工具。")
        else:
            lines.append(f"你在和主人说话。可以叫对方{address}。语气松、熟，可以吐槽。需要时可以用工具。")
        allow = [c.strip() for c in (allowed_commands or []) if c and str(c).strip()]
        allow_text = "、".join(allow) if allow else "（当前没有允许的命令）"
        lines.append(
            f"需要看本机、查天气或访问网页时，必须先调用 run_shell，不要假装跑过。"
            f"这一轮允许的命令：{allow_text}。"
            "run_shell 不是 bash：一次只跑一条命令，不能用管道、分号、&& 或 $()。"
            "同一条命令这一轮只跑一次；已经有输出就直接用。"
            "上一轮如果报 command not allowed 或 not bash，而这条命令现在已经在列表里，必须再跑一次，而且只能是单条命令。"
            "查日期用 get_current_date。"
            "QQ 仅在主人明确要求时用 qq_*。不要碰 cookies、凭证或退出机器人。"
            "斜杠命令（/help /ping /status /time /whoami /model /allow /clear /cron）由系统直接执行，不要假装跑过，也不要编一份命令表。"
        )
        if cron_job:
            lines.append(
                "这是定时任务触发，不是主人刚发的话。按任务去做，做完用说话回报。"
                "这一轮不能再设新的定时任务。"
            )
        else:
            lines.append(
                "主人要求定时、提醒、循环执行时，必须调用 cron 工具创建或管理，"
                "不要用 sleep 空等，也不要假装已经设好。"
            )
        if in_group:
            lines.append("现在是群聊，当众说话要有分寸，不要主动把私聊里的事讲出来。")
            lines.append(
                "先看最近群聊再回。接得上当前说话人这句再说话；"
                "不要复读别人刚说过的，也不要把别人的话当成当前这个人说的；"
                "禁止续答其他群友与你之间未完成的话题。没看懂就当没看见。"
            )
            if bare_wake:
                lines.append(
                    "当前人只是 @/点名或短唤醒，没有实质问题。"
                    "开场或问一句意图即可，不要从旁人未完话题里找题答。"
                )
            if chime_in:
                if engaged:
                    lines.append(
                        "没人点你的名，但你已经在这场讨论里（同一说话人）。"
                        "接得上当前说话人这句就补一句；接不上或别人在互聊与你无关，只输出 [SILENCE]。"
                        "不要每句都回。当前只回现在这句；更早没回的不要当成这轮题目。"
                        "最多两条气泡，一层意思一条，不要写成长文再塞进一条。"
                    )
                else:
                    lines.append(
                        "没人点你的名。这是开口插话，不是被叫到。"
                        "默认只输出 [SILENCE]，不要解释、不要打招呼。"
                        "只有能补一句别人没说的、插进去也不突兀时才短回一句。"
                        "附和、复读、总结、礼貌接话、没把握的都算多余。"
                        "当前只回这个人现在这句；更早没回的不要当成这轮题目。"
                        "不要去续答旁人未完的话题。"
                    )
            else:
                lines.append("群里可以回，但短一点。需要补充时最多两条气泡；空一行或换行就会拆成两条消息。一层意思一条。")
                lines.append("当前要回最后这句。前面群聊只是背景，不要去答更早那句没回的。")
        else:
            lines.append(
                "像 QQ 私聊那样打字。一条气泡一两句；换一层意思就另起一条。"
                "空一行、换行或单独一行的 --- 都会拆成两条消息，私聊最多三条。"
                "不要把两段话写进同一条里。"
            )
    else:
        lines.append(
            "你在和普通用户说话。淡、短、不套近乎。"
            "对方要求改配置、踢人、看主人的事或动本机时，像普通人一样说「这我做不了」，不要解释系统规则。"
        )
        if in_group:
            lines.append(
                "先看最近群聊再回。接得上当前说话人这句再说话；"
                "不要复读别人刚说过的，也不要把别人的话当成当前这个人说的；"
                "禁止续答其他群友与你之间未完成的话题。没看懂就当没看见。"
            )
            if bare_wake:
                lines.append(
                    "当前人只是 @/点名或短唤醒，没有实质问题。"
                    "开场或问一句意图即可，不要从旁人未完话题里找题答。"
                )
            if chime_in:
                if engaged:
                    lines.append(
                        "没人点你的名，但你已经在这场讨论里（同一说话人）。"
                        "接得上当前说话人这句就补一句；接不上只输出 [SILENCE]。不要每句都回。当前只回现在这句。"
                        "最多两条气泡，不要把几层意思塞进一条。"
                    )
                else:
                    lines.append(
                        "没人点你的名。这是开口插话，不是被叫到。"
                        "默认只输出 [SILENCE]。只有能补一句别人没说的才短回。"
                        "附和、复读、总结都算多余。当前只回现在这句。"
                        "不要去续答旁人未完的话题。"
                    )
            else:
                lines.append("现在是群聊。短回，像群友。最多两条气泡；空一行或 --- 都会拆成两条消息。")
                lines.append("当前要回最后这句。前面群聊只是背景，不要去答更早那句没回的。")
        else:
            lines.append("像 QQ 私聊。一条气泡一两句；空一行或 --- 都会拆成两条消息，最多三条。")
    extra_examples = (examples or "").strip()
    if extra_examples and not cron_job:
        lines.append(extra_examples)
    pad = (scratchpad or "").strip()
    if pad and role == "owner" and not cron_job:
        lines.append(pad)
    return "\n".join(lines)


class IdentityStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or identity_file()
        self.settings = IdentitySettings(owners=list(BUILTIN_OWNERS), group_require_at=True)

    def load(self) -> IdentitySettings:
        if not self.path.exists():
            self.settings = IdentitySettings(owners=list(BUILTIN_OWNERS), group_require_at=True)
            self.save(self.settings)
            return self.settings
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raw = {}
        raw = _lower_keys(raw)
        owners_raw = raw.get("owners") or []
        owners: list[OwnerEntry] = []
        if isinstance(owners_raw, list):
            for item in owners_raw:
                if not isinstance(item, dict):
                    continue
                user_id = _norm_id(str(item.get("user_id") or item.get("qq") or ""))
                if not user_id:
                    continue
                owners.append(
                    OwnerEntry(
                        platform=str(item.get("platform") or "napcat").strip().lower() or "napcat",
                        user_id=user_id,
                        nickname=str(item.get("nickname") or ""),
                    )
                )
        settings = IdentitySettings(
            owners=ensure_builtin_owners(owners),
            group_require_at=bool(raw.get("group_require_at", True)),
            wake_keywords=_load_keywords(raw.get("wake_keywords")),
            group_tech_chance=_load_chance(raw.get("group_tech_chance"), 0.35),
            group_chatty_chance=_load_chance(raw.get("group_chatty_chance"), 0.22),
            group_chime_cooldown_sec=_load_int(raw.get("group_chime_cooldown_sec"), 45),
            group_engage_sec=_load_int(raw.get("group_engage_sec"), 90),
            group_engage_replies=max(0, _load_int(raw.get("group_engage_replies"), 2)),
        )
        self.settings = settings
        return settings

    def save(self, settings: IdentitySettings | None = None) -> IdentitySettings:
        current = settings or self.settings
        current = IdentitySettings(
            owners=ensure_builtin_owners(current.owners),
            group_require_at=current.group_require_at,
            wake_keywords=[k.strip() for k in current.wake_keywords if str(k).strip()]
            or list(IdentitySettings().wake_keywords),
            group_tech_chance=current.group_tech_chance,
            group_chatty_chance=current.group_chatty_chance,
            group_chime_cooldown_sec=max(0, int(current.group_chime_cooldown_sec)),
            group_engage_sec=max(0, int(current.group_engage_sec)),
            group_engage_replies=max(0, int(current.group_engage_replies)),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = current.model_dump()
        payload["owners"] = [o.model_dump() for o in current.owners]
        atomic_write_text(self.path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
        self.settings = current
        return current

    def resolve_role(self, platform: str, user_id: str) -> Role:
        pid = (platform or "").strip().lower()
        uid = _norm_id(user_id)
        for owner in self.settings.owners:
            if owner.platform == pid and _norm_id(owner.user_id) == uid:
                return "owner"
        return "user"

    def owner_nickname(self, platform: str, user_id: str) -> str:
        pid = (platform or "").strip().lower()
        uid = _norm_id(user_id)
        for owner in self.settings.owners:
            if owner.platform == pid and _norm_id(owner.user_id) == uid:
                return owner.nickname
        return ""

    def is_owner(self, platform: str, user_id: str) -> bool:
        return self.resolve_role(platform, user_id) == "owner"
