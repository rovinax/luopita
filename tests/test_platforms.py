import unittest

import httpx

from core.agent_runtime import (
    MAX_SHELL_CALLS_PER_TURN,
    CommandPolicy,
    begin_shell_turn,
    end_shell_turn,
    execute_command,
    parse_agent_tag,
)
from interface.platform.napcat import NapcatAdapter, parse_onebot_event
from interface.platform.napcat_api import NapcatAPIError, NapcatClient


class TestCommandPolicy(unittest.TestCase):
    def setUp(self):
        end_shell_turn()

    def tearDown(self):
        end_shell_turn()

    def test_denied_command(self):
        policy = CommandPolicy(allow={"ls"}, workdir=".", timeout_sec=5)
        result = execute_command("rm -rf /", policy=policy)
        self.assertIn("not allowed", result)

    def test_empty_command(self):
        policy = CommandPolicy(allow={"ls"}, workdir=".", timeout_sec=5)
        result = execute_command("   ", policy=policy)
        self.assertIn("empty", result.lower())

    def test_allowlist_accepts_usr_bin_path(self):
        policy = CommandPolicy(allow={"curl"}, workdir=".", timeout_sec=5)
        denied = execute_command("/tmp/curl https://example.com", policy=policy)
        self.assertIn("not allowed", denied)
        # /usr/bin/curl is treated as curl; we only check the policy gate here.
        result = execute_command("/usr/bin/true", policy=CommandPolicy(allow={"true"}, workdir=".", timeout_sec=5))
        self.assertNotIn("not allowed", result)

    def test_parse_tags_still_available(self):
        tag, payload = parse_agent_tag("[command]: ls")
        self.assertEqual(tag, "[command]")
        self.assertEqual(payload, "ls")

    def test_strips_trailing_semicolon(self):
        begin_shell_turn()
        policy = CommandPolicy(allow={"true"}, workdir=".", timeout_sec=5)
        result = execute_command("true;", policy=policy)
        self.assertNotIn("not allowed", result)
        self.assertNotIn("already ran", result)

    def test_same_command_once_per_turn(self):
        begin_shell_turn()
        policy = CommandPolicy(allow={"true"}, workdir=".", timeout_sec=5)
        first = execute_command("true", policy=policy)
        second = execute_command("true", policy=policy)
        self.assertNotIn("already ran", first)
        self.assertIn("already ran", second)

    def test_shell_call_cap_per_turn(self):
        begin_shell_turn()
        policy = CommandPolicy(allow={"echo"}, workdir=".", timeout_sec=5)
        for i in range(MAX_SHELL_CALLS_PER_TURN):
            out = execute_command(f"echo {i}", policy=policy)
            self.assertNotIn("too many", out)
        ninth = execute_command("echo extra", policy=policy)
        self.assertIn("too many", ninth)

    def test_rejects_pipe_and_keeps_query_ampersand(self):
        policy = CommandPolicy(allow={"echo", "curl"}, workdir=".", timeout_sec=5)
        piped = execute_command(
            'curl -sS "https://zh.wikipedia.org/w/api.php?action=query&list=search" | head -c 800',
            policy=policy,
        )
        self.assertIn("not bash", piped)
        self.assertNotIn("Could not resolve host", piped)
        chained = execute_command("echo hi && echo there", policy=policy)
        self.assertIn("not bash", chained)
        glued = execute_command("echo hi; echo there", policy=policy)
        self.assertIn("not bash", glued)
        sub = execute_command("echo $(whoami)", policy=policy)
        self.assertIn("not bash", sub)
        ok = execute_command("echo https://example.com/search?q=foo&bar=1", policy=policy)
        self.assertNotIn("not bash", ok)
        self.assertIn("foo", ok)

    def test_clips_huge_stdout(self):
        from core.agent_runtime import MAX_STDOUT_CHARS

        policy = CommandPolicy(allow={"echo"}, workdir=".", timeout_sec=5)
        result = execute_command("echo " + ("x" * 12000), policy=policy)
        self.assertIn("truncated", result)
        self.assertLess(len(result), MAX_STDOUT_CHARS + 200)


class TestNapcatParse(unittest.TestCase):
    def test_private_string_message(self):
        inbound = parse_onebot_event(
            {"post_type": "message", "message_type": "private", "user_id": 42, "message": "hi"}
        )
        assert inbound is not None
        self.assertEqual(inbound.platform, "napcat")
        self.assertEqual(inbound.channel_type, "private")
        self.assertEqual(inbound.chat_id, "42")
        self.assertEqual(inbound.text, "hi")

    def test_group_array_message(self):
        inbound = parse_onebot_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 9,
                "user_id": 3,
                "message": [{"type": "text", "data": {"text": "hello"}}],
            }
        )
        assert inbound is not None
        self.assertEqual(inbound.channel_type, "group")
        self.assertEqual(inbound.chat_id, "9")
        self.assertEqual(inbound.session_key, "napcat:group:9:3")

    def test_ignores_notice(self):
        self.assertIsNone(parse_onebot_event({"post_type": "notice"}))

    def test_image_and_at_segments(self):
        inbound = parse_onebot_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 88,
                "user_id": 7,
                "message_id": 55,
                "sender": {"nickname": "Ada", "card": "管理员"},
                "message": [
                    {"type": "at", "data": {"qq": "1001"}},
                    {"type": "text", "data": {"text": " 看图"}},
                    {"type": "image", "data": {"file": "abc.jpg"}},
                ],
            }
        )
        assert inbound is not None
        self.assertEqual(inbound.text, "[@1001] 看图[图片:abc.jpg]")
        self.assertEqual(inbound.at_user_ids, ["1001"])
        self.assertEqual(inbound.message_id, "55")
        self.assertEqual(inbound.sender_name, "管理员")
        self.assertEqual(len(inbound.media), 1)
        self.assertEqual(inbound.media[0].kind, "image")
        self.assertEqual(inbound.media[0].file_id, "abc.jpg")

    def test_cq_at_string_and_self_id(self):
        inbound = parse_onebot_event(
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": 10001,
                "group_id": 1,
                "user_id": 2,
                "message": "[CQ:at,qq=10001] 查下天气",
                "raw_message": "[CQ:at,qq=10001] 查下天气",
            }
        )
        assert inbound is not None
        self.assertEqual(inbound.self_id, "10001")
        self.assertEqual(inbound.at_user_ids, ["10001"])

    def test_reply_segment_and_cq_reply(self):
        from interface.platform.napcat import extract_reply_ids, strip_reply_marks, summarize_fetched_msg

        inbound = parse_onebot_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 1091265973,
                "user_id": 3430694875,
                "self_id": 10001,
                "sender": {"nickname": "辞隅"},
                "message": [
                    {"type": "reply", "data": {"id": "12345"}},
                    {"type": "at", "data": {"qq": "10001"}},
                    {"type": "text", "data": {"text": " 还说脏话"}},
                ],
            }
        )
        assert inbound is not None
        self.assertEqual(inbound.reply_to_ids, ["12345"])
        self.assertIn("[回复:12345]", inbound.text)
        self.assertIn("还说脏话", inbound.text)
        self.assertEqual(inbound.at_user_ids, ["10001"])

        cq = parse_onebot_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 1,
                "user_id": 2,
                "message": "[CQ:reply,id=99][CQ:at,qq=10001] 问啥了",
                "raw_message": "[CQ:reply,id=99][CQ:at,qq=10001] 问啥了",
            }
        )
        assert cq is not None
        self.assertEqual(cq.reply_to_ids, ["99"])
        self.assertEqual(extract_reply_ids(cq.text), ["99"])
        self.assertEqual(strip_reply_marks("[回复:99][@10001] 还说脏话"), "[@10001] 还说脏话")
        self.assertEqual(
            summarize_fetched_msg(
                {
                    "sender": {"nickname": "llluopita.", "card": "llluopita."},
                    "message": [{"type": "text", "data": {"text": "草，我的人设卡被扒出来挂墙上了是吧"}}],
                }
            ),
            "llluopita.: 草，我的人设卡被扒出来挂墙上了是吧",
        )


class TestNapcatClient(unittest.IsolatedAsyncioTestCase):
    async def test_call_ok(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(str(request.url).endswith("/get_login_info"))
            self.assertEqual(request.headers.get("authorization"), "Bearer tok")
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"user_id": 1}})

        client = NapcatClient("http://napcat:3000", access_token="tok", transport=httpx.MockTransport(handler))
        data = await client.call("get_login_info")
        self.assertEqual(data["user_id"], 1)
        await client.aclose()

    async def test_call_failed_status(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "failed", "retcode": 1400, "message": "bad"})

        client = NapcatClient("http://napcat:3000", transport=httpx.MockTransport(handler))
        with self.assertRaises(NapcatAPIError):
            await client.call("send_msg", {"message_type": "private", "user_id": 1, "message": "x"})
        await client.aclose()

    async def test_fetch_message(self):
        from utils.config import NapcatSettings

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(str(request.url).endswith("/get_msg"))
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "sender": {"nickname": "Ada"},
                        "raw_message": "hello",
                        "message": [{"type": "text", "data": {"text": "hello"}}],
                    },
                },
            )

        adapter = NapcatAdapter(NapcatSettings(enabled=True, bot_url="http://napcat:3000"))
        await adapter.api.aclose()
        adapter.api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        data = await adapter.fetch_message("12345")
        assert data is not None
        self.assertEqual(data["raw_message"], "hello")
        await adapter.aclose()


class TestNapcatTools(unittest.IsolatedAsyncioTestCase):
    def test_file_actions_are_on_tool_allowlist(self):
        from interface.platform.napcat_api import TOOL_ACTIONS

        for name in (
            "download_file",
            "upload_group_file",
            "upload_private_file",
            "get_file",
            "get_image",
            "get_group_file_url",
            "get_private_file_url",
            "get_group_root_files",
        ):
            self.assertIn(name, TOOL_ACTIONS)

    async def test_qq_api_blocks_cookies(self):
        from agent.napcat_tools import build_napcat_tools
        from core.identity import reset_current_role, set_current_role
        from utils.config import NapcatSettings

        tools = {t.name: t for t in build_napcat_tools(NapcatAdapter(NapcatSettings(enabled=True)))}
        token = set_current_role("owner")
        try:
            result = await tools["qq_api"].ainvoke({"action": "get_cookies", "params_json": "{}"})
        finally:
            reset_current_role(token)
        self.assertIn("not allowed", result)

    async def test_qq_api_allows_file_upload_download(self):
        from agent.napcat_tools import build_napcat_tools
        from core.identity import reset_current_role, set_current_role
        from utils.config import NapcatSettings

        adapter = NapcatAdapter(NapcatSettings(enabled=True, bot_url="http://napcat:3000"))
        seen: list[str] = []

        async def fake_call(action, params=None):
            seen.append(action)
            return {"ok": True, "action": action, "params": params or {}}

        adapter.api.call = fake_call  # type: ignore[method-assign]
        tools = {t.name: t for t in build_napcat_tools(adapter)}
        token = set_current_role("owner")
        try:
            for action in (
                "download_file",
                "upload_group_file",
                "upload_private_file",
                "get_file",
                "get_image",
                "get_group_file_url",
            ):
                result = await tools["qq_api"].ainvoke({"action": action, "params_json": "{}"})
                self.assertNotIn("not allowed", result.lower())
                self.assertIn(action, result)
        finally:
            reset_current_role(token)
        self.assertEqual(
            seen,
            [
                "download_file",
                "upload_group_file",
                "upload_private_file",
                "get_file",
                "get_image",
                "get_group_file_url",
            ],
        )

    async def test_tools_when_disabled(self):
        from agent.napcat_tools import build_napcat_tools
        from core.identity import reset_current_role, set_current_role
        from utils.config import NapcatSettings

        tools = {t.name: t for t in build_napcat_tools(NapcatAdapter(NapcatSettings(enabled=False)))}
        token = set_current_role("owner")
        try:
            result = await tools["qq_get_login_info"].ainvoke({})
        finally:
            reset_current_role(token)
        self.assertIn("disabled", result.lower())


if __name__ == "__main__":
    unittest.main()
