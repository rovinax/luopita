from __future__ import annotations

import asyncio
import inspect
import random
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from core.agent_runtime import begin_shell_turn, end_shell_turn
from core.bubbles import split_reply_bubbles
from core.outbound_sanitize import sanitize_outbound_text
from core.commands import (
    parse_slash,
    render_allow,
    render_cleared,
    render_cron_added,
    render_cron_error,
    render_cron_job,
    render_cron_list,
    render_cron_missing,
    render_cron_ran,
    render_cron_removed,
    render_cron_usage,
    render_help,
    render_model,
    render_ping,
    render_status,
    render_time,
    render_unknown,
    render_whoami,
)
from core.commitments.service import CommitmentService
from core.commitments.types import Commitment
from core.cron.context import CronDelivery, reset_cron_delivery, set_cron_delivery
from core.cron.parse import parse_when, split_cron_args
from core.cron.types import CronJob
from core.reflect import maybe_reflect_turn
from core.scratchpad import (
    format_scratchpad_block,
    load_scratchpad,
    mark_proactive,
    touch_scratchpad_after_turn,
)
from core.group_context.keys import shard_id_from_session_key, shard_session_key
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
from core.voice_examples import format_examples_block, infer_mode, infer_scene, select_examples
from core.group_context.pipeline import GroupContextPipeline
from core.identity import (
    IdentityStore,
    compose_system_prompt,
    default_session_key,
    mentioned_bot,
    reset_current_role,
    set_current_role,
)
from core.media import MAX_MEDIA_ITEMS, MINI_PNG, FetchedMedia, build_user_content
from core.memory import MemoryService
from interface.llm.factory import get_chat_model, get_files_client
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
    # Prefer final assistant text without pending tool_calls; fall back to any AI text.
    candidates: list[Any] = []
    for message in reversed(messages):
        if isinstance(message, AIMessage) or getattr(message, "type", "") == "ai":
            candidates.append(message)
    ordered = [m for m in candidates if not getattr(m, "tool_calls", None)] + [
        m for m in candidates if getattr(m, "tool_calls", None)
    ]
    for message in ordered:
        content = message.content
        text = ""
        if isinstance(content, str) and content.strip():
            text = content.strip()
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif isinstance(block, str):
                    parts.append(block)
            text = "".join(parts).strip()
        cleaned = sanitize_outbound_text(text)
        if cleaned:
            return cleaned
        # Markup-only / empty after sanitize: keep scanning earlier AI messages.
        continue
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
        cron: Any = None,
        cron_graph: Any = None,
        commitments: CommitmentService | None = None,
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
        self.cron = cron
        self.cron_graph = cron_graph
        self.commitments = commitments
        self._last_group_reply: dict[str, float] = {}
        self._group_threads: dict[str, GroupThread] = {}
        self._bg_tasks: set[asyncio.Task] = set()
        self._heartbeat_running: set[str] = set()

    def attach(
        self,
        owner_graph: Any,
        user_graph: Any,
        adapters: AdapterRegistry,
        config: AppConfig,
        identity: IdentityStore | None = None,
        group_pipeline: GroupContextPipeline | None = None,
        cron_graph: Any = None,
        cron: Any = None,
        commitments: CommitmentService | None = None,
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
        if cron_graph is not None:
            self.cron_graph = cron_graph
        if cron is not None:
            self.cron = cron
        if commitments is not None:
            self.commitments = commitments

    def _graph_for(self, role: str) -> Any:
        return self.owner_graph if role == "owner" else self.user_graph

    async def _try_owner_command(
        self, inbound: InboundMessage, role: str, *, deliver: bool = True
    ) -> ChatResponse | None:
        if role != "owner":
            return None
        cfg = self.get_config()
        ident = self.identity.settings
        parsed = parse_slash(
            inbound.text,
            keywords=ident.wake_keywords,
            persona_name=cfg.persona.name,
        )
        if parsed is None:
            return None
        name, args = parsed
        if name == "help":
            reply = render_help()
        elif name == "ping":
            reply = render_ping()
        elif name == "time":
            reply = render_time()
        elif name == "whoami":
            reply = render_whoami(
                nickname=self.identity.owner_nickname(inbound.platform, inbound.user_id),
                platform=inbound.platform,
                user_id=inbound.user_id,
                channel_type=inbound.channel_type,
                chat_id=inbound.chat_id,
                bot_id=(cfg.napcat.bot_id or inbound.self_id or "").strip(),
            )
        elif name == "model":
            reply = render_model(
                provider=cfg.llm.provider,
                model=cfg.llm.model,
                vision_model=cfg.llm.vision_model,
            )
        elif name == "allow":
            reply = render_allow(cfg.agent.command_allowlist)
        elif name == "status":
            reply = await self._command_status(inbound)
        elif name == "clear":
            await self._clear_owner_memory(inbound, role)
            reply = render_cleared()
        elif name == "cron":
            reply = await self._command_cron(inbound, args)
        else:
            reply = render_unknown(name)
        return await self._emit_command_reply(inbound, role, reply, deliver=deliver)

    async def _command_status(self, inbound: InboundMessage) -> str:
        cfg = self.get_config()
        db_ok = False
        redis_ok = False
        try:
            db_ok = await self.memory.db.ping()
        except Exception:
            db_ok = False
        pipeline = self.group_pipeline
        if pipeline is not None:
            try:
                redis_ok = await pipeline.hot.redis.ping()
            except Exception:
                redis_ok = False
        napcat = self.adapters.get("napcat")
        napcat_on = bool(napcat is not None and napcat.enabled())
        return render_status(
            provider=cfg.llm.provider,
            model=cfg.llm.model,
            database="memory" if cfg.is_memory_db() else "postgres",
            db_ok=db_ok,
            redis="memory" if cfg.is_memory_redis() else "redis",
            redis_ok=redis_ok,
            napcat_on=napcat_on,
            bot_id=(cfg.napcat.bot_id or inbound.self_id or "").strip(),
        )

    async def _command_cron(self, inbound: InboundMessage, args: str) -> str:
        cron = self.cron
        if cron is None:
            return "定时任务还没起来。"
        action, rest = split_cron_args(args)
        platform = inbound.platform
        user_id = inbound.user_id
        if action == "help":
            return render_cron_usage()
        if action == "list":
            jobs = await cron.list_jobs(user_id=user_id, platform=platform)
            return render_cron_list(jobs)
        if action == "add":
            found = parse_when(rest)
            if found is None:
                return render_cron_usage()
            spec, prompt = found
            if not prompt:
                return render_cron_error("还差任务内容。")
            try:
                job = await cron.add(
                    kind=spec.kind,
                    schedule=spec.schedule,
                    prompt=prompt,
                    name=spec.label,
                    platform=platform,
                    channel_type=inbound.channel_type,
                    chat_id=inbound.chat_id,
                    user_id=user_id,
                    delete_after_run=spec.delete_after_run,
                )
            except ValueError as exc:
                return render_cron_error(str(exc))
            return render_cron_added(job)
        job_id = rest.split(None, 1)[0] if rest.strip() else ""
        if action in {"get", "rm", "on", "off", "run"} and not job_id:
            return render_cron_usage()
        owned = await cron.owned(job_id, platform=platform, user_id=user_id) if job_id else None
        if owned is None:
            return render_cron_missing()
        if action == "get":
            return render_cron_job(owned)
        if action == "rm":
            await cron.remove(owned.id)
            return render_cron_removed(owned.id)
        if action == "on":
            job = await cron.set_enabled(owned.id, True)
            return render_cron_job(job or owned)
        if action == "off":
            job = await cron.set_enabled(owned.id, False)
            return render_cron_job(job or owned)
        if action == "run":
            await cron.run_now(owned.id)
            leftover = await cron.get(owned.id)
            return render_cron_ran(leftover, owned.id)
        return render_cron_usage()

    def _session_belongs_to_chat(self, session_id: str, inbound: InboundMessage) -> bool:
        sid = (session_id or "").strip()
        if not sid:
            return False
        if sid == inbound.session_key:
            return True
        if inbound.channel_type == "group":
            return sid.startswith(f"{inbound.platform}:group:{inbound.chat_id}:")
        if inbound.channel_type == "private":
            return sid == default_session_key(
                inbound.platform, inbound.channel_type, inbound.chat_id, inbound.user_id
            )
        return False

    async def _collect_clear_thread_ids(self, inbound: InboundMessage) -> list[str]:
        ids = [inbound.session_key]
        pipeline = self.group_pipeline
        if inbound.channel_type == "group" and pipeline is not None:
            active = await pipeline.hot.get_active_shard(
                inbound.platform, inbound.chat_id, inbound.user_id
            )
            if active:
                ids.append(shard_session_key(inbound.platform, inbound.chat_id, active))
        if hasattr(self.memory.db, "session_ids_for_user"):
            extra = await self.memory.db.session_ids_for_user(
                inbound.user_id, platform=inbound.platform
            )
            ids.extend(extra)
        seen: set[str] = set()
        out: list[str] = []
        for thread_id in ids:
            sid = (thread_id or "").strip()
            if not sid or sid in seen or not self._session_belongs_to_chat(sid, inbound):
                continue
            seen.add(sid)
            out.append(sid)
        return out

    async def _wipe_graph_thread(self, graph: Any, thread_id: str) -> None:
        saver = getattr(graph, "checkpointer", None)
        delete = getattr(saver, "adelete_thread", None) if saver is not None else None
        if callable(delete):
            try:
                result = delete(thread_id)
                if inspect.isawaitable(result):
                    await result
                self.logger.info(f"clear thread deleted id={thread_id}")
                return
            except Exception as exc:
                self.logger.warning(f"clear thread adelete skip id={thread_id}: {exc}")
        config = {"configurable": {"thread_id": thread_id}}
        try:
            snapshot = await graph.aget_state(config)
            messages = list((snapshot.values or {}).get("messages") or [])
            removals = [RemoveMessage(id=message.id) for message in messages if getattr(message, "id", None)]
            if removals:
                await graph.aupdate_state(config, {"messages": removals})
            self.logger.info(f"clear thread emptied id={thread_id} n={len(removals)}")
        except Exception as exc:
            self.logger.warning(f"clear thread skip id={thread_id}: {exc}")

    async def _clear_owner_memory(self, inbound: InboundMessage, _role: str) -> None:
        thread_ids = await self._collect_clear_thread_ids(inbound)
        shard_ids = [sid for tid in thread_ids if (sid := shard_id_from_session_key(tid))]
        pipeline = self.group_pipeline
        if pipeline is not None:
            active = await pipeline.hot.get_active_shard(
                inbound.platform, inbound.chat_id, inbound.user_id
            )
            if active:
                shard_ids.append(active)
            await pipeline.hot.clear_user_context(
                platform=inbound.platform,
                chat_id=inbound.chat_id,
                user_id=inbound.user_id,
                shard_ids=shard_ids,
            )
            if hasattr(self.memory.db, "clear_user_group_shards"):
                dropped = await self.memory.db.clear_user_group_shards(
                    inbound.platform, inbound.chat_id, inbound.user_id
                )
                for sid in dropped:
                    shard_ids.append(sid)
                    thread_ids.append(shard_session_key(inbound.platform, inbound.chat_id, sid))
        for thread_id in thread_ids:
            for graph in (self.owner_graph, self.user_graph):
                await self._wipe_graph_thread(graph, thread_id)
            if hasattr(self.memory.db, "clear_session_messages"):
                await self.memory.db.clear_session_messages(thread_id)
        await self.memory.forget_user(inbound.user_id)
        if hasattr(self.memory.db, "clear_user_profile"):
            await self.memory.db.clear_user_profile(
                inbound.platform, inbound.chat_id, inbound.user_id
            )
        if hasattr(self.memory.db, "put_scratchpad"):
            await self.memory.db.put_scratchpad(
                inbound.platform,
                inbound.user_id,
                {"focus": "", "open_summary": "", "reflect_notes": "", "last_proactive_at": ""},
            )
        if self.commitments is not None:
            for item in await self.commitments.list_open(
                platform=inbound.platform, user_id=inbound.user_id, limit=50
            ):
                await self.commitments.close(item.id, status="cancelled")
        chat_key = f"{inbound.platform}:{inbound.channel_type}:{inbound.chat_id}"
        self._group_threads.pop(chat_key, None)
        self._last_group_reply.pop(chat_key, None)
        self.logger.info(
            f"clear memory user={inbound.user_id} chat={inbound.chat_id} "
            f"threads={len(thread_ids)} shards={len(set(shard_ids))}"
        )

    async def _emit_command_reply(
        self, inbound: InboundMessage, role: str, reply: str, *, deliver: bool = True
    ) -> ChatResponse:
        cfg = self.get_config()
        session_id = inbound.session_key
        await self.memory.db.ensure_session(
            session_id=session_id,
            user_id=inbound.user_id,
            platform=inbound.platform,
            channel_type=inbound.channel_type,
        )
        await self.memory.db.save_message(session_id, "user", inbound.text, provider=cfg.llm.provider)
        await self.memory.db.save_message(session_id, "assistant", reply, provider="command")
        if deliver:
            await self.adapters.send(
                OutboundMessage(
                    platform=inbound.platform,
                    channel_type=inbound.channel_type,
                    chat_id=inbound.chat_id,
                    text=reply,
                    session_key=session_id,
                    user_id=inbound.user_id,
                )
            )
        self.logger.info(
            f"chat session={session_id} role={role} mode=command bubbles=1 "
            f"platform={inbound.platform} latency_ms=0"
        )
        return ChatResponse(session_id=session_id, reply=reply, ignored=False, role=role)

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

    def _spawn_profile_refresh(
        self,
        *,
        inbound: InboundMessage,
        shard_id: str,
        sender_name: str,
        compressed: bool,
        bot_name: str,
    ) -> None:
        cfg = self.get_config()
        pipeline = self.group_pipeline
        if pipeline is None or cfg.is_mock_llm() or not shard_id:
            return
        llm = get_chat_model(
            provider=cfg.llm.provider,
            base_url=cfg.llm.base_url,
            api_key=cfg.llm.api_key,
            model=cfg.llm.model,
            temperature=0,
        )
        task = asyncio.create_task(
            self._profile_refresh_job(
                platform=inbound.platform,
                chat_id=inbound.chat_id,
                user_id=inbound.user_id,
                sender_name=sender_name,
                shard_id=shard_id,
                bot_name=bot_name,
                compressed=compressed,
                llm=llm,
            )
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _profile_refresh_job(
        self,
        *,
        platform: str,
        chat_id: str,
        user_id: str,
        sender_name: str,
        shard_id: str,
        bot_name: str,
        compressed: bool,
        llm: Any,
    ) -> None:
        pipeline = self.group_pipeline
        if pipeline is None:
            return
        try:
            await pipeline.maybe_refresh_profile(
                platform=platform,
                chat_id=chat_id,
                user_id=user_id,
                sender_name=sender_name,
                shard_id=shard_id,
                bot_name=bot_name,
                compressed=compressed,
                mock=False,
                llm=llm,
            )
        except Exception as exc:
            self.logger.warning(f"profile refresh skip user={user_id} chat={chat_id}: {exc}")

    async def _after_owner_turn(
        self,
        *,
        inbound: InboundMessage,
        session_id: str,
        user_text: str,
    ) -> None:
        reflect_line = ""
        try:
            reflect_line = await maybe_reflect_turn(
                self.memory, user_id=inbound.user_id, user_text=user_text
            )
        except Exception as exc:
            self.logger.warning(f"reflect skip user={inbound.user_id}: {exc}")

        open_summary = ""
        if self.commitments is not None:
            try:
                changed = await self.commitments.ingest_turn(
                    user_text=user_text,
                    platform=inbound.platform,
                    channel_type=inbound.channel_type,
                    chat_id=inbound.chat_id,
                    user_id=inbound.user_id,
                    source_session=session_id,
                )
                if changed and self.cron is not None:
                    self.cron.wake()
                open_items = await self.commitments.list_open(
                    platform=inbound.platform, user_id=inbound.user_id, limit=5
                )
                open_summary = self.commitments.summary_for_scratchpad(open_items)
            except Exception as exc:
                self.logger.warning(f"commitment ingest skip user={inbound.user_id}: {exc}")

        try:
            existing = await load_scratchpad(
                self.memory.db, platform=inbound.platform, user_id=inbound.user_id
            )
            await touch_scratchpad_after_turn(
                self.memory.db,
                platform=inbound.platform,
                user_id=inbound.user_id,
                user_text=user_text,
                open_summary=open_summary or str(existing.get("open_summary") or ""),
                reflect_notes=reflect_line or str(existing.get("reflect_notes") or ""),
            )
        except Exception as exc:
            self.logger.warning(f"scratchpad skip user={inbound.user_id}: {exc}")

    async def run_commitment_followup(self, item: Commitment) -> str:
        # Always deliver to private chat with the owner to avoid noisy group pings.
        channel_type = "private"
        chat_id = item.user_id or item.chat_id
        job = CronJob(
            id=f"cmt-{item.id}",
            name=item.text[:24] or "commitment",
            kind="at",
            schedule="now",
            prompt=self.commitments.format_followup_prompt(item) if self.commitments else item.text,
            platform=item.platform,
            channel_type=channel_type,
            chat_id=chat_id,
            user_id=item.user_id,
            delete_after_run=True,
        )
        reply = await self.run_cron_turn(job)
        if self.commitments is not None:
            await self.commitments.mark_notified(item.id)
            await self.commitments.close(item.id, status="done")
            try:
                await mark_proactive(
                    self.memory.db, platform=item.platform, user_id=item.user_id
                )
            except Exception:
                pass
        return reply

    async def heartbeat_tick(self) -> None:
        if self.commitments is None:
            return
        due = await self.commitments.due_open(limit=10)
        for item in due:
            if not item.id or item.id in self._heartbeat_running:
                continue
            if not self.commitments.should_notify(item):
                continue
            if not self.commitments.allow_proactive(platform=item.platform, user_id=item.user_id):
                continue
            self._heartbeat_running.add(item.id)
            self.commitments.note_proactive(platform=item.platform, user_id=item.user_id)

            def _done(t: asyncio.Task, cid: str = item.id) -> None:
                self._bg_tasks.discard(t)
                self._heartbeat_running.discard(cid)

            task = asyncio.create_task(
                self._safe_commitment_followup(item),
                name=f"commitment-{item.id}",
            )
            self._bg_tasks.add(task)
            task.add_done_callback(_done)

    async def _safe_commitment_followup(self, item: Commitment) -> None:
        try:
            await self.run_commitment_followup(item)
            self.logger.info(f"commitment followup id={item.id} user={item.user_id}")
        except Exception as exc:
            self.logger.warning(f"commitment followup failed id={item.id}: {exc}")
            self._heartbeat_running.discard(item.id)

    async def run_cron_turn(self, job: CronJob) -> str:
        cfg = self.get_config()
        session_id = f"cron:{job.id}"
        graph = self.cron_graph or self.owner_graph
        persona_block = compose_system_prompt(
            cfg.persona,
            "owner",
            in_group=False,
            owner_nickname=self.identity.owner_nickname(job.platform, job.user_id),
            allowed_commands=cfg.agent.command_allowlist,
            cron_job=True,
        )
        qq_context = ""
        if job.platform == "napcat":
            qq_context = (
                "Current QQ context: "
                f"channel={job.channel_type} chat_id={job.chat_id} "
                f"user_id={job.user_id}."
                " Use qq_* tools when the task needs QQ."
            )
        await self.memory.db.ensure_session(
            session_id=session_id,
            user_id=job.user_id,
            platform=job.platform,
            channel_type=job.channel_type,
        )
        prompt = (job.prompt or "").strip() or "按定时任务做该做的事。"
        await self.memory.db.save_message(session_id, "user", prompt, provider="cron")
        token = set_current_role("owner")
        t0 = time.perf_counter()
        result: dict[str, Any] | None = None
        begin_shell_turn()
        try:
            result = await asyncio.wait_for(
                graph.ainvoke(
                    {"messages": [HumanMessage(content=prompt)]},
                    config={
                        "configurable": {
                            "thread_id": session_id,
                            "persona": persona_block,
                            "memories": "",
                            "qq_context": qq_context,
                            "group_context": "",
                            "platform": job.platform,
                            "chat_id": job.chat_id,
                            "user_id": job.user_id,
                            "channel_type": job.channel_type,
                            "role": "owner",
                        },
                        "recursion_limit": GRAPH_RECURSION_LIMIT,
                    },
                ),
                timeout=INVOKE_TIMEOUT_SEC,
            )
        except Exception as exc:
            self.logger.error(f"cron turn failed id={job.id}: {exc}")
            result = {"messages": [AIMessage(content="定时任务刚才卡住了。")]}
        finally:
            end_shell_turn()
            reset_current_role(token)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        reply = last_ai_text(result or {}) or "定时任务跑完了。"
        if is_silence_reply(reply):
            reply = "定时任务跑完了。"
        bubbles = split_reply_bubbles(reply, max_bubbles=4)
        stored_reply = "\n".join(bubbles) if bubbles else reply
        await self.memory.db.save_message(
            session_id,
            "assistant",
            stored_reply,
            provider=cfg.llm.provider,
            latency_ms=latency_ms,
        )
        stagger = job.platform == "napcat"
        outbound_ids: list[str] = []
        for index, bubble in enumerate(bubbles or [stored_reply]):
            if stagger and index > 0:
                await asyncio.sleep(0.28 + min(len(bubble), 40) * 0.012 + random.random() * 0.35)
            mid = await self.adapters.send(
                OutboundMessage(
                    platform=job.platform,  # type: ignore[arg-type]
                    channel_type=job.channel_type,  # type: ignore[arg-type]
                    chat_id=job.chat_id,
                    text=bubble,
                    session_key=session_id,
                    user_id=job.user_id,
                )
            )
            if mid:
                outbound_ids.append(str(mid))
        if job.channel_type == "group":
            await self.memory.db.append_group_event(
                platform=job.platform,
                chat_id=job.chat_id,
                user_id=cfg.napcat.bot_id or "",
                sender_name=cfg.persona.name,
                role="assistant",
                content=stored_reply,
                message_id=outbound_ids[0] if outbound_ids else "",
            )
        self.logger.info(
            f"cron fired id={job.id} session={session_id} "
            f"platform={job.platform} latency_ms={latency_ms} bubbles={len(bubbles)}"
        )
        return stored_reply

    async def handle(self, inbound: InboundMessage, deliver: bool = True) -> ChatResponse:
        cfg = self.get_config()
        session_id = inbound.session_key
        role = self.identity.resolve_role(inbound.platform, inbound.user_id)
        ident = self.identity.settings
        commanded = await self._try_owner_command(inbound, role, deliver=deliver)
        if commanded is not None:
            return commanded
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
        prepared = None
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
                tech_chance=ident.group_tech_chance,
                chatty_chance=ident.group_chatty_chance,
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
            bare_wake = is_bare_wake(
                inbound.text,
                keywords=ident.wake_keywords,
                persona_name=cfg.persona.name,
                explicit=trigger.explicit,
            )
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
                bare_wake=bare_wake,
                chime=trigger.mode == "chime",
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
                tech_chance=ident.group_tech_chance,
                chatty_chance=ident.group_chatty_chance,
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
        bare_wake = bool(assembled is not None and assembled.bare_wake)
        scratchpad_block = ""
        if role == "owner":
            pad = await load_scratchpad(
                self.memory.db, platform=inbound.platform, user_id=inbound.user_id
            )
            scratchpad_block = format_scratchpad_block(pad)
        examples_block = format_examples_block(
            select_examples(
                mode=infer_mode(chime_in=chime_in, bare_wake=bare_wake),
                relation="owner" if role == "owner" else "peer",
                scene=infer_scene(text=inbound.text, has_media=bool(fetched)),
                chime_in=chime_in,
                bare_wake=bare_wake,
            ),
            bot_name=cfg.persona.name,
        )
        persona_block = compose_system_prompt(
            cfg.persona,
            role,
            in_group=inbound.channel_type == "group",
            owner_nickname=self.identity.owner_nickname(inbound.platform, inbound.user_id),
            chime_in=chime_in,
            engaged=engaged and chime_in,
            allowed_commands=cfg.agent.command_allowlist,
            has_media=bool(fetched),
            bare_wake=bare_wake,
            examples=examples_block,
            scratchpad=scratchpad_block,
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
        delivery = None
        if role == "owner":
            delivery = set_cron_delivery(
                CronDelivery(
                    platform=inbound.platform,
                    channel_type=inbound.channel_type,
                    chat_id=inbound.chat_id,
                    user_id=inbound.user_id,
                )
            )
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
            if delivery is not None:
                reset_cron_delivery(delivery)
            reset_current_role(token)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        reply = last_ai_text(result or {})
        if chime_in and (not reply or is_silence_reply(reply)):
            await self._drop_silence_ai(graph, session_id, result)
            self.logger.info(
                f"chat silent session={session_id} role={role} platform={inbound.platform} latency_ms={latency_ms}"
            )
            return ChatResponse(session_id=session_id, reply="", ignored=True, role=role)
        reply = reply or "刚才卡住了，再说一次。"
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
            if pipeline is not None and route is not None:
                self._spawn_profile_refresh(
                    inbound=inbound,
                    shard_id=route.shard_id,
                    sender_name=inbound.sender_name or "",
                    compressed=bool(prepared.compressed) if prepared is not None else False,
                    bot_name=cfg.persona.name,
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
        if role == "owner" and stored_reply and not is_silence_reply(stored_reply):
            try:
                await self.memory.remember_turn(inbound.user_id, stored_user, stored_reply)
            except Exception as exc:
                self.logger.warning(f"remember_turn skip user={inbound.user_id}: {exc}")
            await self._after_owner_turn(
                inbound=inbound,
                session_id=session_id,
                user_text=stored_user,
            )
        self.logger.info(
            f"chat session={session_id} role={role} mode={mode} bubbles={len(bubbles)} "
            f"platform={inbound.platform} latency_ms={latency_ms}"
            + (f" shard={route.shard_id}" if route is not None else "")
        )
        return ChatResponse(session_id=session_id, reply=stored_reply, ignored=False, role=role)
