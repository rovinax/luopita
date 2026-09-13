from __future__ import annotations

import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="luopita-group-")
os.environ["LUOPITA_PROVIDER"] = "mock"
os.environ["LUOPITA_API_KEY"] = ""
os.environ["LUOPITA_DATABASE_URL"] = "memory://"
os.environ["LUOPITA_NAPCAT_ENABLED"] = "false"
os.environ["LUOPITA_IDENTITY_FILE"] = os.path.join(_tmp, "identity.yaml")
os.environ["LUOPITA_PERSON_FILE"] = os.path.join(_tmp, "person.yaml")

from app.runtime import Runtime
from core.database import InMemoryDatabase
from core.group_talk import format_group_context, is_bare_wake, is_silence_reply
from msg.schema import InboundMessage


class TestFormatGroupContext(unittest.TestCase):
    def test_labels_speakers_and_current_user(self):
        text = format_group_context(
            [
                {"user_id": "1", "sender_name": "张三", "role": "user", "content": "docker 起不来"},
                {"user_id": "2", "sender_name": "李四", "role": "user", "content": "你看过 logs 吗"},
                {"user_id": "bot", "sender_name": "小Lu", "role": "assistant", "content": "先 docker logs"},
            ],
            current_user_id="1",
            current_name="张三",
            bot_name="小Lu",
        )
        self.assertIn("张三: docker 起不来", text)
        self.assertIn("李四: 你看过 logs 吗", text)
        self.assertIn("小Lu: 先 docker logs", text)
        self.assertIn("当前对你说话的是张三", text)
        self.assertIn("只答ta现在这句", text)
        self.assertIn("不要把别人的话当成当前这个人说的", text)
        self.assertIn("禁止主动续答", text)
        self.assertIn("旁听背景", text)

    def test_includes_shanghai_hhmm_when_created_at_present(self):
        text = format_group_context(
            [
                {
                    "user_id": "1",
                    "sender_name": "张三",
                    "role": "user",
                    "content": "docker 起不来",
                    "created_at": "2026-09-12T06:32:00+00:00",
                },
                {
                    "user_id": "2",
                    "sender_name": "李四",
                    "role": "user",
                    "content": "你看过 logs 吗",
                    "created_at": "2026-09-12T06:33:00+00:00",
                },
            ],
            current_name="李四",
        )
        self.assertIn("张三 14:32: docker 起不来", text)
        self.assertIn("李四 14:33: 你看过 logs 吗", text)

    def test_trims_oldest_when_over_budget(self):
        events = [
            {"user_id": str(i), "sender_name": f"u{i}", "role": "user", "content": "x" * 80}
            for i in range(20)
        ]
        text = format_group_context(events, current_name="u19", max_chars=400)
        self.assertIn("u19:", text)
        self.assertNotIn("u0:", text)


class TestSilenceReply(unittest.TestCase):
    def test_markers(self):
        self.assertTrue(is_silence_reply("[SILENCE]"))
        self.assertTrue(is_silence_reply(" [silence]\n"))
        self.assertTrue(is_silence_reply(""))
        self.assertFalse(is_silence_reply("先看 logs"))


class TestBareWake(unittest.TestCase):
    def test_empty_after_wake(self):
        self.assertTrue(is_bare_wake("@小Lu", keywords=["小Lu"], persona_name="小Lu", explicit=True))
        self.assertTrue(is_bare_wake("[@1001] 小Lu", keywords=["小Lu"], persona_name="小Lu", explicit=True))
        self.assertTrue(is_bare_wake("小Lu 在吗", keywords=["小Lu"], persona_name="小Lu", explicit=True))

    def test_real_ask_not_bare(self):
        self.assertFalse(
            is_bare_wake("@小Lu docker 起不来", keywords=["小Lu"], persona_name="小Lu", explicit=True)
        )
        self.assertFalse(is_bare_wake("小Lu", keywords=["小Lu"], persona_name="小Lu", explicit=False))


class TestInMemoryGroupEvents(unittest.IsolatedAsyncioTestCase):
    async def test_append_and_load_recent(self):
        db = InMemoryDatabase()
        await db.append_group_event(
            platform="napcat", chat_id="9", user_id="1", sender_name="Ada", content="hi"
        )
        await db.append_group_event(
            platform="napcat", chat_id="9", user_id="2", sender_name="Bob", content="yo"
        )
        await db.append_group_event(
            platform="napcat", chat_id="8", user_id="1", sender_name="Ada", content="other room"
        )
        await db.append_group_event(
            platform="napcat",
            chat_id="9",
            user_id="3",
            sender_name="Cara",
            content="[图片:cat.png]",
            media=[{"kind": "image", "file_id": "cat.png", "url": "", "name": "cat.png", "mime": ""}],
        )
        rows = await db.load_group_recent("napcat", "9", limit=30)
        self.assertEqual([r["content"] for r in rows], ["hi", "yo", "[图片:cat.png]"])
        self.assertEqual(rows[-1]["media"][0]["file_id"], "cat.png")
        self.assertEqual(await db.list_sessions(), [])


class TestGroupTimelineChat(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = await Runtime.start()

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_ignored_is_parked_on_session(self):
        orch = self.runtime.orchestrator
        ignored = await orch.handle(
            InboundMessage(
                platform="napcat",
                channel_type="group",
                chat_id="55",
                user_id="1",
                text="hello everyone",
                sender_name="张三",
            ),
            deliver=False,
        )
        self.assertTrue(ignored.ignored)
        events = await self.runtime.db.load_group_recent("napcat", "55")
        self.assertEqual([e["content"] for e in events], ["hello everyone"])
        stored = await self.runtime.db.load_messages("napcat:group:55:1")
        self.assertEqual([m["role"] for m in stored], ["user"])
        self.assertEqual(stored[0]["content"], "hello everyone")
        state = await orch.user_graph.aget_state({"configurable": {"thread_id": "napcat:group:55:1"}})
        humans = [
            getattr(m, "content", "")
            for m in list((state.values or {}).get("messages") or [])
            if getattr(m, "type", "") == "human"
        ]
        self.assertEqual(humans, [])

    async def test_cross_user_context_stays_on_per_person_session(self):
        orch = self.runtime.orchestrator
        captured: dict = {}
        graph = orch.user_graph
        original = graph.ainvoke

        async def wrapped(*args, **kwargs):
            captured["config"] = kwargs.get("config") or (args[1] if len(args) > 1 else None)
            return await original(*args, **kwargs)

        graph.ainvoke = wrapped
        try:
            await orch.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="group",
                    chat_id="56",
                    user_id="1",
                    text="hello everyone",
                    sender_name="张三",
                ),
                deliver=False,
            )
            replied = await orch.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="group",
                    chat_id="56",
                    user_id="2",
                    text="[@1001] 帮看下",
                    sender_name="李四",
                    at_user_ids=["1001"],
                    self_id="1001",
                ),
                deliver=False,
            )
        finally:
            graph.ainvoke = original

        self.assertFalse(replied.ignored)
        self.assertRegex(replied.session_id, r"^napcat:group:56:shard:[a-f0-9]+$")
        conf = dict((captured.get("config") or {}).get("configurable") or {})
        group_context = conf.get("group_context") or ""
        self.assertIn("hello everyone", group_context)
        self.assertIn("帮看下", group_context)
        self.assertIn("张三", group_context)
        self.assertIn("李四", group_context)
        self.assertIn("当前对你说话的是李四", group_context)

        events = await self.runtime.db.load_group_recent("napcat", "56")
        contents = [e["content"] for e in events]
        self.assertIn("hello everyone", contents)
        self.assertIn("[@1001] 帮看下", contents)
        self.assertTrue(any(e["role"] == "assistant" for e in events))

        sessions = await self.runtime.db.list_sessions()
        ids = {s["session_id"] for s in sessions}
        self.assertIn("napcat:group:56:1", ids)
        self.assertTrue(any(i.startswith("napcat:group:56:shard:") for i in ids))

    async def test_same_user_parked_turn_stays_in_session(self):
        orch = self.runtime.orchestrator
        captured: dict = {}
        graph = orch.user_graph
        original = graph.ainvoke

        async def wrapped(*args, **kwargs):
            captured["config"] = kwargs.get("config") or (args[1] if len(args) > 1 else None)
            captured["payload"] = args[0] if args else kwargs.get("input")
            return await original(*args, **kwargs)

        graph.ainvoke = wrapped
        try:
            parked = await orch.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="group",
                    chat_id="57",
                    user_id="1",
                    text="hello everyone",
                    sender_name="张三",
                ),
                deliver=False,
            )
            self.assertTrue(parked.ignored)
            replied = await orch.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="group",
                    chat_id="57",
                    user_id="1",
                    text="[@1001] 帮看下刚才那个",
                    sender_name="张三",
                    at_user_ids=["1001"],
                    self_id="1001",
                ),
                deliver=False,
            )
        finally:
            graph.ainvoke = original

        self.assertFalse(replied.ignored)
        conf = dict((captured.get("config") or {}).get("configurable") or {})
        group_context = conf.get("group_context") or ""
        self.assertIn("hello everyone", group_context)
        self.assertIn("帮看下刚才那个", group_context)
        self.assertIn("张三", group_context)
        payload = captured.get("payload") or {}
        invoke_humans = [
            str(getattr(m, "content", ""))
            for m in list(payload.get("messages") or [])
            if getattr(m, "type", "") == "human"
        ]
        self.assertTrue(any("帮看下刚才那个" in text for text in invoke_humans))
        self.assertFalse(any("hello everyone" in text for text in invoke_humans))
        state = await orch.user_graph.aget_state({"configurable": {"thread_id": replied.session_id}})
        humans = [
            str(getattr(m, "content", ""))
            for m in list((state.values or {}).get("messages") or [])
            if getattr(m, "type", "") == "human"
        ]
        self.assertFalse(any("hello everyone" in text for text in humans))
        self.assertTrue(any("帮看下刚才那个" in text for text in humans))
        stored = await self.runtime.db.load_messages(replied.session_id)
        self.assertGreaterEqual(len([m for m in stored if m["role"] == "user"]), 1)
        parked = await self.runtime.db.load_messages("napcat:group:57:1")
        self.assertTrue(any("hello everyone" in m["content"] for m in parked))

    async def test_chime_silence_keeps_human_context(self):
        orch = self.runtime.orchestrator
        self.runtime.identity.settings.group_tech_chance = 1.0
        self.runtime.identity.settings.group_chime_cooldown_sec = 0
        graph = orch.user_graph
        original = graph.ainvoke

        async def wrapped(*args, **kwargs):
            result = await original(*args, **kwargs)
            messages = list(result.get("messages") or [])
            out = []
            replaced = False
            for message in messages:
                if not replaced and getattr(message, "type", "") == "ai":
                    copier = getattr(message, "model_copy", None)
                    if callable(copier):
                        out.append(copier(update={"content": "[SILENCE]"}))
                    else:
                        message.content = "[SILENCE]"
                        out.append(message)
                    replaced = True
                    continue
                out.append(message)
            return {**result, "messages": out}

        graph.ainvoke = wrapped
        try:
            silent = await orch.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="group",
                    chat_id="58",
                    user_id="3",
                    text="docker 起不来报错了怎么修",
                    sender_name="王五",
                ),
                deliver=False,
            )
        finally:
            graph.ainvoke = original

        self.assertTrue(silent.ignored)
        stored = await self.runtime.db.load_messages(silent.session_id)
        self.assertEqual([m["role"] for m in stored], ["user"])
        events = await self.runtime.db.load_group_recent("napcat", "58")
        self.assertEqual([e["role"] for e in events], ["user"])

    async def test_private_session_stays_per_user(self):
        orch = self.runtime.orchestrator
        resp = await orch.handle(
            InboundMessage(
                platform="napcat",
                channel_type="private",
                chat_id="42",
                user_id="42",
                text="hi",
            ),
            deliver=False,
        )
        self.assertFalse(resp.ignored)
        self.assertEqual(resp.session_id, "napcat:private:42")
        self.assertEqual(await self.runtime.db.load_group_recent("napcat", "42"), [])

    async def test_same_speaker_followup_beats_cooldown(self):
        orch = self.runtime.orchestrator
        ident = self.runtime.identity.settings
        ident.group_chime_cooldown_sec = 999
        ident.group_engage_sec = 90
        ident.group_engage_replies = 2
        first = await orch.handle(
            InboundMessage(
                platform="napcat",
                channel_type="group",
                chat_id="80",
                user_id="1",
                text="docker 起不来报错了怎么修",
                sender_name="张三",
            ),
            deliver=False,
        )
        self.assertFalse(first.ignored)
        second = await orch.handle(
            InboundMessage(
                platform="napcat",
                channel_type="group",
                chat_id="80",
                user_id="1",
                text="还是不行，日志在这",
                sender_name="张三",
            ),
            deliver=False,
        )
        self.assertFalse(second.ignored)
        outsider = await orch.handle(
            InboundMessage(
                platform="napcat",
                channel_type="group",
                chat_id="80",
                user_id="2",
                text="晚上吃饭不",
                sender_name="李四",
            ),
            deliver=False,
        )
        self.assertTrue(outsider.ignored)

    async def test_quoted_reply_is_fetched_into_user_text(self):
        orch = self.runtime.orchestrator
        captured: dict = {}
        graph = orch.user_graph
        original = graph.ainvoke

        async def wrapped(*args, **kwargs):
            captured["payload"] = args[0] if args else kwargs.get("input")
            return await original(*args, **kwargs)

        async def fake_fetch(message_id: str):
            self.assertEqual(str(message_id), "12345")
            return {
                "sender": {"nickname": "llluopita.", "card": "llluopita."},
                "message": [{"type": "text", "data": {"text": "草，我的人设卡被扒出来挂墙上了是吧"}}],
            }

        graph.ainvoke = wrapped
        self.runtime.napcat.fetch_message = fake_fetch  # type: ignore[method-assign]
        try:
            replied = await orch.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="group",
                    chat_id="1091265973",
                    user_id="3430694875",
                    text="[回复:12345][@1001] 还说脏话",
                    sender_name="辞隅",
                    at_user_ids=["1001"],
                    reply_to_ids=["12345"],
                    self_id="1001",
                ),
                deliver=False,
            )
        finally:
            graph.ainvoke = original

        self.assertFalse(replied.ignored)
        payload = captured.get("payload") or {}
        humans = [
            str(getattr(m, "content", ""))
            for m in list(payload.get("messages") or [])
            if getattr(m, "type", "") == "human"
        ]
        self.assertTrue(humans)
        body = humans[0]
        self.assertIn("草，我的人设卡被扒出来挂墙上了是吧", body)
        self.assertIn("被回复的原话", body)
        self.assertIn("当前这句", body)
        self.assertIn("还说脏话", body)
        self.assertNotIn("[回复:12345]", body)


if __name__ == "__main__":
    unittest.main()
