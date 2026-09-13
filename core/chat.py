from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from core.agent_runtime import begin_shell_turn, end_shell_turn
from core.bubbles import split_reply_bubbles
from core.graph import GRAPH_RECURSION_LIMIT, INVOKE_TIMEOUT_SEC
from core.group_talk import (
    GROUP_CONTEXT_LIMIT,
    decide_group_reply,
    format_group_context,
    is_bare_wake,
    is_silence_reply,
    mentioned_name,
    text_asks_about_image,
)
from core.group_context.pipeline import GroupContextPipeline
from core.identity import (
    IdentityStore,
    compose_system_prompt,
    mentioned_bot,
    reset_current_role,
    set_current_role,
)
from core.media import MAX_MEDIA_ITEMS, MINI_PNG, FetchedMedia, build_user_content
from core.memory import MemoryService
from interface.llm.factory import get_files_client
from interface.platform.napcat import (
    NapcatAdapter,
    dump_media_refs,
    extract_at_user_ids,
    extract_reply_ids,
    media_from_event_text,
    media_from_fetched_msg,
    media_from_payload,
    strip_reply_marks,
    summarize_fetched_msg,
    take_media,
)
from interface.platform.registry import AdapterRegistry
from msg.schema import ChatResponse, InboundMessage, MediaRef, OutboundMessage
from utils.config import AppConfig
from utils.log import ChatbotLogger


@dataclass
class GroupThread:
    engaged_until: float = 0.0
    last_user_id: str = ""
    replies_left: int = 0


def last_ai_text(result: dict[str, Any]) -> str:
    messages = result.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, AIMessage) or getattr(message, "type", "") == "ai":
            content = message.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text") or ""))
                    elif isinstance(block, str):
                        parts.append(block)
                text = "".join(parts).strip()
                if text:
                    return text
    return ""


class ChatOrchestrator:
    def __init__(
        self,
        config: AppConfig,
        logger: ChatbotLogger,
        memory: MemoryService,
        owner_graph: Any,
        user_graph: Any,
        adapters: AdapterRegistry,
        get_config: Any,
        identity: IdentityStore,
        group_pipeline: GroupContextPipeline | None = None,
    ) -> None:
        self.config = config
        self.logger = logger
        self.memory = memory
        self.owner_graph = owner_graph
        self.user_graph = user_graph
        self.graph = owner_graph
        self.adapters = adapters
        self.get_config = get_config
        self.identity = identity
        self.group_pipeline = group_pipeline
        self._last_group_reply: dict[str, float] = {}
        self._group_threads: dict[str, GroupThread] = {}

    def attach(
        self,
        owner_graph: Any,
        user_graph: Any,
        adapters: AdapterRegistry,
        config: AppConfig,
        identity: IdentityStore | None = None,
        group_pipeline: GroupContextPipeline | None = None,
    ) -> None:
        self.owner_graph = owner_graph
        self.user_graph = user_graph
        self.graph = owner_graph
        self.adapters = adapters
        self.config = config
        if identity is not None:
            self.identity = identity
        if group_pipeline is not None:
            self.group_pipeline = group_pipeline

    def _graph_for(self, role: str) -> Any:
        return self.owner_graph if role == "owner" else self.user_graph

    async def _park_unreplied(
        self,
        *,
        session_id: str,
        inbound: InboundMessage,
        text: str,
    ) -> None:
        cfg = self.get_config()
        body = (text or "").strip() or inbound.text
        await self.memory.db.ensure_session(
            session_id=session_id,
            user_id=inbound.user_id,
            platform=inbound.platform,
            channel_type=inbound.channel_type,
        )
        await self.memory.db.save_message(session_id, "user", body, provider=cfg.llm.provider)

    async def _drop_silence_ai(self, graph: Any, session_id: str, result: dict[str, Any] | None) -> None:
        messages = list((result or {}).get("messages") or [])
        for message in reversed(messages):
            if not (isinstance(message, AIMessage) or getattr(message, "type", "") == "ai"):
                continue
            message_id = getattr(message, "id", None)
            if not message_id:
                return
            try:
                await graph.aupdate_state(
                    {"configurable": {"thread_id": session_id}},
                    {"messages": [RemoveMessage(id=message_id)]},
                    as_node="agent",
                )
            except Exception as exc:
                self.logger.warning(f"drop silence skip session={session_id}: {exc}")
            return

    def _reply_ids(self, inbound: InboundMessage) -> list[str]:
        ids = list(inbound.reply_to_ids)
        ids.extend(extract_reply_ids(inbound.text))
        if inbound.raw:
            ids.extend(extract_reply_ids(inbound.raw.get("message")))
            ids.extend(extract_reply_ids(inbound.raw.get("raw_message")))
        seen: set[str] = set()
        out: list[str] = []
        for item in ids:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out[:2]

    async def _quoted_context(self, inbound: InboundMessage) -> tuple[str, list[MediaRef]]:
        ids = self._reply_ids(inbound)
        if not ids:
            return "", []
        napcat = self.adapters.get("napcat")
        if not isinstance(napcat, NapcatAdapter) or not napcat.can_fetch():
            return "", []
        lines: list[str] = []
        media: list[MediaRef] = []
        for rid in ids:
            data = await napcat.fetch_message(rid)
            summary = summarize_fetched_msg(data)
            media.extend(media_from_fetched_msg(data))
            if not summary:
                self.logger.warning(f"quote fetch empty id={rid}")
                continue
            lines.append(summary)
            self.logger.info(f"quote fetch ok id={rid}")
        quoted_media = take_media(media, limit=MAX_MEDIA_ITEMS)
        if not lines:
            return "", quoted_media
        return "对方引用了这条消息（被回复的原话，不是当前这句）：\n" + "\n".join(lines), quoted_media

    def _timeline_media(self, events: list[dict[str, Any]]) -> list[MediaRef]:
        items: list[MediaRef] = []
        for event in reversed(events or []):
            if str(event.get("role") or "") == "assistant":
                continue
            refs = media_from_payload(event.get("media"))
            if not refs:
                refs = media_from_event_text(str(event.get("content") or ""))
            items.extend(refs)
        return items

    async def _fetch_media_blobs(
        self,
        inbound: InboundMessage,
        items: list[MediaRef],
        *,
        mock: bool,
    ) -> list[FetchedMedia]:
        fetched: list[FetchedMedia] = []
        if not items:
            return fetched
        napcat = self.adapters.get("napcat")
        if isinstance(napcat, NapcatAdapter):
            if not napcat.can_fetch():
                self.logger.warning("media fetch skip: napcat bot_url empty")
            else:
                group_id = inbound.chat_id if inbound.channel_type == "group" else ""
                for item in items[:MAX_MEDIA_ITEMS]:
                    try:
                        blob = await napcat.fetch_media(item, group_id=group_id)
                    except Exception as exc:
                        self.logger.warning(f"media fetch skip: {exc}")
                        blob = None
                    if blob:
                        fetched.append(blob)
                        self.logger.info(
                            f"media ready file={item.file_id or item.name or '-'} bytes={len(blob.data)} "
                            f"uploadable={bool(blob.data)}"
                        )
                    else:
                        self.logger.warning(
                            f"media fetch empty file={item.file_id or item.name or '-'} url={item.url or '-'}"
                        )
        else:
            self.logger.warning("media fetch skip: napcat adapter missing")
        if items and not fetched and mock:
            for item in items[:MAX_MEDIA_ITEMS]:
                fetched.append(
                    FetchedMedia(
                        kind=item.kind,
                        name=item.name or "image.png",
                        data=MINI_PNG,
                        mime="image/png",
                    )
                )
        return fetched

    async def _is_reply_to_bot(self, inbound: InboundMessage, bot_id: str) -> bool:
        ids = [str(x).strip() for x in (inbound.reply_to_ids or []) if str(x).strip()]
        if not ids:
            return False
        pipeline = self.group_pipeline
        if pipeline is not None:
            for mid in ids:
                shard = await pipeline.hot.shard_for_message(inbound.platform, inbound.chat_id, mid)
                if shard:
                    return True
                if hasattr(self.memory.db, "is_bot_group_message"):
                    if await self.memory.db.is_bot_group_message(
                        inbound.platform, inbound.chat_id, mid, bot_id
                    ):
                        return True
        napcat = self.adapters.get("napcat")
        if isinstance(napcat, NapcatAdapter) and bot_id:
            for mid in ids[:2]:
                try:
                    data = await napcat.fetch_message(mid)
                except Exception:
                    data = None
                if not data:
                    continue
                sender = data.get("sender") if isinstance(data.get("sender"), dict) else {}
                uid = str(data.get("user_id") or sender.get("user_id") or "").strip()
                if uid and uid == str(bot_id).strip():
                    return True
        return False

    def _thread(self, chat_key: str) -> GroupThread:
        return self._group_threads.setdefault(chat_key, GroupThread())

    def _engagement(self, chat_key: str, inbound: InboundMessage, ident: Any) -> tuple[bool, bool, bool, int]:
        now = time.time()
        thread = self._thread(chat_key)
        engage_sec = max(0, int(getattr(ident, "group_engage_sec", 90) or 0))
        engaged = now < thread.engaged_until and thread.replies_left > 0
        same_speaker = engaged and bool(thread.last_user_id) and thread.last_user_id == inbound.user_id
        last_reply = self._last_group_reply.get(chat_key, 0.0)
        can_open = (now - last_reply) >= max(0, int(ident.group_chime_cooldown_sec or 0))
        return engaged, same_speaker, can_open, thread.replies_left

    def _note_group_sent(self, *, chat_key: str, user_id: str, ident: Any, opening: bool) -> None:
        now = time.time()
        extra = max(0, int(getattr(ident, "group_engage_replies", 2) or 0))
        engage_sec = max(0, int(getattr(ident, "group_engage_sec", 90) or 0))
        thread = self._thread(chat_key)
        thread.engaged_until = now + engage_sec
        thread.last_user_id = user_id
        thread.replies_left = extra if opening else max(0, thread.replies_left - 1)
        self._last_group_reply[chat_key] = now

    async def handle(self, inbound: InboundMessage, deliver: bool = True) -> ChatResponse:
        cfg = self.get_config()
        session_id = inbound.session_key
        role = self.identity.resolve_role(inbound.platform, inbound.user_id)
        ident = self.identity.settings
        chat_key = f"{inbound.platform}:{inbound.channel_type}:{inbound.chat_id}"
        bot_id = (cfg.napcat.bot_id or inbound.self_id or "").strip()
        if inbound.self_id and not (cfg.napcat.bot_id or "").strip():
            cfg.napcat.bot_id = inbound.self_id.strip()
            bot_id = cfg.napcat.bot_id
        at_ids = list(inbound.at_user_ids)
        at_ids.extend(extract_at_user_ids(inbound.text))
        if inbound.raw:
            at_ids.extend(extract_at_user_ids(inbound.raw.get("message")))
            at_ids.extend(extract_at_user_ids(inbound.raw.get("raw_message")))
        mentioned = mentioned_bot(at_ids, bot_id)
        named = mentioned_name(inbound.text, ident.wake_keywords, cfg.persona.name)
        has_media = bool(inbound.media)
        engaged, same_speaker, can_open, replies_left = self._engagement(chat_key, inbound, ident)

        pipeline = self.group_pipeline
        route = None
        assembled = None
        mode = "direct"
        if inbound.channel_type == "group" and pipeline is not None:
            replied_bot = await self._is_reply_to_bot(inbound, bot_id)
            trigger = await pipeline.decide(
                channel_type=inbound.channel_type,
                role=role,
                group_require_at=ident.group_require_at,
                text=inbound.text,
                at_user_ids=at_ids,
                bot_id=bot_id,
                wake_keywords=ident.wake_keywords,
                persona_name=cfg.persona.name,
                has_media=has_media,
                engaged=engaged,
                same_speaker=same_speaker,
                can_open=can_open,
                replies_left=replies_left,
                reply_to_ids=inbound.reply_to_ids,
                platform=inbound.platform,
                chat_id=inbound.chat_id,
                user_id=inbound.user_id,
                replied_bot_hint=replied_bot,
            )
            mode = trigger.mode
            await pipeline.ingest_background(
                platform=inbound.platform,
                chat_id=inbound.chat_id,
                user_id=inbound.user_id,
                sender_name=inbound.sender_name or "",
                role="user",
                content=inbound.text,
                message_id=inbound.message_id or "",
                media=dump_media_refs(inbound.media),
                at_user_ids=at_ids,
            )
            if mode == "ignore":
                await self._park_unreplied(
                    session_id=session_id,
                    inbound=inbound,
                    text=inbound.text,
                )
                self.logger.info(
                    f"chat ignored session={session_id} platform={inbound.platform} user={inbound.user_id} "
                    f"mentioned={mentioned} named={named} bot_id={bot_id or '-'} at={at_ids or []} "
                    f"reason={trigger.reason} parked=1"
                )
                return ChatResponse(session_id=session_id, reply="", ignored=True, role=role)
            prepared = await pipeline.prepare_reply(
                platform=inbound.platform,
                chat_id=inbound.chat_id,
                user_id=inbound.user_id,
                sender_name=inbound.sender_name or "",
                text=inbound.text,
                trigger=trigger,
                reply_to_ids=inbound.reply_to_ids,
                bot_id=bot_id,
                bot_name=cfg.persona.name,
                bare_wake=is_bare_wake(
                    inbound.text,
                    keywords=ident.wake_keywords,
                    persona_name=cfg.persona.name,
                    explicit=trigger.explicit,
                ),
            )
            route = prepared.route
            assembled = prepared.assembled
            if route is not None:
                session_id = route.session_key
        else:
            mode = decide_group_reply(
                channel_type=inbound.channel_type,
                role=role,
                group_require_at=ident.group_require_at,
                text=inbound.text,
                mentioned=mentioned,
                named=named,
                has_media=has_media,
                engaged=engaged,
                same_speaker=same_speaker,
                can_open=can_open,
                replies_left=replies_left,
            )
            if inbound.channel_type == "group":
                await self.memory.db.append_group_event(
                    platform=inbound.platform,
                    chat_id=inbound.chat_id,
                    user_id=inbound.user_id,
                    sender_name=inbound.sender_name or "",
                    role="user",
                    content=inbound.text,
                    media=dump_media_refs(inbound.media),
                    message_id=inbound.message_id or "",
                )
            if mode == "ignore":
                if inbound.channel_type == "group":
                    await self._park_unreplied(
                        session_id=session_id,
                        inbound=inbound,
                        text=inbound.text,
                    )
                self.logger.info(
                    f"chat ignored session={session_id} platform={inbound.platform} user={inbound.user_id} "
                    f"mentioned={mentioned} named={named} bot_id={bot_id or '-'} at={at_ids or []} parked=1"
                )
                return ChatResponse(session_id=session_id, reply="", ignored=True, role=role)

        chime_in = mode == "chime"

        await self.memory.db.ensure_session(
            session_id=session_id,
            user_id=inbound.user_id,
            platform=inbound.platform,
            channel_type=inbound.channel_type,
        )
        recalled: list[str] = []
        if role == "owner":
            recalled = await self.memory.recall(inbound.user_id, inbound.text)
        memory_block = self.memory.format_for_prompt(recalled)
        qq_context = ""
        if inbound.platform == "napcat" and role == "owner":
            qq_context = (
                "Current QQ context: "
                f"channel={inbound.channel_type} chat_id={inbound.chat_id} "
                f"user_id={inbound.user_id} message_id={inbound.message_id or ''} "
                f"sender={inbound.sender_name or ''}."
                " Use qq_* tools when the user asks to query or moderate QQ."
            )
        events: list[dict[str, Any]] = []
        group_context = ""
        if inbound.channel_type == "group":
            if assembled is not None:
                group_context = assembled.as_group_context_block()
            else:
                events = await self.memory.db.load_group_recent(
                    inbound.platform, inbound.chat_id, limit=GROUP_CONTEXT_LIMIT
                )
                group_context = format_group_context(
                    events,
                    current_user_id=inbound.user_id,
                    current_name=inbound.sender_name or "",
                    bot_name=cfg.persona.name,
                )
            if not events:
                events = await self.memory.db.load_group_recent(
                    inbound.platform, inbound.chat_id, limit=GROUP_CONTEXT_LIMIT
                )

        quoted, quoted_media = await self._quoted_context(inbound)
        current_media = list(inbound.media)
        timeline_media: list[MediaRef] = []
        if not current_media and not quoted_media and text_asks_about_image(
            strip_reply_marks(inbound.text) or inbound.text
        ):
            timeline_media = self._timeline_media(events)
        to_fetch = take_media(
            current_media,
            quoted_media,
            timeline_media,
            limit=MAX_MEDIA_ITEMS,
        )
        fetched = await self._fetch_media_blobs(inbound, to_fetch, mock=cfg.is_mock_llm())
        persona_block = compose_system_prompt(
            cfg.persona,
            role,
            in_group=inbound.channel_type == "group",
            owner_nickname=self.identity.owner_nickname(inbound.platform, inbound.user_id),
            chime_in=chime_in,
            engaged=engaged and chime_in,
            allowed_commands=cfg.agent.command_allowlist,
            has_media=bool(fetched),
            bare_wake=bool(assembled is not None and assembled.bare_wake),
        )
        files_client = get_files_client(cfg.llm.provider, cfg.llm.base_url, cfg.llm.api_key)
        user_text = inbound.text
        if assembled is not None and assembled.resolved_text:
            user_text = assembled.resolved_text
        if quoted:
            current = strip_reply_marks(user_text) or user_text
            user_text = f"{quoted}\n当前这句：{current}"
        inbound_keys = {(item.kind, item.file_id, item.url, item.name) for item in inbound.media}
        quoted_keys = {(item.kind, item.file_id, item.url, item.name) for item in quoted_media}
        timeline_only = [
            item
            for item in to_fetch
            if (item.kind, item.file_id, item.url, item.name) not in inbound_keys
            and (item.kind, item.file_id, item.url, item.name) not in quoted_keys
        ]
        if timeline_only and fetched:
            user_text = f"群里刚才有人发过图，下面附上近期的图，先看再回。\n{user_text}".strip()
        user_content, media_notes = await build_user_content(user_text, fetched, files_client)
        if has_media and not fetched:
            miss = "这张图我这边没下下来，重新发一张或者贴文字吧。"
            media_notes = f"{media_notes}\n{miss}".strip() if media_notes else miss
            if isinstance(user_content, list):
                user_content = list(user_content) + [{"type": "text", "text": miss}]
            else:
                user_content = f"{user_content}\n{miss}".strip()
        stored_user = user_text
        if media_notes and media_notes not in stored_user:
            stored_user = f"{stored_user}\n{media_notes}".strip()
        await self.memory.db.save_message(session_id, "user", stored_user, provider=cfg.llm.provider)
        if pipeline is not None and route is not None and inbound.channel_type == "group":
            await pipeline.write_turn(
                platform=inbound.platform,
                chat_id=inbound.chat_id,
                user_id=inbound.user_id,
                shard_id=route.shard_id,
                role="user",
                content=stored_user,
                sender_name=inbound.sender_name or "",
                message_id=inbound.message_id or "",
            )
        token = set_current_role(role)
        t0 = time.perf_counter()
        result: dict[str, Any] | None = None
        graph = self._graph_for(role)
        begin_shell_turn()
        try:
            result = await asyncio.wait_for(
                graph.ainvoke(
                    {"messages": [HumanMessage(content=user_content)]},
                    config={
                        "configurable": {
                            "thread_id": session_id,
                            "persona": persona_block,
                            "memories": memory_block,
                            "qq_context": qq_context,
                            "group_context": group_context,
                            "platform": inbound.platform,
                            "chat_id": inbound.chat_id,
                            "user_id": inbound.user_id,
                            "channel_type": inbound.channel_type,
                            "role": role,
                        },
                        "recursion_limit": GRAPH_RECURSION_LIMIT,
                    },
                ),
                timeout=INVOKE_TIMEOUT_SEC,
            )
        except Exception as exc:
            self.logger.error(f"llm call failed session={session_id}: {exc}")
            result = {
                "messages": [
                    AIMessage(
                        content="这张图我这边没看成。再发一次，或者贴文字也行。"
                        if has_media
                        else "刚才卡住了，再说一次。"
                    )
                ]
            }
        finally:
            end_shell_turn()
            reset_current_role(token)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        reply = last_ai_text(result or {})
        if chime_in and is_silence_reply(reply):
            await self._drop_silence_ai(graph, session_id, result)
            self.logger.info(
                f"chat silent session={session_id} role={role} platform={inbound.platform} latency_ms={latency_ms}"
            )
            return ChatResponse(session_id=session_id, reply="", ignored=True, role=role)
        reply = reply or "(empty reply)"
        bubbles = split_reply_bubbles(reply, max_bubbles=3 if chime_in else 4)
        stored_reply = "\n".join(bubbles) if bubbles else reply
        await self.memory.db.save_message(
            session_id,
            "assistant",
            stored_reply,
            provider=cfg.llm.provider,
            latency_ms=latency_ms,
        )
        if inbound.channel_type == "group":
            shard_id = route.shard_id if route is not None else ""
            # Send first so we can bind real outbound message_ids to the shard.
            outbound_ids: list[str] = []
            if deliver:
                stagger = inbound.platform == "napcat"
                for index, bubble in enumerate(bubbles or [stored_reply]):
                    if stagger and index > 0:
                        await asyncio.sleep(0.28 + min(len(bubble), 40) * 0.012 + random.random() * 0.35)
                    mid = await self.adapters.send(
                        OutboundMessage(
                            platform=inbound.platform,
                            channel_type=inbound.channel_type,
                            chat_id=inbound.chat_id,
                            text=bubble,
                            session_key=session_id,
                            user_id=inbound.user_id,
                        )
                    )
                    if mid:
                        outbound_ids.append(str(mid))
            primary_mid = outbound_ids[0] if outbound_ids else ""
            if pipeline is not None:
                await pipeline.ingest_background(
                    platform=inbound.platform,
                    chat_id=inbound.chat_id,
                    user_id=cfg.napcat.bot_id or "",
                    sender_name=cfg.persona.name,
                    role="assistant",
                    content=stored_reply,
                    message_id=primary_mid,
                    shard_id=shard_id,
                )
                if route is not None:
                    await pipeline.write_turn(
                        platform=inbound.platform,
                        chat_id=inbound.chat_id,
                        user_id=inbound.user_id,
                        shard_id=route.shard_id,
                        role="assistant",
                        content=stored_reply,
                        sender_name=cfg.persona.name,
                        message_id=primary_mid,
                    )
                    for mid in outbound_ids:
                        await pipeline.hot.bind_message_shard(
                            inbound.platform, inbound.chat_id, mid, route.shard_id
                        )
            else:
                await self.memory.db.append_group_event(
                    platform=inbound.platform,
                    chat_id=inbound.chat_id,
                    user_id=cfg.napcat.bot_id or "",
                    sender_name=cfg.persona.name,
                    role="assistant",
                    content=stored_reply,
                    message_id=primary_mid,
                    shard_id=shard_id,
                )
            opening = mode == "direct" or not engaged
            self._note_group_sent(
                chat_key=chat_key,
                user_id=inbound.user_id,
                ident=ident,
                opening=opening,
            )
        elif deliver:
            for bubble in bubbles or [stored_reply]:
                await self.adapters.send(
                    OutboundMessage(
                        platform=inbound.platform,
                        channel_type=inbound.channel_type,
                        chat_id=inbound.chat_id,
                        text=bubble,
                        session_key=session_id,
                        user_id=inbound.user_id,
                    )
                )
        self.logger.info(
            f"chat session={session_id} role={role} mode={mode} bubbles={len(bubbles)} "
            f"platform={inbound.platform} latency_ms={latency_ms}"
            + (f" shard={route.shard_id}" if route is not None else "")
        )
        return ChatResponse(session_id=session_id, reply=stored_reply, ignored=False, role=role)
