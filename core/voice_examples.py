from __future__ import annotations

import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable

import yaml

from core.group_talk import looks_like_tech
from utils.config import ROOT, atomic_write_text

VOICE_EXAMPLES_FILE = ROOT / "config" / "voice_examples.yaml"
MAX_EXAMPLES = 4
MAX_CHARS = 800

_VALID_SCENES = {"tech", "chat", "image", "silence"}
_VALID_MODES = {"direct", "chime", "bare_wake"}
_VALID_RELATIONS = {"owner", "peer"}
SCENES = tuple(sorted(_VALID_SCENES))
MODES = ("direct", "chime", "bare_wake")
RELATIONS = ("owner", "peer")

_cache: tuple[str, float, tuple["VoiceExample", ...]] | None = None


@dataclass(frozen=True)
class VoiceExample:
    id: str
    scene: str
    mode: str
    relation: str
    input: str
    good: str
    bad: str = ""


def infer_scene(*, text: str = "", has_media: bool = False) -> str:
    if has_media:
        return "image"
    if looks_like_tech(text):
        return "tech"
    return "chat"


def infer_mode(*, chime_in: bool = False, bare_wake: bool = False) -> str:
    if bare_wake:
        return "bare_wake"
    if chime_in:
        return "chime"
    return "direct"


def examples_file() -> Path:
    override = os.getenv("LUOPITA_EXAMPLES_FILE")
    if override:
        return Path(override)
    return VOICE_EXAMPLES_FILE


def reset_examples_cache() -> None:
    global _cache
    _cache = None


def example_to_dict(example: VoiceExample) -> dict[str, str]:
    return asdict(example)


def load_examples(path: Path | None = None) -> list[VoiceExample]:
    global _cache
    target = path or examples_file()
    key = str(target)
    mtime = 0.0
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return []
    if _cache is not None and _cache[0] == key and _cache[1] == mtime:
        return list(_cache[2])
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    items = raw.get("examples") if isinstance(raw, dict) else raw
    examples: list[VoiceExample] = []
    if isinstance(items, list):
        for item in items:
            parsed = _parse_example(item)
            if parsed is not None:
                examples.append(parsed)
    _cache = (key, mtime, tuple(examples))
    return examples


def parse_example_payload(raw: object, *, index: int = 0) -> VoiceExample:
    if not isinstance(raw, dict):
        raise ValueError(f"invalid example at {index}")
    payload = dict(raw)
    if not str(payload.get("id") or "").strip():
        payload["id"] = f"ex_{index + 1}"
    parsed = _parse_example(payload)
    if parsed is None:
        raise ValueError(f"invalid example at {index}")
    return parsed


def save_examples(examples: Iterable[VoiceExample], path: Path | None = None) -> list[VoiceExample]:
    target = path or examples_file()
    cleaned: list[VoiceExample] = []
    used: set[str] = set()
    for item in examples:
        ident = (item.id or "").strip() or f"{item.scene}_{item.mode}_{item.relation}"
        base = ident
        n = 2
        while ident in used:
            ident = f"{base}_{n}"
            n += 1
        used.add(ident)
        cleaned.append(item if ident == item.id else replace(item, id=ident))
    payload = {"examples": [example_to_dict(item) for item in cleaned]}
    atomic_write_text(
        target,
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False),
    )
    reset_examples_cache()
    return load_examples(target)


def select_examples(
    examples: Iterable[VoiceExample] | None = None,
    *,
    mode: str,
    relation: str,
    scene: str,
    chime_in: bool = False,
    bare_wake: bool = False,
) -> list[VoiceExample]:
    pool = list(examples) if examples is not None else load_examples()
    wanted_mode = mode if mode in _VALID_MODES else infer_mode(chime_in=chime_in, bare_wake=bare_wake)
    wanted_scene = scene if scene in _VALID_SCENES else "chat"
    wanted_relation = relation if relation in _VALID_RELATIONS else "peer"

    scored: list[tuple[int, str, VoiceExample]] = []
    for example in pool:
        if not _eligible(example, chime_in=chime_in, bare_wake=bare_wake):
            continue
        score = 0
        if example.mode == wanted_mode:
            score += 3
        if example.scene == wanted_scene:
            score += 2
        if example.relation == wanted_relation:
            score += 1
        if chime_in and example.scene == "silence":
            score += 4
        if bare_wake and example.mode == "bare_wake":
            score += 3
        scored.append((score, example.id, example))
    scored.sort(key=lambda item: (-item[0], item[1]))

    picked: list[VoiceExample] = []
    used = 0
    if chime_in:
        for _, _, example in scored:
            if example.scene == "silence":
                picked.append(example)
                used += len(_format_one(example))
                break

    for _, _, example in scored:
        if example in picked:
            continue
        block = _format_one(example)
        if picked and used + len(block) > MAX_CHARS:
            continue
        if len(picked) >= MAX_EXAMPLES:
            break
        picked.append(example)
        used += len(block)

    if picked and not any(item.bad.strip() for item in picked):
        for _, _, example in scored:
            if example.bad.strip() and example not in picked:
                if len(picked) >= MAX_EXAMPLES:
                    picked[-1] = example
                else:
                    picked.append(example)
                break
    return picked[:MAX_EXAMPLES]


def format_examples_block(
    examples: Iterable[VoiceExample],
    *,
    bot_name: str = "小Lu",
) -> str:
    items = [item for item in examples if item.good.strip()]
    if not items:
        return ""
    name = (bot_name or "小Lu").strip() or "小Lu"
    lines = [
        "[说话样例]",
        "这些是口吻，不是记忆，不要复述，不要当成发生过的事。",
    ]
    for item in items:
        lines.append(_format_one(item, bot_name=name).rstrip())
    return "\n".join(lines).strip()


def _eligible(example: VoiceExample, *, chime_in: bool, bare_wake: bool) -> bool:
    if bare_wake and example.scene == "tech":
        return False
    if chime_in and example.mode != "chime" and example.scene != "silence":
        return False
    return True


def _parse_example(raw: object) -> VoiceExample | None:
    if not isinstance(raw, dict):
        return None
    ident = str(raw.get("id") or "").strip()
    scene = str(raw.get("scene") or "").strip()
    mode = str(raw.get("mode") or "").strip()
    relation = str(raw.get("relation") or "").strip()
    good = str(raw.get("good") or "").strip()
    if not ident or scene not in _VALID_SCENES or mode not in _VALID_MODES:
        return None
    if relation not in _VALID_RELATIONS or not good:
        return None
    return VoiceExample(
        id=ident,
        scene=scene,
        mode=mode,
        relation=relation,
        input=str(raw.get("input") or "").strip(),
        good=good,
        bad=str(raw.get("bad") or "").strip(),
    )


def _format_one(example: VoiceExample, *, bot_name: str = "小Lu") -> str:
    name = (bot_name or "小Lu").strip() or "小Lu"
    lines = [f"用户: {example.input or '（无）'}", f"{name}: {example.good}"]
    if example.bad.strip():
        lines.append(f"不要像: {example.bad}")
    return "\n".join(lines)
