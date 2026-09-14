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


if __name__ == "__main__":
    unittest.main()
