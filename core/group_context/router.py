from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.group_context.keys import (
    TOPIC_DRIFT_THRESHOLD,
    blend_centroid,
    cosine_similarity,
    hash_embed,
    new_shard_id,
    shard_session_key,
)
from core.group_context.store import HotStore
from core.group_context.trigger import TriggerResult


@dataclass
class RouteResult:
    shard_id: str
    session_key: str
    topic: str = ""
    is_new: bool = False
    reason: str = ""
    centroid: list[float] = field(default_factory=list)
    embedding: list[float] = field(default_factory=list)


class ContextRouter:
    def __init__(self, hot: HotStore, db: Any, embed_fn=hash_embed) -> None:
        self.hot = hot
        self.db = db
        self.embed = embed_fn

    async def route(
        self,
        *,
        platform: str,
        chat_id: str,
        user_id: str,
        text: str,
        trigger: TriggerResult,
        reply_to_ids: list[str] | None = None,
        bot_id: str = "",
    ) -> RouteResult | None:
        if trigger.mode == "ignore":
            return None

        embedding = self.embed(text or "")
        reply_ids = [str(x).strip() for x in (reply_to_ids or []) if str(x).strip()]

        # A. reply-to-bot → inherit shard only if same speaker owns it
        if trigger.replied_bot or reply_ids:
            for mid in reply_ids:
                shard_id = await self.hot.shard_for_message(platform, chat_id, mid)
                if not shard_id and hasattr(self.db, "shard_for_message"):
                    shard_id = await self.db.shard_for_message(platform, chat_id, mid)
                if not shard_id:
                    continue
                owner = await self._shard_owner(shard_id)
                # Legacy shards without owner stay inheritable.
                if owner and owner != str(user_id).strip():
                    return await self._new_shard(
                        platform,
                        chat_id,
                        user_id,
                        embedding,
                        topic=_topic_hint(text),
                        reason="reply_foreign_shard",
                    )
                shard = await self._load_or_touch_shard(platform, chat_id, user_id, shard_id, embedding)
                return RouteResult(
                    shard_id=shard_id,
                    session_key=shard_session_key(platform, chat_id, shard_id),
                    topic=str(shard.get("topic") or ""),
                    is_new=False,
                    reason="reply_chain",
                    centroid=list(shard.get("centroid") or embedding),
                    embedding=embedding,
                )

        active_id = await self.hot.get_active_shard(platform, chat_id, user_id)
        if not active_id and hasattr(self.db, "get_user_active_shard"):
            active_id = await self.db.get_user_active_shard(platform, chat_id, user_id)

        if active_id:
            shard = await self._load_or_touch_shard(platform, chat_id, user_id, active_id, embedding)
            centroid = list(shard.get("centroid") or [])
            score = cosine_similarity(embedding, centroid) if centroid else 1.0
            # C. topic drift → new shard; else continue user line (B)
            if centroid and score < TOPIC_DRIFT_THRESHOLD and trigger.explicit:
                return await self._new_shard(
                    platform, chat_id, user_id, embedding, topic=_topic_hint(text), reason="topic_drift"
                )
            return RouteResult(
                shard_id=active_id,
                session_key=shard_session_key(platform, chat_id, active_id),
                topic=str(shard.get("topic") or ""),
                is_new=False,
                reason="user_active",
                centroid=blend_centroid(centroid, embedding),
                embedding=embedding,
            )

        # New explicit / chime open
        return await self._new_shard(
            platform,
            chat_id,
            user_id,
            embedding,
            topic=_topic_hint(text),
            reason="new_user_topic",
        )

    async def _new_shard(
        self,
        platform: str,
        chat_id: str,
        user_id: str,
        embedding: list[float],
        *,
        topic: str,
        reason: str,
    ) -> RouteResult:
        shard_id = new_shard_id()
        if hasattr(self.db, "upsert_group_shard"):
            await self.db.upsert_group_shard(
                shard_id=shard_id,
                platform=platform,
                chat_id=chat_id,
                user_id=user_id,
                topic=topic,
                centroid=embedding,
            )
        await self.hot.set_active_shard(platform, chat_id, user_id, shard_id)
        return RouteResult(
            shard_id=shard_id,
            session_key=shard_session_key(platform, chat_id, shard_id),
            topic=topic,
            is_new=True,
            reason=reason,
            centroid=list(embedding),
            embedding=embedding,
        )

    async def _shard_owner(self, shard_id: str) -> str:
        if hasattr(self.db, "get_group_shard"):
            loaded = await self.db.get_group_shard(shard_id)
            if loaded:
                return str(loaded.get("user_id") or "").strip()
        return ""

    async def _load_or_touch_shard(
        self,
        platform: str,
        chat_id: str,
        user_id: str,
        shard_id: str,
        embedding: list[float],
    ) -> dict[str, Any]:
        shard: dict[str, Any] = {"shard_id": shard_id, "topic": "", "centroid": []}
        if hasattr(self.db, "get_group_shard"):
            loaded = await self.db.get_group_shard(shard_id)
            if loaded:
                shard = loaded
        centroid = blend_centroid(list(shard.get("centroid") or []), embedding)
        shard["centroid"] = centroid
        if hasattr(self.db, "upsert_group_shard"):
            await self.db.upsert_group_shard(
                shard_id=shard_id,
                platform=platform,
                chat_id=chat_id,
                user_id=str(shard.get("user_id") or user_id),
                topic=str(shard.get("topic") or ""),
                centroid=centroid,
            )
        await self.hot.set_active_shard(platform, chat_id, user_id, shard_id)
        return shard


def _topic_hint(text: str) -> str:
    raw = " ".join((text or "").split())
    return raw[:40]
