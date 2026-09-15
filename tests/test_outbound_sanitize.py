from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage

from core.chat import last_ai_text
from core.outbound_sanitize import looks_like_tool_markup, sanitize_outbound_text
from interface.platform.registry import AdapterRegistry
from msg.schema import OutboundMessage


class TestOutboundSanitize(unittest.TestCase):
    def test_strips_dsml_and_recovers_message_param(self):
        raw = (
            '<｜｜DSML｜｜ invoke name="qq_send_group_message">\n\n'
            '<｜｜DSML｜｜ parameter name="group_id" string="true">967712135</｜｜DSML｜｜ parameter>\n\n'
            '<｜｜DSML｜｜ parameter name="message" string="true">【今晚题·Linux】\n\n'
            "用 epoll 的 LT 和 ET</｜｜DSML｜｜ parameter>\n\n"
            "</｜｜DSML｜｜ invoke>\n\n"
            "</｜｜DSML｜｜ calls>"
        )
        self.assertTrue(looks_like_tool_markup(raw))
        cleaned = sanitize_outbound_text(raw)
        self.assertFalse(looks_like_tool_markup(cleaned))
        self.assertIn("今晚题", cleaned)
        self.assertNotIn("DSML", cleaned)
        self.assertNotIn("invoke", cleaned)

    def test_markup_only_becomes_empty(self):
        raw = '<｜｜DSML｜｜invoke name="run_shell">\n</｜｜DSML｜｜invoke>'
        self.assertEqual(sanitize_outbound_text(raw), "")

    def test_last_ai_text_skips_dsml_when_clean_exists(self):
        result = {
            "messages": [
                AIMessage(content="今晚题发了"),
                AIMessage(
                    content='<｜｜DSML｜｜invoke name="run_shell"></｜｜DSML｜｜invoke>',
                    tool_calls=[
                        {
                            "name": "run_shell",
                            "args": {"command": "ls"},
                            "id": "c1",
                            "type": "tool_call",
                        }
                    ],
                ),
            ]
        }
        self.assertEqual(last_ai_text(result), "今晚题发了")

    def test_strips_common_markdown(self):
        raw = (
            "## 排查\n"
            "先看 **postgres** 日志，别用 *教程腔*\n"
            "\n"
            "```bash\n"
            "docker compose logs postgres --tail 50\n"
            "```\n"
            "\n"
            "- 先看端口\n"
            "- 再看密码\n"
            "\n"
            "参考 [文档](https://example.com)"
        )
        cleaned = sanitize_outbound_text(raw)
        self.assertNotIn("**", cleaned)
        self.assertNotIn("##", cleaned)
        self.assertNotIn("```", cleaned)
        self.assertNotIn("`", cleaned)
        self.assertNotIn("[文档](", cleaned)
        self.assertIn("postgres", cleaned)
        self.assertIn("docker compose logs postgres --tail 50", cleaned)
        self.assertIn("先看端口", cleaned)
        self.assertIn("文档 https://example.com", cleaned)
        self.assertIn("\n\n", cleaned)

    def test_keeps_filenames_silence_and_cq(self):
        self.assertEqual(
            sanitize_outbound_text("改 tests/test_clock.py 再跑"),
            "改 tests/test_clock.py 再跑",
        )
        self.assertEqual(sanitize_outbound_text("[SILENCE]"), "[SILENCE]")
        self.assertEqual(sanitize_outbound_text("[CQ:at,qq=1001] 你看下"), "[CQ:at,qq=1001] 你看下")
        self.assertEqual(sanitize_outbound_text("3 * 4 * 5"), "3 * 4 * 5")

    def test_last_ai_text_strips_markdown(self):
        result = {"messages": [AIMessage(content="别 **加粗**，直接 `ls`")]}
        self.assertEqual(last_ai_text(result), "别 加粗，直接 ls")


class _CaptureAdapter:
    name = "napcat"

    def __init__(self) -> None:
        self.sent: list[str] = []

    def enabled(self) -> bool:
        return True

    async def send(self, message: OutboundMessage) -> str | None:
        self.sent.append(message.text)
        return "1"


class TestSendGate(unittest.IsolatedAsyncioTestCase):
    async def test_registry_blocks_dsml_bubbles(self):
        adapter = _CaptureAdapter()
        registry = AdapterRegistry([adapter])
        mid = await registry.send(
            OutboundMessage(
                platform="napcat",
                channel_type="group",
                chat_id="1",
                text='<｜｜DSML｜｜invoke name="run_shell"></｜｜DSML｜｜invoke>',
            )
        )
        self.assertIsNone(mid)
        self.assertEqual(adapter.sent, [])

    async def test_registry_sanitizes_message_param(self):
        adapter = _CaptureAdapter()
        registry = AdapterRegistry([adapter])
        raw = (
            '<｜｜DSML｜｜ parameter name="message" string="true">'
            "今晚题发了</｜｜DSML｜｜ parameter>"
        )
        mid = await registry.send(
            OutboundMessage(platform="napcat", channel_type="group", chat_id="1", text=raw)
        )
        self.assertEqual(mid, "1")
        self.assertEqual(adapter.sent, ["今晚题发了"])

    async def test_registry_strips_markdown(self):
        adapter = _CaptureAdapter()
        registry = AdapterRegistry([adapter])
        mid = await registry.send(
            OutboundMessage(
                platform="napcat",
                channel_type="group",
                chat_id="1",
                text="先看 **postgres** 日志\n\n`docker compose logs postgres`",
            )
        )
        self.assertEqual(mid, "1")
        self.assertEqual(adapter.sent, ["先看 postgres 日志\n\ndocker compose logs postgres"])


if __name__ == "__main__":
    unittest.main()
