import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="luopita-test-")
os.environ["LUOPITA_PROVIDER"] = "mock"
os.environ["LUOPITA_API_KEY"] = ""
os.environ["LUOPITA_DATABASE_URL"] = "memory://"
os.environ["LUOPITA_NAPCAT_ENABLED"] = "false"
os.environ.pop("LUOPITA_ADMIN_TOKEN", None)
os.environ["LUOPITA_IDENTITY_FILE"] = os.path.join(_tmp, "identity.yaml")
os.environ["LUOPITA_PERSON_FILE"] = os.path.join(_tmp, "person.yaml")
os.environ["LUOPITA_EXAMPLES_FILE"] = os.path.join(_tmp, "voice_examples.yaml")

from fastapi.testclient import TestClient

from app.main import create_app


class TestHttpAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def test_health(self):
        with TestClient(self.app) as client:
            resp = client.get("/health")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["ok"], True)
            self.assertEqual(resp.json()["service"], "luopita")
            self.assertEqual(resp.json()["database"], "memory")

    def test_chat_mock(self):
        with TestClient(self.app) as client:
            resp = client.post("/chat", json={"text": "hi", "user_id": "u1"})
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["ok"], True)
            self.assertIn("session_id", body)
            self.assertEqual(body["reply"], "(mock) hi")

    def test_chat_requires_text(self):
        with TestClient(self.app) as client:
            resp = client.post("/api/chat", json={"text": "  "})
            self.assertEqual(resp.status_code, 400)

    def test_config_masks_secrets(self):
        with TestClient(self.app) as client:
            put = client.put(
                "/api/config",
                json={"llm": {"api_key": "sk-secret-value", "provider": "mock"}},
            )
            self.assertEqual(put.status_code, 200)
            got = client.get("/api/config")
            self.assertEqual(got.status_code, 200)
            key = got.json()["config"]["llm"]["api_key"]
            self.assertNotEqual(key, "sk-secret-value")
            self.assertIn("****", key)

    def test_persona_roundtrip(self):
        with TestClient(self.app) as client:
            resp = client.put(
                "/api/persona",
                json={
                    "name": "Pita",
                    "owner_address": "rovina",
                    "voice": "短句",
                    "taboos": "不自称 AI",
                    "relationship": "一起写代码",
                    "system_prompt": "You are Pita.",
                },
            )
            self.assertEqual(resp.status_code, 200)
            persona = resp.json()["persona"]
            self.assertEqual(persona["name"], "Pita")
            self.assertEqual(persona["owner_address"], "rovina")
            self.assertEqual(persona["voice"], "短句")

            person_path = os.environ["LUOPITA_PERSON_FILE"]
            with open(person_path, encoding="utf-8") as fh:
                on_disk = fh.read()
            self.assertIn("Pita", on_disk)
            self.assertIn("You are Pita.", on_disk)

            with open(person_path, "w", encoding="utf-8") as fh:
                fh.write("name: FromFile\nowner_address: disk\nvoice: yaml\ntaboos: x\nrelationship: y\nsystem_prompt: file prompt\n")
            reread = client.get("/api/persona")
            self.assertEqual(reread.status_code, 200)
            self.assertEqual(reread.json()["persona"]["name"], "FromFile")
            self.assertEqual(reread.json()["persona"]["voice"], "yaml")
            cfg = client.get("/api/config").json()["config"]
            self.assertEqual(cfg["persona"]["name"], "FromFile")

    def test_examples_roundtrip(self):
        with TestClient(self.app) as client:
            got = client.get("/api/examples")
            self.assertEqual(got.status_code, 200)
            self.assertIn("examples", got.json())
            payload = {
                "examples": [
                    {
                        "id": "tech_port_direct_peer",
                        "scene": "tech",
                        "mode": "direct",
                        "relation": "peer",
                        "input": "5170 起不来",
                        "good": "多半是端口占了",
                        "bad": "好的，这个问题可以从以下几个方面排查",
                    },
                    {
                        "scene": "chat",
                        "mode": "chime",
                        "relation": "peer",
                        "input": "这配置也太绕了",
                        "good": "是有点绕",
                        "bad": "",
                    },
                ]
            }
            put = client.put("/api/examples", json=payload)
            self.assertEqual(put.status_code, 200)
            ids = [item["id"] for item in put.json()["examples"]]
            self.assertIn("tech_port_direct_peer", ids)
            self.assertTrue(any(item.startswith("chat_chime_peer") or item.startswith("ex_") for item in ids))
            reread = client.get("/api/examples").json()["examples"]
            self.assertEqual(len(reread), 2)
            bad = client.put(
                "/api/examples",
                json={"examples": [{"scene": "nope", "mode": "direct", "relation": "peer", "good": "x"}]},
            )
            self.assertEqual(bad.status_code, 400)

    def test_profiles_list_and_delete(self):
        with TestClient(self.app) as client:
            runtime = self.app.state.runtime
            runtime.db.user_profiles[("napcat", "9", "1")] = {
                "platform": "napcat",
                "chat_id": "9",
                "user_id": "1",
                "display_name": "Ada",
                "preferences": "",
                "notes": "",
                "card": {
                    "address": "Ada",
                    "familiarity": "peer",
                    "reply_pref": "短",
                    "stack": ["docker"],
                },
                "updated_at": "2026-09-14T00:00:00+00:00",
            }
            listed = client.get("/api/profiles")
            self.assertEqual(listed.status_code, 200)
            rows = listed.json()["profiles"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["display_name"], "Ada")
            self.assertEqual(rows[0]["card"]["reply_pref"], "短")
            self.assertEqual(rows[0]["updated_at"], "2026-09-14 08:00（UTC+8）")
            self.assertIn("[当前说话人]", rows[0]["prompt"])
            deleted = client.delete(
                "/api/profiles",
                params={"platform": "napcat", "chat_id": "9", "user_id": "1"},
            )
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(client.get("/api/profiles").json()["profiles"], [])

    def test_identity_roundtrip(self):
        with TestClient(self.app) as client:
            got = client.get("/api/identity")
            self.assertEqual(got.status_code, 200)
            owners = {(o["platform"], o["user_id"]) for o in got.json()["identity"]["owners"]}
            self.assertIn(("admin", "admin"), owners)
            put = client.put(
                "/api/identity",
                json={
                    "group_require_at": True,
                    "owners": [
                        {"platform": "napcat", "user_id": "123456", "nickname": "rovina"},
                    ],
                },
            )
            self.assertEqual(put.status_code, 200)
            keys = {(o["platform"], o["user_id"]) for o in put.json()["identity"]["owners"]}
            self.assertIn(("napcat", "123456"), keys)
            self.assertIn(("admin", "admin"), keys)
            cfg = client.get("/api/config").json()["config"]
            self.assertEqual(cfg["identity"]["group_require_at"], True)

    def test_identity_engage_and_agent_runtime_settings(self):
        with TestClient(self.app) as client:
            ident = client.put(
                "/api/identity",
                json={"group_engage_sec": 45, "group_engage_replies": 3},
            )
            self.assertEqual(ident.status_code, 200)
            body = ident.json()["identity"]
            self.assertEqual(body["group_engage_sec"], 45)
            self.assertEqual(body["group_engage_replies"], 3)
            cfg = client.put(
                "/api/config",
                json={"agent": {"short_term_messages": 12}, "log_level": "WARNING"},
            )
            self.assertEqual(cfg.status_code, 200)
            saved = cfg.json()["config"]
            self.assertEqual(saved["agent"]["short_term_messages"], 12)
            self.assertEqual(saved["log_level"], "WARNING")

    def test_group_ignored_without_at(self):
        with TestClient(self.app) as client:
            resp = client.post(
                "/webhooks/napcat",
                json={
                    "post_type": "message",
                    "message_type": "group",
                    "group_id": 88,
                    "user_id": 7,
                    "message": "hello everyone",
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["ignored"])
            hist = client.get("/api/sessions/napcat:group:88:7/messages")
            self.assertEqual(hist.status_code, 200)
            roles = [m["role"] for m in hist.json()["messages"]]
            self.assertIn("user", roles)
            self.assertNotIn("assistant", roles)

    def test_group_keyword_replies_like_at(self):
        with TestClient(self.app) as client:
            client.put("/api/identity", json={"group_tech_chance": 0, "group_chatty_chance": 0})
            resp = client.post(
                "/webhooks/napcat",
                json={
                    "post_type": "message",
                    "message_type": "group",
                    "group_id": 88,
                    "user_id": 7,
                    "message": "小lu 帮看下",
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["ignored"])
            self.assertRegex(resp.json()["session_id"], r"^napcat:group:88:shard:[a-f0-9]+$")

    def test_group_cq_at_replies_without_configured_bot_id(self):
        with TestClient(self.app) as client:
            resp = client.post(
                "/webhooks/napcat",
                json={
                    "post_type": "message",
                    "message_type": "group",
                    "self_id": 10001,
                    "group_id": 88,
                    "user_id": 7,
                    "message": "[CQ:at,qq=10001] 在吗",
                    "raw_message": "[CQ:at,qq=10001] 在吗",
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["ignored"])
            self.assertRegex(resp.json()["session_id"], r"^napcat:group:88:shard:[a-f0-9]+$")

    def test_group_tech_chimes_in(self):
        with TestClient(self.app) as client:
            client.put("/api/identity", json={"group_tech_chance": 1.0, "group_chime_cooldown_sec": 0})
            resp = client.post(
                "/webhooks/napcat",
                json={
                    "post_type": "message",
                    "message_type": "group",
                    "group_id": 77,
                    "user_id": 6,
                    "message": "docker 起不来报错了怎么修",
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["ignored"])
            self.assertRegex(resp.json()["session_id"], r"^napcat:group:77:shard:[a-f0-9]+$")

    def test_group_owner_filler_is_ignored(self):
        with TestClient(self.app) as client:
            client.put(
                "/api/identity",
                json={"owners": [{"platform": "napcat", "user_id": "10001", "nickname": "host"}]},
            )
            resp = client.post(
                "/webhooks/napcat",
                json={
                    "post_type": "message",
                    "message_type": "group",
                    "group_id": 9,
                    "user_id": 10001,
                    "message": "我在",
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["ignored"])
            named = client.post(
                "/webhooks/napcat",
                json={
                    "post_type": "message",
                    "message_type": "group",
                    "self_id": "2002",
                    "group_id": 9,
                    "user_id": 10001,
                    "message": [{"type": "at", "data": {"qq": "2002"}}, {"type": "text", "data": {"text": " 帮我看下"}}],
                },
            )
            self.assertFalse(named.json()["ignored"])
            self.assertEqual(named.json()["role"], "owner")
            self.assertRegex(named.json()["session_id"], r"^napcat:group:9:shard:[a-f0-9]+$")

    def test_group_at_bot_replies(self):
        with TestClient(self.app) as client:
            client.put("/api/config", json={"napcat": {"bot_id": "2002"}})
            resp = client.post(
                "/webhooks/napcat",
                json={
                    "post_type": "message",
                    "message_type": "group",
                    "group_id": 11,
                    "user_id": 8,
                    "message": [{"type": "at", "data": {"qq": "2002"}}, {"type": "text", "data": {"text": " hi"}}],
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["ignored"])
            self.assertEqual(resp.json()["role"], "user")
            sid = resp.json()["session_id"]
            self.assertRegex(sid, r"^napcat:group:11:shard:[a-f0-9]+$")
            sessions = client.get("/api/sessions").json()["sessions"]
            row = next(s for s in sessions if s["session_id"] == sid)
            self.assertGreaterEqual(row["message_count"], 1)

    def test_platform_toggle(self):
        with TestClient(self.app) as client:
            resp = client.post("/api/platforms/napcat/toggle", json={"enabled": True})
            self.assertEqual(resp.status_code, 200)
            names = {p["name"]: p["enabled"] for p in resp.json()["platforms"]}
            self.assertTrue(names["napcat"])
            client.post("/api/platforms/napcat/toggle", json={"enabled": False})

    def test_napcat_webhook_ignores_non_message(self):
        with TestClient(self.app) as client:
            resp = client.post("/webhooks/napcat", json={"post_type": "meta_event"})
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["ignored"])

    def test_napcat_webhook_private_chat(self):
        with TestClient(self.app) as client:
            resp = client.post(
                "/webhooks/napcat",
                json={
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": 10001,
                    "message": [{"type": "text", "data": {"text": "hello"}}],
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["ignored"])
            sessions = client.get("/api/sessions").json()["sessions"]
            self.assertTrue(any("10001" in str(s["session_id"]) for s in sessions))

    def test_napcat_webhook_unauthorized(self):
        with TestClient(self.app) as client:
            client.put("/api/config", json={"napcat": {"access_token": "s3cret"}})
            resp = client.post(
                "/webhooks/napcat",
                json={"post_type": "message", "message_type": "private", "user_id": 1, "message": "x"},
            )
            self.assertEqual(resp.status_code, 401)
            ok = client.post(
                "/webhooks/napcat",
                headers={"Authorization": "Bearer s3cret"},
                json={"post_type": "message", "message_type": "private", "user_id": 1, "message": "ping"},
            )
            self.assertEqual(ok.status_code, 200)

    def test_napcat_webhook_hmac_signature(self):
        import hashlib
        import hmac
        import json

        with TestClient(self.app) as client:
            client.put("/api/config", json={"napcat": {"access_token": "s3cret"}})
            body = json.dumps(
                {"post_type": "message", "message_type": "private", "user_id": 2, "message": "sig"}
            ).encode()
            digest = hmac.new(b"s3cret", body, hashlib.sha1).hexdigest()
            resp = client.post(
                "/webhooks/napcat",
                headers={"Content-Type": "application/json", "X-Signature": f"sha1={digest}"},
                content=body,
            )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["ignored"])

    def test_session_history(self):
        with TestClient(self.app) as client:
            chat = client.post("/api/chat", json={"text": "remember me", "session_id": "admin:console:hist"})
            self.assertEqual(chat.status_code, 200)
            hist = client.get("/api/sessions/admin:console:hist/messages")
            self.assertEqual(hist.status_code, 200)
            roles = [m["role"] for m in hist.json()["messages"]]
            self.assertIn("user", roles)
            self.assertIn("assistant", roles)

    def test_napcat_actions_catalog(self):
        with TestClient(self.app) as client:
            resp = client.get("/api/napcat/actions")
            self.assertEqual(resp.status_code, 200)
            names = {item["action"] for item in resp.json()["actions"]}
            self.assertIn("send_msg", names)
            self.assertIn("get_group_list", names)
            self.assertIn("set_group_ban", names)

    def test_napcat_action_blocked_and_disabled(self):
        with TestClient(self.app) as client:
            blocked = client.post("/api/napcat/get_cookies", json={})
            self.assertEqual(blocked.status_code, 403)
            disabled = client.post("/api/napcat/get_login_info", json={})
            self.assertEqual(disabled.status_code, 409)

    def test_napcat_webhook_image_only(self):
        with TestClient(self.app) as client:
            resp = client.post(
                "/webhooks/napcat",
                json={
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": 42,
                    "message": [{"type": "image", "data": {"file": "a.jpg"}}],
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["ignored"])


if __name__ == "__main__":
    unittest.main()
