from __future__ import annotations

import os
import tempfile
import unittest

from core.commands import parse_slash, render_help, render_unknown, render_allow, render_whoami

_tmp = tempfile.mkdtemp(prefix="luopita-cmd-")
os.environ["LUOPITA_PROVIDER"] = "mock"
os.environ["LUOPITA_API_KEY"] = ""
os.environ["LUOPITA_DATABASE_URL"] = "memory://"
os.environ["LUOPITA_NAPCAT_ENABLED"] = "false"
os.environ["LUOPITA_IDENTITY_FILE"] = os.path.join(_tmp, "identity.yaml")
os.environ["LUOPITA_PERSON_FILE"] = os.path.join(_tmp, "person.yaml")

from fastapi.testclient import TestClient

from app.main import create_app
from app.runtime import Runtime
from core.group_context.keys import event_epoch, shard_id_from_session_key
from msg.schema import InboundMessage
from utils.config import OwnerEntry


class TestParseSlash(unittest.TestCase):
    def test_plain_and_aliases(self):
        self.assertEqual(parse_slash("/help"), ("help", ""))
        self.assertEqual(parse_slash("/"), ("help", ""))
        self.assertEqual(parse_slash("/ping"), ("ping", ""))
        self.assertEqual(parse_slash("/reset"), ("clear", ""))
        self.assertEqual(parse_slash("/stat extra"), ("status", "extra"))
        self.assertIsNone(parse_slash("help"))
        self.assertIsNone(parse_slash("。"))
        self.assertIsNone(parse_slash("!ping"))

    def test_strips_reply_and_name(self):
        self.assertEqual(
            parse_slash("[回复:1][@1001] /status", persona_name="小Lu"),
            ("status", ""),
        )
        self.assertEqual(
            parse_slash("小Lu /ping", keywords=["小Lu"], persona_name="小Lu"),
            ("ping", ""),
        )

    def test_help_lists_commands(self):
        text = render_help()
        for name in ("help", "ping", "status", "time", "whoami", "model", "allow", "clear", "cron"):
            self.assertIn(f"/{name}", text)
        self.assertIn("没有 `/nope`", render_unknown("nope"))
        self.assertIn("curl", render_allow(["ls", "curl"]))
        who = render_whoami(
            nickname="小鲨鱼",
            platform="napcat",
            user_id="1",
            channel_type="group",
            chat_id="9",
            bot_id="2",
        )
        self.assertIn("小鲨鱼", who)
        self.assertIn("群 9", who)


class TestOwnerCommandsHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def test_owner_ping_skips_llm(self):
        with TestClient(self.app) as client:
            resp = client.post("/chat", json={"text": "/ping"})
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["reply"], "在。")
            self.assertEqual(body["role"], "owner")
            self.assertFalse(body["ignored"])

    def test_owner_help_and_unknown(self):
        with TestClient(self.app) as client:
            help_body = client.post("/chat", json={"text": "/help"}).json()
            self.assertIn("/clear", help_body["reply"])
            self.assertIn("/cron", help_body["reply"])
            self.assertNotIn("(mock)", help_body["reply"])
            miss = client.post("/chat", json={"text": "/nope"}).json()
            self.assertIn("没有 `/nope`", miss["reply"])

    def test_user_slash_is_not_a_command(self):
        with TestClient(self.app) as client:
            resp = client.post("/chat", json={"text": "/ping", "user_id": "stranger"})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["role"], "user")
            self.assertEqual(resp.json()["reply"], "(mock) /ping")

    def test_owner_cron_list_and_add(self):
        with TestClient(self.app) as client:
            empty = client.post("/chat", json={"text": "/cron"}).json()
            self.assertIn("还没有定时任务", empty["reply"])
            self.assertNotIn("(mock)", empty["reply"])
            added = client.post("/chat", json={"text": "/cron add 20m 提醒喝水"}).json()
            self.assertIn("记下了", added["reply"])
            self.assertIn("提醒喝水", added["reply"])
            listed = client.post("/chat", json={"text": "/cron list"}).json()
            self.assertIn("提醒喝水", listed["reply"])

    def test_user_cron_is_not_a_command(self):
        with TestClient(self.app) as client:
            resp = client.post("/chat", json={"text": "/cron", "user_id": "stranger"})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["role"], "user")
            self.assertEqual(resp.json()["reply"], "(mock) /cron")

    def test_owner_status_and_clear(self):
        with TestClient(self.app) as client:
            status = client.post("/chat", json={"text": "/status"}).json()
            self.assertIn("模型", status["reply"])
            self.assertIn("mock", status["reply"])
            cleared = client.post("/chat", json={"text": "/clear"}).json()
            self.assertIn("忘掉", cleared["reply"])

    def test_owner_clear_drops_history(self):
        with TestClient(self.app) as client:
            sid = "admin:console:clear-me"
            client.post("/chat", json={"text": "secret banana token", "session_id": sid})
            hist = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
            self.assertTrue(any("banana" in m["content"] for m in hist))
            cleared = client.post("/chat", json={"text": "/clear", "session_id": sid}).json()
            self.assertIn("忘掉", cleared["reply"])
            hist2 = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
            blob = " ".join(m["content"] for m in hist2)
            self.assertNotIn("banana", blob)
            self.assertIn("/clear", blob)


class TestClearForgetsContext(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = await Runtime.start()

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_clear_wipes_graph_and_long_term(self):
        orch = self.runtime.orchestrator
        session_id = "admin:console:wipe"
        await orch.handle(
            InboundMessage(
                platform="admin",
                channel_type="console",
                chat_id="wipe",
                user_id="admin",
                text="secret banana token",
                session_key=session_id,
            ),
            deliver=False,
        )
        await self.runtime.db.put_memory(user_id="admin", content="User: secret banana token")
        recalled = await self.runtime.memory.recall("admin", "banana")
        self.assertTrue(any("banana" in item for item in recalled))
        await orch.handle(
            InboundMessage(
                platform="admin",
                channel_type="console",
                chat_id="wipe",
                user_id="admin",
                text="/clear",
                session_key=session_id,
            ),
            deliver=False,
        )
        state = await orch.owner_graph.aget_state({"configurable": {"thread_id": session_id}})
        leftover = " ".join(
            str(getattr(message, "content", "") or "")
            for message in list((state.values or {}).get("messages") or [])
        )
        self.assertNotIn("banana", leftover)
        stored = await self.runtime.db.load_messages(session_id)
        blob = " ".join(item["content"] for item in stored)
        self.assertNotIn("banana", blob)
        self.assertEqual(await self.runtime.memory.recall("admin", "banana"), [])

    async def test_group_clear_wipes_shard_and_hot_layer(self):
        ident = self.runtime.identity.settings
        ident.owners = list(ident.owners) + [OwnerEntry(platform="napcat", user_id="7", nickname="host")]
        orch = self.runtime.orchestrator
        first = await orch.handle(
            InboundMessage(
                platform="napcat",
                channel_type="group",
                chat_id="88",
                user_id="7",
                text="小Lu secret banana token",
                sender_name="host",
            ),
            deliver=False,
        )
        self.assertFalse(first.ignored)
        shard_sid = first.session_id
        shard_id = shard_id_from_session_key(shard_sid)
        self.assertTrue(shard_id)
        turns = await self.runtime.hot.load_turns(
            platform="napcat", chat_id="88", user_id="7", shard_id=shard_id
        )
        self.assertTrue(turns)
        await orch.handle(
            InboundMessage(
                platform="napcat",
                channel_type="group",
                chat_id="88",
                user_id="7",
                text="/clear",
                sender_name="host",
            ),
            deliver=False,
        )
        state = await orch.owner_graph.aget_state({"configurable": {"thread_id": shard_sid}})
        leftover = " ".join(
            str(getattr(message, "content", "") or "")
            for message in list((state.values or {}).get("messages") or [])
        )
        self.assertNotIn("banana", leftover)
        self.assertEqual(
            await self.runtime.hot.load_turns(
                platform="napcat", chat_id="88", user_id="7", shard_id=shard_id
            ),
            [],
        )
        stored = await self.runtime.db.load_messages(shard_sid)
        self.assertFalse(any("banana" in item["content"] for item in stored))
        self.assertEqual(await self.runtime.db.get_user_active_shard("napcat", "88", "7"), "")
        second = await orch.handle(
            InboundMessage(
                platform="napcat",
                channel_type="group",
                chat_id="88",
                user_id="7",
                text="小Lu ping after clear",
                sender_name="host",
            ),
            deliver=False,
        )
        self.assertFalse(second.ignored)
        self.assertNotEqual(second.session_id, shard_sid)
        cutoff = await self.runtime.hot.user_cleared_at("napcat", "88", "7")
        self.assertGreater(cutoff, 0)
        mem = await self.runtime.hot.load_group_mem(platform="napcat", chat_id="88", limit=40)
        visible = " ".join(
            str(event.get("content") or "")
            for event in mem
            if event_epoch(event) >= cutoff
        )
        self.assertNotIn("banana", visible)
