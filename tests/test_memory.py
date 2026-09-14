from __future__ import annotations

import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="luopita-memory-")
os.environ["LUOPITA_PROVIDER"] = "mock"
os.environ["LUOPITA_API_KEY"] = ""
os.environ["LUOPITA_DATABASE_URL"] = "memory://"
os.environ["LUOPITA_NAPCAT_ENABLED"] = "false"
os.environ["LUOPITA_IDENTITY_FILE"] = os.path.join(_tmp, "identity.yaml")
os.environ["LUOPITA_PERSON_FILE"] = os.path.join(_tmp, "person.yaml")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.runtime import Runtime
from core.media import prepare_messages_for_llm
from core.memory import (
    MemoryService,
    RECALL_MAX_CHARS,
    RECALL_MAX_ITEMS,
    extract_memory_facts,
    should_remember_turn,
)
from core.window import (
    compact_dropped,
    drop_trailing_extra_humans,
    tool_rounds_since_last_human,
    trim_short_term,
)
from msg.schema import InboundMessage


def _pairs(n: int) -> list:
    msgs = []
    for i in range(n):
        msgs.append(HumanMessage(content=f"human-{i}", id=f"h{i}"))
        msgs.append(AIMessage(content=f"ai-{i}", id=f"a{i}"))
    return msgs


def _tool_tail() -> list:
    return [
        HumanMessage(content="old", id="h0"),
        AIMessage(content="old a", id="a0"),
        HumanMessage(content="tool please", id="ht"),
        AIMessage(
            content="",
            id="at",
            tool_calls=[
                {
                    "name": "run_shell",
                    "args": {"command": "ls"},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="ok", tool_call_id="c1", id="t1"),
        AIMessage(content="done", id="af"),
    ]


class TestTrimShortTerm(unittest.TestCase):
    def test_thirty_turns_fit_window_and_start_on_human(self):
        keep, dropped = trim_short_term(_pairs(30), max_messages=16)
        self.assertLessEqual(len(keep), 16)
        self.assertGreaterEqual(len(dropped), 44)
        self.assertEqual(getattr(keep[0], "type", ""), "human")
        self.assertEqual(keep[0].content, "human-22")
        self.assertEqual(keep[-1].content, "ai-29")

    def test_tool_pair_is_not_orphaned(self):
        keep, dropped = trim_short_term(_tool_tail(), max_messages=3)
        types = [getattr(m, "type", "") for m in keep]
        self.assertNotEqual(types[0], "tool")
        self.assertEqual(getattr(keep[0], "type", ""), "human")
        self.assertIn("tool", types)
        ai_with_calls = [m for m in keep if getattr(m, "tool_calls", None)]
        self.assertTrue(ai_with_calls)
        self.assertTrue(any(getattr(m, "tool_call_id", "") == "c1" for m in keep))
        self.assertEqual(dropped[0].content, "old")

    def test_overflow_skips_leading_ai_instead_of_growing(self):
        msgs = _pairs(8) + [HumanMessage(content="next", id="h8")]
        keep, dropped = trim_short_term(msgs, max_messages=16)
        self.assertLessEqual(len(keep), 16)
        self.assertEqual(getattr(keep[0], "type", ""), "human")
        self.assertTrue(dropped)
        self.assertEqual(dropped[0].content, "human-0")


class TestToolRounds(unittest.TestCase):
    def test_counts_tool_ais_since_last_human(self):
        msgs = [
            HumanMessage(content="go", id="h0"),
            AIMessage(
                content="",
                id="a0",
                tool_calls=[
                    {
                        "name": "run_shell",
                        "args": {"command": "ls"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content="ok", tool_call_id="c1", id="t1"),
            AIMessage(
                content="",
                id="a1",
                tool_calls=[
                    {
                        "name": "run_shell",
                        "args": {"command": "pwd"},
                        "id": "c2",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content="ok", tool_call_id="c2", id="t2"),
        ]
        self.assertEqual(tool_rounds_since_last_human(msgs), 2)
        msgs.append(HumanMessage(content="next", id="h1"))
        self.assertEqual(tool_rounds_since_last_human(msgs), 0)


class TestDropTrailingExtraHumans(unittest.TestCase):
    def test_keeps_only_current_human_after_parked(self):
        msgs = [
            HumanMessage(content="hello everyone", id="h0"),
            HumanMessage(content="[@1001] 帮看下刚才那个", id="h1"),
        ]
        keep, parked = drop_trailing_extra_humans(msgs)
        self.assertEqual([m.content for m in keep], ["[@1001] 帮看下刚才那个"])
        self.assertEqual([m.content for m in parked], ["hello everyone"])

    def test_leaves_single_human_and_pairs_alone(self):
        single = [HumanMessage(content="only", id="h")]
        self.assertEqual(drop_trailing_extra_humans(single), (single, []))
        paired = _pairs(2) + [HumanMessage(content="now", id="hn")]
        keep, parked = drop_trailing_extra_humans(paired)
        self.assertEqual(parked, [])
        self.assertEqual(keep[-1].content, "now")
        self.assertEqual(len(keep), len(paired))


class TestRememberFilter(unittest.TestCase):
    def test_skips_filler_and_silence(self):
        self.assertFalse(should_remember_turn("哈哈", "嗯"))
        self.assertFalse(should_remember_turn("在吗", "在"))
        self.assertFalse(should_remember_turn("你好", "[SILENCE]"))
        self.assertFalse(should_remember_turn("[表情包]", "哈哈"))

    def test_keeps_commitment_and_preference(self):
        self.assertTrue(should_remember_turn("明天提醒我交周报", "好，明天叫你"))
        self.assertTrue(should_remember_turn("别用客服腔回我", "行，记下了"))
        self.assertTrue(should_remember_turn("luopita 那个 docker 端口还在修", "多半 5170 占了"))

    def test_extract_facts_prefers_short_lines(self):
        facts = extract_memory_facts(
            "别列步骤，明天提醒我交周报，项目叫 luopita",
            "好，我记下了",
        )
        self.assertTrue(facts)
        self.assertTrue(any("周报" in item or "提醒" in item for item in facts))
        self.assertTrue(any("步骤" in item or "偏好" in item or "别" in item for item in facts))


class TestArchiveAndRecall(unittest.IsolatedAsyncioTestCase):
    async def test_dropped_archive_is_recallable_and_capped(self):
        from core.database import InMemoryDatabase

        db = InMemoryDatabase()
        memory = MemoryService(db)
        dropped = [
            HumanMessage(content="蓝莓蛋糕配方"),
            AIMessage(content="烤箱 180"),
            HumanMessage(content="蓝莓蛋糕配方"),
        ]
        snippet = await memory.archive_dropped("u1", dropped)
        self.assertTrue(snippet.startswith("更早聊过："))
        self.assertEqual(snippet.count("蓝莓蛋糕配方"), 1)
        recalled = await memory.recall("u1", "蓝莓")
        self.assertTrue(any("更早聊过" in item and "蓝莓" in item for item in recalled))
        self.assertEqual(len(db.memories["u1"]), 1)

        for i in range(12):
            await db.put_memory("u1", f"memory-item-{i}-" + ("x" * 200))
        recalled = await memory.recall("u1", "memory-item", limit=20)
        self.assertLessEqual(len(recalled), RECALL_MAX_ITEMS)
        formatted = memory.format_for_prompt(recalled + ["y" * 500] * 8)
        self.assertLessEqual(len(formatted), RECALL_MAX_CHARS + 80)
        self.assertLessEqual(formatted.count("\n- "), RECALL_MAX_ITEMS)

    async def test_remember_turn_writes_filtered_facts(self):
        from core.database import InMemoryDatabase

        db = InMemoryDatabase()
        memory = MemoryService(db)
        await memory.remember_turn("u1", "哈哈", "嗯")
        self.assertEqual(db.memories.get("u1", []), [])
        await memory.remember_turn("u1", "明天提醒我交周报", "好，明天叫你")
        rows = db.memories["u1"]
        self.assertEqual(len(rows), 1)
        self.assertIn("周报", rows[0]["content"])

    def test_compact_empty_without_human(self):
        self.assertEqual(compact_dropped([AIMessage(content="only ai")]), "")


class TestPrepareWindowMedia(unittest.TestCase):
    def test_old_file_blocks_only_on_last_human(self):
        old = HumanMessage(
            content=[{"type": "text", "text": "旧图"}, {"type": "file", "file": {"file_id": "file-api-old"}}]
        )
        mid = AIMessage(content="ok")
        new = HumanMessage(
            content=[{"type": "text", "text": "新图"}, {"type": "file", "file_id": "file-api-new"}]
        )
        prepared = prepare_messages_for_llm([old, mid, new])
        self.assertEqual(prepared[0].content, "旧图\n[图片]")
        self.assertEqual(prepared[2].content[1], {"type": "file", "file_id": "file-api-new"})


class TestCheckpointWindow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = await Runtime.start()
        self.runtime.config.agent.short_term_messages = 8

    async def asyncTearDown(self):
        await self.runtime.close()

    async def _chat(self, *, platform: str, user_id: str, text: str, chat_id: str = ""):
        return await self.runtime.orchestrator.handle(
            InboundMessage(
                platform=platform,  # type: ignore[arg-type]
                channel_type="private",
                chat_id=chat_id or user_id,
                user_id=user_id,
                text=text,
            ),
            deliver=False,
        )

    async def test_owner_remembers_meaningful_turns_and_archives_overflow(self):
        calls: list[str] = []
        original = self.runtime.db.put_memory

        async def wrapped(user_id: str, content: str, embedding=None) -> None:
            calls.append(content)
            try:
                await original(user_id, content, embedding=embedding)
            except TypeError:
                await original(user_id, content)

        self.runtime.db.put_memory = wrapped  # type: ignore[method-assign]
        await self._chat(platform="admin", user_id="admin", text="哈哈")
        filler_calls = list(calls)
        self.assertEqual(filler_calls, [])
        await self._chat(platform="admin", user_id="admin", text="明天提醒我交周报 pineapple")
        self.assertTrue(any("周报" in item or "提醒" in item for item in calls))
        intent_count = len(calls)
        for i in range(12):
            await self._chat(platform="admin", user_id="admin", text=f"later-{i} pineapple 继续修 luopita")
        self.assertGreater(len(calls), intent_count)
        self.assertTrue(any(item.startswith("更早聊过：") for item in calls))
        recalled = await self.runtime.memory.recall("admin", "周报")
        self.assertTrue(any("周报" in item or "提醒" in item for item in recalled))

    async def test_checkpoint_stops_growing(self):
        session_id = ""
        for i in range(28):
            resp = await self._chat(platform="admin", user_id="admin", text=f"turn-{i} checkpoint")
            session_id = resp.session_id
        state = await self.runtime.owner_graph.aget_state({"configurable": {"thread_id": session_id}})
        graph_messages = list((state.values or {}).get("messages") or [])
        self.assertLessEqual(len(graph_messages), 12)
        self.assertEqual(getattr(graph_messages[0], "type", ""), "human")
        stored = await self.runtime.db.load_messages(session_id, limit=200)
        self.assertGreaterEqual(len(stored), 50)

    async def test_user_role_does_not_archive(self):
        for i in range(12):
            await self._chat(platform="napcat", user_id="888", text=f"stranger-{i}")
        self.assertEqual(self.runtime.db.memories.get("888", []), [])
