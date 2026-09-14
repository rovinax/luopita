from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="luopita-cron-fire-")
os.environ["LUOPITA_PROVIDER"] = "mock"
os.environ["LUOPITA_API_KEY"] = ""
os.environ["LUOPITA_DATABASE_URL"] = "memory://"
os.environ["LUOPITA_NAPCAT_ENABLED"] = "false"
os.environ["LUOPITA_IDENTITY_FILE"] = os.path.join(_tmp, "identity.yaml")
os.environ["LUOPITA_PERSON_FILE"] = os.path.join(_tmp, "person.yaml")

from app.runtime import Runtime
from msg.schema import InboundMessage, OutboundMessage


class TestCronFire(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = await Runtime.start()
        self.sent: list[str] = []
        adapter = self.runtime.adapters.get("admin")
        original = adapter.send

        async def capture(message: OutboundMessage):
            self.sent.append(message.text)
            return await original(message)

        adapter.send = capture

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_fire_sends_and_isolates_thread(self):
        orch = self.runtime.orchestrator
        session_id = "admin:console:cron-iso"
        await orch.handle(
            InboundMessage(
                platform="admin",
                channel_type="console",
                chat_id="cron-iso",
                user_id="admin",
                text="hello banana original",
                session_key=session_id,
            ),
            deliver=False,
        )
        job = await self.runtime.cron.add(
            kind="at",
            schedule="1m",
            prompt="提醒喝水",
            platform="admin",
            channel_type="console",
            chat_id="cron-iso",
            user_id="admin",
        )
        reply = await orch.run_cron_turn(job)
        self.assertIn("提醒喝水", reply)
        self.assertTrue(any("提醒喝水" in text for text in self.sent))
        original_state = await orch.owner_graph.aget_state(
            {"configurable": {"thread_id": session_id}}
        )
        original_blob = " ".join(
            str(getattr(message, "content", "") or "")
            for message in list((original_state.values or {}).get("messages") or [])
        )
        self.assertIn("banana", original_blob)
        cron_state = await orch.cron_graph.aget_state(
            {"configurable": {"thread_id": f"cron:{job.id}"}}
        )
        cron_blob = " ".join(
            str(getattr(message, "content", "") or "")
            for message in list((cron_state.values or {}).get("messages") or [])
        )
        self.assertIn("提醒喝水", cron_blob)
        self.assertNotIn("banana", cron_blob)

    async def test_owner_tool_present_user_hidden(self):
        from agent.tools import build_tools
        from core.graph import policy_from_config

        owner_tools = build_tools(
            lambda: policy_from_config(self.runtime.get_config()),
            cron=self.runtime.cron,
            role="owner",
        )
        user_tools = build_tools(
            lambda: policy_from_config(self.runtime.get_config()),
            cron=self.runtime.cron,
            role="user",
        )
        self.assertIn("cron", [tool.name for tool in owner_tools])
        self.assertNotIn("cron", [tool.name for tool in user_tools])

    async def test_owner_tool_add_uses_delivery(self):
        from agent.tools import build_cron_tool
        from core.cron.context import CronDelivery, reset_cron_delivery, set_cron_delivery
        from core.identity import reset_current_role, set_current_role

        tool = build_cron_tool(self.runtime.cron)
        token = set_current_role("owner")
        delivery = set_cron_delivery(
            CronDelivery(platform="admin", channel_type="console", chat_id="admin", user_id="admin")
        )
        try:
            text = await tool.ainvoke(
                {"action": "add", "kind": "at", "schedule": "20m", "prompt": "关窗"}
            )
        finally:
            reset_cron_delivery(delivery)
            reset_current_role(token)
        self.assertIn("记下了", text)
        jobs = await self.runtime.cron.list_jobs(user_id="admin", platform="admin")
        self.assertTrue(any(job.prompt == "关窗" for job in jobs))
