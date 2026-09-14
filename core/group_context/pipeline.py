from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.database import utc_now
from core.group_context.assembler import AssembledContext, assemble_group_prompt
from core.group_context.compress import maybe_compress_shard, needs_compression
from core.group_context.keys import hash_embed, turn_payload
from core.group_context.profile import (
    EXTRACT_TURN_LIMIT,
    card_from_row,
    extract_profile_card,
    merge_profile,
    should_refresh,
)
from core.group_context.router import ContextRouter, RouteResult
from core.group_context.store import HotStore
from core.group_context.trigger import TriggerResult, decide_trigger, extract_trigger_flags, score_against_centroid


@dataclass
class PipelineResult:
    trigger: TriggerResult
    route: RouteResult | None = None
    assembled: AssembledContext | None = None
    event_id: int | None = None
    compressed: bool = False


class GroupContextPipeline:
    def __init__(self, hot: HotStore, db: Any, embed_fn=hash_embed) -> None:
        self.hot = hot
        self.db = db
        self.embed = embed_fn
        self.router = ContextRouter(hot, db, embed_fn=embed_fn)

    async def ingest_background(
        self,
        *,
        platform: str,
        chat_id: str,
        user_id: str,
        sender_name: str,
        role: str,
        content: str,
        message_id: str = "",
        media: list[dict[str, Any]] | None = None,
        shard_id: str = "",
        at_user_ids: list[str] | None = None,
    ) -> int | None:
        """Always write group shared memory (layer D). Returns event id when available."""
        embedding = self.embed(content or "") if content else []
        event = {
            "platform": platform,
            "chat_id": chat_id,
            "user_id": user_id,
            "sender_name": sender_name,
            "role": role,
            "content": content,
            "message_id": message_id,
            "shard_id": shard_id,
            "media": list(media or []),
            "created_at": utc_now(),
        }
        await self.hot.append_group_mem(platform=platform, chat_id=chat_id, event=event)
        await self.hot.push_entity(
            platform,
            chat_id,
            user_id=user_id,
            name=sender_name,
            mentioned_ids=at_user_ids,
        )
        event_id = None
        if hasattr(self.db, "append_group_event"):
            result = await self.db.append_group_event(
                platform=platform,
                chat_id=chat_id,
                user_id=user_id,
                sender_name=sender_name,
                role=role,
                content=content,
                media=media,
                message_id=message_id,
                shard_id=shard_id,
                embedding=embedding or None,
            )
            if isinstance(result, int):
                event_id = result
        if message_id and shard_id:
            await self.hot.bind_message_shard(platform, chat_id, message_id, shard_id)
        return event_id

    async def decide(
        self,
        *,
        channel_type: str,
        role: str,
        group_require_at: bool,
        text: str,
        at_user_ids: list[str],
        bot_id: str,
        wake_keywords: list[str] | None,
        persona_name: str,
        has_media: bool,
        engaged: bool,
        same_speaker: bool,
        can_open: bool,
        replies_left: int,
        reply_to_ids: list[str] | None,
        platform: str,
        chat_id: str,
        user_id: str,
        replied_bot_hint: bool = False,
    ) -> TriggerResult:
        mentioned, named, command = extract_trigger_flags(
            text=text,
            at_user_ids=at_user_ids,
            bot_id=bot_id,
            wake_keywords=wake_keywords,
            persona_name=persona_name,
        )
        replied_bot = bool(replied_bot_hint)
        for mid in reply_to_ids or []:
            if replied_bot:
                break
            shard = await self.hot.shard_for_message(platform, chat_id, str(mid))
            if shard:
                replied_bot = True
                break
            if hasattr(self.db, "is_bot_group_message"):
                if await self.db.is_bot_group_message(platform, chat_id, str(mid), bot_id):
                    replied_bot = True
                    break

        semantic = 0.0
        active = await self.hot.get_active_shard(platform, chat_id, user_id)
        if active and hasattr(self.db, "get_group_shard"):
            shard = await self.db.get_group_shard(active)
            if shard and shard.get("centroid"):
                semantic = score_against_centroid(text, list(shard["centroid"]), self.embed)

        return decide_trigger(
            channel_type=channel_type,
            role=role,
            group_require_at=group_require_at,
            text=text,
            mentioned=mentioned,
            named=named,
            has_media=has_media,
            engaged=engaged,
            same_speaker=same_speaker,
            can_open=can_open,
            replies_left=replies_left,
            replied_bot=replied_bot,
            command=command,
            semantic_score=semantic,
        )

    async def prepare_reply(
        self,
        *,
        platform: str,
        chat_id: str,
        user_id: str,
        sender_name: str,
        text: str,
        trigger: TriggerResult,
        reply_to_ids: list[str] | None,
        bot_id: str,
        bot_name: str,
        bare_wake: bool = False,
        chime: bool = False,
    ) -> PipelineResult:
        route = await self.router.route(
            platform=platform,
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            trigger=trigger,
            reply_to_ids=reply_to_ids,
            bot_id=bot_id,
        )
        if route is None:
            return PipelineResult(trigger=trigger)

        locked = await self.hot.with_shard_lock(route.shard_id)
        did_compress = False
        assembled = None
        try:
            assembled = await assemble_group_prompt(
                hot=self.hot,
                db=self.db,
                platform=platform,
                chat_id=chat_id,
                user_id=user_id,
                sender_name=sender_name,
                shard_id=route.shard_id,
                text=text,
                bot_name=bot_name,
                query_embedding=route.embedding,
                bare_wake=bare_wake,
                chime=chime,
            )
            did_compress = needs_compression(assembled.turns, bot_name=bot_name)
            summary = await maybe_compress_shard(
                hot=self.hot,
                db=self.db,
                shard_id=route.shard_id,
                turns=assembled.turns,
                bot_name=bot_name,
            )
            if summary:
                assembled.summary = summary
        finally:
            if locked:
                await self.hot.release_shard_lock(route.shard_id)

        return PipelineResult(
            trigger=trigger, route=route, assembled=assembled, compressed=did_compress
        )

    async def write_turn(
        self,
        *,
        platform: str,
        chat_id: str,
        user_id: str,
        shard_id: str,
        role: str,
        content: str,
        sender_name: str = "",
        message_id: str = "",
    ) -> None:
        turn = turn_payload(
            role=role,
            user_id=user_id,
            sender_name=sender_name,
            content=content,
            message_id=message_id,
        )
        await self.hot.append_turn(
            platform=platform,
            chat_id=chat_id,
            user_id=user_id,
            shard_id=shard_id,
            turn=turn,
        )
        if hasattr(self.db, "append_shard_turn"):
            await self.db.append_shard_turn(shard_id=shard_id, turn=turn)

    async def maybe_refresh_profile(
        self,
        *,
        platform: str,
        chat_id: str,
        user_id: str,
        sender_name: str,
        shard_id: str,
        bot_name: str = "小Lu",
        compressed: bool = False,
        mock: bool = False,
        llm: Any = None,
    ) -> Any:
        if mock or llm is None:
            return None
        if not hasattr(self.db, "get_user_profile") or not hasattr(self.db, "put_user_profile"):
            return None
        turns = await self.hot.load_turns(
            platform=platform,
            chat_id=chat_id,
            user_id=user_id,
            shard_id=shard_id,
            limit=EXTRACT_TURN_LIMIT,
        )
        if not turns and hasattr(self.db, "load_shard_turns"):
            turns = await self.db.load_shard_turns(shard_id, limit=EXTRACT_TURN_LIMIT)
        row = await self.db.get_user_profile(platform, user_id, chat_id)
        old = card_from_row(row)
        if not should_refresh(old, turns, compressed=compressed):
            return None
        extracted = await extract_profile_card(
            turns, sender_name, llm=llm, bot_name=bot_name
        )
        if extracted is None:
            return None
        merged = merge_profile(old, extracted)
        await self.db.put_user_profile(
            platform=platform,
            chat_id=chat_id,
            user_id=user_id,
            display_name=sender_name or merged.address,
            card=merged.to_dict(),
        )
        return merged
