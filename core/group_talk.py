from __future__ import annotations

import re
from typing import Literal

from core.clock import format_hhmm

GroupReplyMode = Literal["ignore", "direct", "chime"]

DEFAULT_WAKE_KEYWORDS = [
    "小lu",
    "小Lu",
    "小LU",
    "luopita",
    "Luopita",
    "小陆",
    "小路",
]

_TECH_HINTS = (
    "报错",
    "报了",
    "error",
    "traceback",
    "exception",
    "panic",
    "segfault",
    "bug",
    "debug",
    "stack",
    "compile",
    "syntax",
    "typeerror",
    "nullpointer",
    "oom",
    "python",
    "javascript",
    "typescript",
    "golang",
    "rust",
    "java",
    "docker",
    "kubernetes",
    "k8s",
    "nginx",
    "postgres",
    "redis",
    "mysql",
    "sqlite",
    "mongodb",
    "fastapi",
    "langchain",
    "react",
    "vue",
    "vite",
    "webpack",
    "npm",
    "pip ",
    "uv ",
    "git ",
    "github",
    "linux",
    "wsl",
    "bash",
    "shell",
    "api",
    "http",
    "tcp",
    "grpc",
    "sql",
    "yaml",
    "json",
    "regex",
    "端口",
    "编译",
    "依赖",
    "部署",
    "容器",
    "镜像",
    "线程",
    "内存泄漏",
    "跑不起来",
    "起不来",
    "怎么装",
    "怎么配",
    "怎么写",
    "如何部署",
    "为啥报",
    "命令行",
    "接口",
    "数据库",
    "报错了",
    "崩了",
    "挂了",
)

_FILLER = {
    "hello",
    "hi",
    "hey",
    "hello everyone",
    "哈喽",
    "你好",
    "大家好",
    "在吗",
    "在不在",
    "早上好",
    "晚上好",
    "早安",
    "晚安",
    "早",
    "哈哈",
    "哈哈哈",
    "呵呵",
    "嗯",
    "哦",
    "好的",
    "好",
    "ok",
    "okay",
    "打卡",
    "签到",
    "收到",
    "1",
    "+",
    "加一",
    "表情包",
    "表情",
    "动画表情",
}

_QUESTION_RE = re.compile(r"[？?]|怎么|为何|为什么|如何|有人会|谁知道|咋整|咋搞|吗$|呢$|求问|请教")
_CODEY_RE = re.compile(r"[`/\\]|\{|\}|err=|errno|http[s]?://|localhost|\.py\b|\.ts\b|\.js\b|\.go\b", re.I)
_STICKER_ONLY_RE = re.compile(r"^(?:\s*\[(?:表情包(?::[^\]]+)?|表情|动画表情)\]\s*)+$")
_IMAGE_TALK_RE = re.compile(
    r"\[图片|(这|那|刚|看|发|甩).{0,8}图|图(是什么|是啥|啥意思|什么)"
)


def looks_like_filler(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return True
    if _STICKER_ONLY_RE.fullmatch(raw):
        return True
    compact = re.sub(r"[\s\[\]@]+", "", raw)
    if len(compact) <= 2:
        return True
    lowered = raw.lower().strip("。.!?！？~～")
    compact_lower = compact.lower()
    if lowered in _FILLER or compact_lower in _FILLER:
        return True
    if re.fullmatch(r"[哈呵嘿啊嗯哦额]+", compact):
        return True
    if re.fullmatch(r"[+\d一二三四五]+", compact):
        return True
    return False


def looks_like_question(text: str) -> bool:
    raw = (text or "").strip()
    return bool(_QUESTION_RE.search(raw))


def looks_like_tech(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    if any(hint in lowered for hint in _TECH_HINTS):
        return True
    if _CODEY_RE.search(raw) and (looks_like_question(raw) or len(raw) >= 12):
        return True
    if looks_like_question(raw) and any(word in lowered for word in ("代码", "配置", "服务", "程序", "脚本", "环境", "版本")):
        return True
    return False


def text_asks_about_image(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_IMAGE_TALK_RE.search(raw))


def mentioned_name(text: str, keywords: list[str] | None = None, persona_name: str = "") -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    names = [item.strip() for item in (keywords or DEFAULT_WAKE_KEYWORDS) if item and item.strip()]
    if persona_name.strip():
        names.append(persona_name.strip())
    for name in names:
        token = name.strip()
        if not token:
            continue
        if token.lower() in lowered or token in raw:
            return True
    return False


_AT_TOKEN_RE = re.compile(r"\[@[^\]]+\]|@\S+")
_REPLY_MARK_RE = re.compile(r"\[回复:[^\]]+\]")


def strip_wake_noise(text: str, keywords: list[str] | None = None, persona_name: str = "") -> str:
    """Remove @/reply marks and wake names so remainder is the real ask."""
    raw = _REPLY_MARK_RE.sub(" ", text or "")
    raw = _AT_TOKEN_RE.sub(" ", raw)
    names = [item.strip() for item in (keywords or DEFAULT_WAKE_KEYWORDS) if item and item.strip()]
    if persona_name.strip():
        names.append(persona_name.strip())
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        raw = re.sub(re.escape(name), " ", raw, flags=re.IGNORECASE)
    return " ".join(raw.split()).strip()


def is_bare_wake(
    text: str,
    *,
    keywords: list[str] | None = None,
    persona_name: str = "",
    explicit: bool = False,
) -> bool:
    """True when the user only @/named the bot (or short filler), with no real ask."""
    if not explicit:
        return False
    remainder = strip_wake_noise(text, keywords=keywords, persona_name=persona_name)
    if not remainder:
        return True
    compact = re.sub(r"\s+", "", remainder)
    return looks_like_filler(remainder) and len(compact) <= 8


def decide_group_reply(
    *,
    channel_type: str,
    role: str,
    group_require_at: bool,
    text: str = "",
    mentioned: bool = False,
    named: bool = False,
    has_media: bool = False,
    engaged: bool = False,
    same_speaker: bool = False,
    can_open: bool = True,
    replies_left: int = 0,
    tech_chance: float = 0.35,
    chatty_chance: float = 0.22,
    cooldown_ready: bool = True,
    rng=None,
) -> GroupReplyMode:
    if channel_type != "group" or not group_require_at:
        return "direct"
    if mentioned or named:
        return "direct"
    if looks_like_filler(text) and not has_media:
        return "ignore"
    if engaged:
        if replies_left <= 0:
            return "ignore"
        if same_speaker or looks_like_tech(text) or looks_like_question(text) or has_media:
            return "chime"
        return "ignore"
    if not can_open:
        return "ignore"
    if looks_like_tech(text):
        return "chime"
    if looks_like_question(text) and (has_media or len((text or "").strip()) >= 8):
        return "chime"
    return "ignore"


GROUP_CONTEXT_LIMIT = 30
GROUP_CONTEXT_CHARS = 2500
SILENCE_MARKERS = {"[silence]", "silence", "【沉默】", "（不回）", "(不回)"}


def is_silence_reply(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return True
    first = raw.splitlines()[0].strip().lower()
    if first in SILENCE_MARKERS:
        return True
    return raw.upper() == "[SILENCE]"


def format_group_context(
    events: list[dict],
    *,
    current_user_id: str = "",
    current_name: str = "",
    bot_name: str = "小Lu",
    max_chars: int = GROUP_CONTEXT_CHARS,
) -> str:
    if not events:
        return ""
    lines: list[str] = []
    for event in events:
        role = str(event.get("role") or "user")
        if role == "assistant":
            name = (bot_name or "小Lu").strip() or "小Lu"
        else:
            name = str(event.get("sender_name") or event.get("user_id") or "某人").strip() or "某人"
        text = " ".join(str(event.get("content") or "").split())
        if not text:
            continue
        if len(text) > 200:
            text = text[:200] + "…"
        stamp = format_hhmm(event.get("created_at"))
        if stamp:
            lines.append(f"{name} {stamp}: {text}")
        else:
            lines.append(f"{name}: {text}")
    if not lines:
        return ""
    header = (
        "最近群聊（旁听背景，不是私聊。"
        "不要把别人的话当成当前这个人说的；"
        "其他人与你的对话禁止主动续答）：\n"
    )
    speaker = (current_name or "").strip() or (current_user_id or "").strip() or "对方"
    footer = (
        f"\n当前对你说话的是{speaker}。"
        "只答ta现在这句。"
        "群记忆里其他人与你的未完话题只是旁听背景，禁止主动续答、禁止当成当前任务；"
        "仅当ta明确提到该话题或引用该消息时才可涉及。"
        "更早没回的话不要当成这轮要答的题。"
    )
    budget = max(200, max_chars - len(header) - len(footer))
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        extra = len(line) + 1
        if kept and used + extra > budget:
            break
        kept.append(line)
        used += extra
    kept.reverse()
    return header + "\n".join(kept) + footer
