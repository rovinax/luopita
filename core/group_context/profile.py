from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.clock import format_shanghai_display, now_shanghai, parse_created_at

FAMILIARITY_ORDER = {"stranger": 0, "peer": 1, "familiar": 2}
FAMILIARITY_ZH = {"stranger": "生", "peer": "熟", "familiar": "很熟"}
STACK_MAX = 8
PROFILE_PROMPT_MAX = 150
MIN_PAIR_TURNS = 4
REFRESH_AFTER_SEC = 24 * 3600
EXTRACT_TURN_LIMIT = 40

EXTRACT_SYS = (
    "从下面「这个人与机器人」的对话里抽取当前说话人画像。"
    "只输出 JSON，不要解释，不要猜测，没有依据的字段留空。"
    "字段：address(称呼字符串), familiarity(stranger|peer|familiar),"
    " reply_pref(怎么回，一句话), stack(技术栈字符串数组),"
    " taboos(雷点一句话), recent(近两周在做的事), evidence(一句原文证据)。"
    "只用这个人与机器人的来回，不要用旁人的话。"
)


@dataclass
class ProfileCard:
    address: str = ""
    familiarity: str = "stranger"
    reply_pref: str = ""
    stack: list[str] = field(default_factory=list)
    taboos: str = ""
    recent: str = ""
    evidence: str = ""
    updated_at: str = ""

    def worth_prompt(self) -> bool:
        if self.reply_pref.strip() or self.stack or self.taboos.strip() or self.recent.strip():
            return True
        return self.familiarity in {"peer", "familiar"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address.strip(),
            "familiarity": _norm_familiarity(self.familiarity),
            "reply_pref": self.reply_pref.strip(),
            "stack": _norm_stack(self.stack),
            "taboos": self.taboos.strip(),
            "recent": self.recent.strip(),
            "evidence": self.evidence.strip(),
            "updated_at": self.updated_at.strip(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProfileCard":
        raw = data if isinstance(data, dict) else {}
        stack = raw.get("stack") or []
        if isinstance(stack, str):
            stack = [part.strip() for part in re.split(r"[,，、;/]+", stack) if part.strip()]
        return cls(
            address=str(raw.get("address") or "").strip(),
            familiarity=_norm_familiarity(str(raw.get("familiarity") or "")),
            reply_pref=str(raw.get("reply_pref") or "").strip(),
            stack=_norm_stack(stack),
            taboos=str(raw.get("taboos") or "").strip(),
            recent=str(raw.get("recent") or "").strip(),
            evidence=str(raw.get("evidence") or "").strip(),
            updated_at=str(raw.get("updated_at") or "").strip(),
        )


def card_from_row(row: dict[str, Any] | None) -> ProfileCard:
    if not row:
        return ProfileCard()
    raw = row.get("card")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    card = ProfileCard.from_dict(raw if isinstance(raw, dict) else {})
    if not card.address:
        card.address = str(row.get("display_name") or "").strip()
    if not card.reply_pref:
        card.reply_pref = str(row.get("preferences") or "").strip()
    if not card.taboos:
        card.taboos = str(row.get("notes") or "").strip()
    if not card.updated_at:
        card.updated_at = str(row.get("updated_at") or "").strip()
    return card


def format_profile_card(
    card: ProfileCard | None,
    *,
    sender_name: str = "",
    chime: bool = False,
    max_chars: int = PROFILE_PROMPT_MAX,
) -> str:
    if card is None or not card.worth_prompt():
        return ""
    name = (card.address or sender_name or "").strip() or "对方"
    fam = FAMILIARITY_ZH.get(_norm_familiarity(card.familiarity), "")
    bits = [name]
    if fam:
        bits.append(fam)
    if not chime and card.reply_pref.strip():
        bits.append(card.reply_pref.strip())
    lines = [f"[当前说话人]\n{'｜'.join(bits)}"]
    if chime:
        if card.taboos.strip():
            lines.append(f"雷点：{card.taboos.strip()}")
    else:
        extras: list[str] = []
        if card.stack:
            extras.append("栈：" + "、".join(card.stack))
        if card.taboos.strip():
            extras.append(f"雷点：{card.taboos.strip()}")
        if card.recent.strip():
            extras.append(f"近期：{card.recent.strip()}")
        lines.extend(extras)
    lines.append("不要复述这些设定，只用在怎么叫、回多长、回多硬")
    text = "\n".join(lines).strip()
    budget = max(40, int(max_chars or PROFILE_PROMPT_MAX))
    if len(text) > budget:
        text = text[: budget - 1].rstrip() + "…"
    return text


def serialize_profile_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    card = card_from_row(row)
    display = str(row.get("display_name") or card.address or row.get("user_id") or "").strip()
    shown = format_shanghai_display(row.get("updated_at") or card.updated_at or "")
    card_data = card.to_dict()
    if shown:
        card_data["updated_at"] = shown
    return {
        "platform": str(row.get("platform") or ""),
        "chat_id": str(row.get("chat_id") or ""),
        "user_id": str(row.get("user_id") or ""),
        "display_name": display,
        "updated_at": shown,
        "card": card_data,
        "prompt": format_profile_card(card, sender_name=display),
    }


def merge_profile(old: ProfileCard | None, new: ProfileCard | None) -> ProfileCard:
    left = old or ProfileCard()
    right = new or ProfileCard()
    merged = ProfileCard(
        address=_prefer(right.address, left.address),
        familiarity=_upgrade_familiarity(left.familiarity, right.familiarity),
        reply_pref=_prefer(right.reply_pref, left.reply_pref),
        stack=_merge_stack(left.stack, right.stack),
        taboos=_merge_phrases(left.taboos, right.taboos),
        recent=_prefer(right.recent, left.recent),
        evidence=_prefer(right.evidence, left.evidence),
        updated_at=now_shanghai().isoformat(),
    )
    return merged


def pair_turns(turns: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for turn in turns or []:
        role = str(turn.get("role") or "")
        if role in {"user", "assistant"}:
            out.append(turn)
    return out


def should_refresh(
    card: ProfileCard | None,
    turns: list[dict[str, Any]] | None,
    *,
    compressed: bool = False,
    now: datetime | None = None,
) -> bool:
    pairs = pair_turns(turns)
    if len(pairs) < MIN_PAIR_TURNS:
        return False
    if compressed:
        return True
    stamp = (card.updated_at if card else "") or ""
    if not stamp:
        return True
    parsed = parse_created_at(stamp)
    if parsed is None:
        return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - parsed).total_seconds() >= REFRESH_AFTER_SEC


def parse_extract_json(text: str) -> ProfileCard | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    card = ProfileCard.from_dict(data)
    if not card.worth_prompt() and not card.address:
        return None
    return card


async def extract_profile_card(
    turns: list[dict[str, Any]],
    sender_name: str = "",
    *,
    llm: Any = None,
    bot_name: str = "小Lu",
) -> ProfileCard | None:
    if llm is None:
        return None
    pairs = pair_turns(turns)[-EXTRACT_TURN_LIMIT:]
    if len(pairs) < MIN_PAIR_TURNS:
        return None
    lines: list[str] = []
    for turn in pairs:
        if str(turn.get("role") or "") == "assistant":
            name = (bot_name or "小Lu").strip() or "小Lu"
        else:
            name = str(turn.get("sender_name") or sender_name or "用户").strip() or "用户"
        content = " ".join(str(turn.get("content") or "").split())[:240]
        if content:
            lines.append(f"{name}: {content}")
    if len(lines) < MIN_PAIR_TURNS:
        return None
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        response = await llm.ainvoke(
            [SystemMessage(content=EXTRACT_SYS), HumanMessage(content="\n".join(lines))]
        )
    except Exception:
        return None
    content = getattr(response, "content", "") or ""
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        content = "".join(parts)
    return parse_extract_json(str(content))


def _norm_familiarity(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw in FAMILIARITY_ORDER:
        return raw
    aliases = {"生": "stranger", "熟": "peer", "很熟": "familiar", "陌生人": "stranger"}
    return aliases.get((value or "").strip(), "stranger")


def _norm_stack(items: list[Any] | tuple[Any, ...] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        token = str(item or "").strip()
        key = token.lower()
        if not token or key in seen:
            continue
        seen.add(key)
        out.append(token)
        if len(out) >= STACK_MAX:
            break
    return out


def _prefer(new: str, old: str) -> str:
    fresh = (new or "").strip()
    return fresh if fresh else (old or "").strip()


def _upgrade_familiarity(old: str, new: str) -> str:
    left = _norm_familiarity(old)
    right = _norm_familiarity(new)
    if FAMILIARITY_ORDER[right] > FAMILIARITY_ORDER[left]:
        return right
    return left


def _merge_stack(old: list[str], new: list[str]) -> list[str]:
    return _norm_stack([*old, *new])


def _merge_phrases(old: str, new: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"[；;、\n]+", f"{old}；{new}"):
        token = chunk.strip()
        key = token.lower()
        if not token or key in seen:
            continue
        seen.add(key)
        parts.append(token)
    return "；".join(parts)
