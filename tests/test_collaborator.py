from __future__ import annotations

import unittest
from datetime import timedelta

from core.clock import now_shanghai
from core.commitments.extract import due_from_text, extract_commitment_ops
from core.commitments.service import CommitmentService
from core.commitments.types import utc_iso
from core.database import InMemoryDatabase
from core.group_talk import decide_group_reply, score_group_reply
from core.identity import compose_system_prompt
from core.reflect import extract_reflect_notes, maybe_reflect_turn, should_reflect
from core.scratchpad import format_scratchpad_block, touch_scratchpad_after_turn
from core.memory import MemoryService
from utils.config import PersonaSettings
from utils.log import ChatbotLogger


class TestCommitmentExtract(unittest.TestCase):
    def test_add_and_cancel(self):
        adds = extract_commitment_ops("明天提醒我交周报")
        self.assertEqual(len(adds), 1)
        self.assertEqual(adds[0].action, "add")
        self.assertTrue(adds[0].due_at)
        self.assertIn("周报", adds[0].text)

        cancels = extract_commitment_ops("算了不用提醒了")
        self.assertEqual(cancels[0].action, "cancel")

    def test_due_from_minutes(self):
        due = due_from_text("10分钟后提醒我喝水")
        self.assertTrue(due)


class TestCommitmentService(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_and_due(self):
        db = InMemoryDatabase()
        svc = CommitmentService(db, ChatbotLogger())
        past = utc_iso(now_shanghai() - timedelta(minutes=1))
        item = await svc.add(
            text="交周报",
            due_at=past,
            platform="admin",
            channel_type="private",
            chat_id="admin",
            user_id="admin",
            evidence="明天提醒我交周报",
        )
        due = await svc.due_open()
        self.assertEqual(due[0].id, item.id)
        self.assertTrue(svc.should_notify(due[0]))
        await svc.mark_notified(item.id)
        self.assertFalse(svc.should_notify((await svc.list_open(user_id="admin"))[0]))

    async def test_ingest_turn_add_cancel(self):
        db = InMemoryDatabase()
        svc = CommitmentService(db, ChatbotLogger())
        added = await svc.ingest_turn(
            user_text="明天提醒我交周报",
            platform="admin",
            channel_type="private",
            chat_id="admin",
            user_id="admin",
            source_session="s1",
        )
        self.assertEqual(len(added), 1)
        closed = await svc.ingest_turn(
            user_text="算了不用提醒了",
            platform="admin",
            channel_type="private",
            chat_id="admin",
            user_id="admin",
        )
        self.assertTrue(closed)
        self.assertEqual(await svc.list_open(user_id="admin"), [])


class TestChimeUtility(unittest.TestCase):
    def test_tech_still_chimes_at_default(self):
        mode = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="docker 起不来报错了怎么修",
        )
        self.assertEqual(mode, "chime")
        score, reason = score_group_reply(text="docker 起不来报错了怎么修")
        self.assertGreaterEqual(score, 0.55)
        self.assertEqual(reason, "tech")

    def test_chatty_ignored_by_default(self):
        mode = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="今天下午把那份方案再对一下细节吧",
        )
        self.assertEqual(mode, "ignore")

    def test_engaged_same_speaker(self):
        mode = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="还是不行，日志在这",
            engaged=True,
            same_speaker=True,
            can_open=False,
            replies_left=2,
        )
        self.assertEqual(mode, "chime")


class TestScratchpadAndReflect(unittest.IsolatedAsyncioTestCase):
    async def test_scratchpad_block(self):
        db = InMemoryDatabase()
        await touch_scratchpad_after_turn(
            db,
            platform="admin",
            user_id="admin",
            user_text="修 luopita 端口",
            open_summary="- 交周报",
            reflect_notes="避免：别列步骤",
        )
        row = await db.get_scratchpad("admin", "admin")
        block = format_scratchpad_block(row)
        self.assertIn("[当前工作态]", block)
        self.assertIn("交周报", block)
        prompt = compose_system_prompt(
            PersonaSettings(),
            "owner",
            in_group=False,
            scratchpad=block,
        )
        self.assertIn("当前工作态", prompt)

    async def test_reflect_writes_strategy(self):
        self.assertTrue(should_reflect("别列步骤回我"))
        notes = extract_reflect_notes("别列步骤回我")
        self.assertIn("步骤", notes["avoid"])
        db = InMemoryDatabase()
        memory = MemoryService(db)
        line = await maybe_reflect_turn(memory, user_id="admin", user_text="别列步骤回我")
        self.assertTrue(line)
        self.assertTrue(any("策略：" in r["content"] for r in db.memories["admin"]))


if __name__ == "__main__":
    unittest.main()
