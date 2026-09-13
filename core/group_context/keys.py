from __future__ import annotations

import hashlib
import math
import uuid
from typing import Any

CONV_TTL_SEC = 30 * 60
GROUP_MEM_TTL_SEC = 7 * 24 * 3600
ENTITY_TTL_SEC = 30 * 60
SUMMARY_TTL_SEC = 7 * 24 * 3600
CONV_MAX_TURNS = 40
GROUP_MEM_MAX = 200
ENTITY_STACK_MAX = 20
SHORT_TURNS = 10
COMPRESS_CHAR_THRESHOLD = 3500
TOPIC_DRIFT_THRESHOLD = 0.42
SEMANTIC_TRIGGER_THRESHOLD = 0.55
EMBED_DIM = 1536


def new_shard_id() -> str:
    return uuid.uuid4().hex[:16]


def conv_key(platform: str, chat_id: str, user_id: str, shard_id: str) -> str:
    return f"conv:{platform}:{chat_id}:{user_id}:{shard_id}"


def group_mem_key(platform: str, chat_id: str) -> str:
    return f"group_mem:{platform}:{chat_id}"


def summary_key(shard_id: str) -> str:
    return f"summary:{shard_id}"


def entity_key(platform: str, chat_id: str) -> str:
    return f"entity:{platform}:{chat_id}"


def lock_key(shard_id: str) -> str:
    return f"lock:shard:{shard_id}"


def msg_shard_key(platform: str, chat_id: str, message_id: str) -> str:
    return f"msg_shard:{platform}:{chat_id}:{message_id}"


def user_active_key(platform: str, chat_id: str, user_id: str) -> str:
    return f"user_active:{platform}:{chat_id}:{user_id}"


def shard_session_key(platform: str, chat_id: str, shard_id: str) -> str:
    """LangGraph thread_id for a group shard."""
    return f"{platform}:group:{chat_id}:shard:{shard_id}"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def hash_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic bag-of-shingles embedding (no external API)."""
    raw = (text or "").strip().lower()
    vec = [0.0] * dim
    if not raw:
        return vec
    tokens = [raw[i : i + 3] for i in range(max(1, len(raw) - 2))]
    tokens.append(raw[:8])
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def blend_centroid(old: list[float] | None, new: list[float], alpha: float = 0.3) -> list[float]:
    if not old:
        return list(new)
    if len(old) != len(new):
        return list(new)
    out = [(1.0 - alpha) * o + alpha * n for o, n in zip(old, new)]
    norm = math.sqrt(sum(v * v for v in out)) or 1.0
    return [v / norm for v in out]


def turn_payload(
    *,
    role: str,
    user_id: str = "",
    sender_name: str = "",
    content: str,
    message_id: str = "",
) -> dict[str, Any]:
    return {
        "role": role,
        "user_id": user_id or "",
        "sender_name": sender_name or "",
        "content": content,
        "message_id": message_id or "",
    }
