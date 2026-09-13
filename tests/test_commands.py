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
        for name in ("help", "ping", "status", "time", "whoami", "model", "allow", "clear"):
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
            self.assertNotIn("(mock)", help_body["reply"])
            miss = client.post("/chat", json={"text": "/nope"}).json()
            self.assertIn("没有 `/nope`", miss["reply"])

    def test_user_slash_is_not_a_command(self):
        with TestClient(self.app) as client:
            resp = client.post("/chat", json={"text": "/ping", "user_id": "stranger"})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["role"], "user")
            self.assertEqual(resp.json()["reply"], "(mock) /ping")

    def test_owner_status_and_clear(self):
        with TestClient(self.app) as client:
            status = client.post("/chat", json={"text": "/status"}).json()
            self.assertIn("模型", status["reply"])
            self.assertIn("mock", status["reply"])
            cleared = client.post("/chat", json={"text": "/clear"}).json()
            self.assertIn("忘掉", cleared["reply"])
