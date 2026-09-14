from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import unittest

from langchain_core.messages import AIMessage

from core.database import InMemoryDatabase
from core.group_context.pipeline import GroupContextPipeline
from core.group_context.profile import (
    ProfileCard,
    card_from_row,
    format_profile_card,
    merge_profile,
    parse_extract_json,
    should_refresh,
)
from core.group_context.redis_client import InMemoryRedis
from core.group_context.store import HotStore
from core.group_context.trigger import decide_trigger


class FakeLLM:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0
        self.messages: list[Any] = []

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls += 1
        self.messages = messages
        return AIMessage(content=self.payload)


class TestProfileCard(unittest.TestCase):
    def test_merge_rules(self):
        old = ProfileCard(
            address="Ada",
            familiarity="peer",
            reply_pref="短",
            stack=["docker"],
            taboos="别说教",
            recent="上周在搞 nginx",
        )
        new = ProfileCard(
            address="",
            familiarity="stranger",
            reply_pref="",
            stack=["postgres", "docker"],
            taboos="当众翻旧账",
            recent="这周 compose 端口",
        )
        merged = merge_profile(old, new)
        self.assertEqual(merged.address, "Ada")
        self.assertEqual(merged.familiarity, "peer")
        self.assertEqual(merged.reply_pref, "短")
        self.assertEqual(merged.stack, ["docker", "postgres"])
        self.assertIn("别说教", merged.taboos)
        self.assertIn("当众翻旧账", merged.taboos)
        self.assertEqual(merged.recent, "这周 compose 端口")
        up = merge_profile(old, ProfileCard(familiarity="familiar"))
        self.assertEqual(up.familiarity, "familiar")

    def test_format_omits_empty_and_shortens_chime(self):
        self.assertEqual(format_profile_card(ProfileCard()), "")
        card = ProfileCard(
            address="Ada",
            familiarity="peer",
            reply_pref="给命令别给教程",
            stack=["postgres"],
            taboos="当众翻旧账",
            recent="compose 端口",
        )
        full = format_profile_card(card)
        self.assertIn("[当前说话人]", full)
        self.assertIn("给命令别给教程", full)
        self.assertIn("postgres", full)
        chime = format_profile_card(card, chime=True)
        self.assertIn("当众翻旧账", chime)
        self.assertNotIn("postgres", chime)
        self.assertNotIn("给命令别给教程", chime)

    def test_parse_extract_json(self):
        card = parse_extract_json(
            """```json
            {"address": "Ada", "familiarity": "peer", "reply_pref": "短", "stack": ["go"], "taboos": "", "recent": ""}
            ```"""
        )
        assert card is not None
        self.assertEqual(card.address, "Ada")
        self.assertEqual(card.stack, ["go"])
        self.assertIsNone(parse_extract_json("not json"))

    def test_should_refresh(self):
        turns = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        card = ProfileCard()
        self.assertFalse(should_refresh(card, turns))
        turns = turns + [{"role": "user", "content": "c"}, {"role": "assistant", "content": "d"}]
        self.assertTrue(should_refresh(ProfileCard(), turns))
        fresh = ProfileCard(updated_at=datetime.now(timezone.utc).isoformat())
        self.assertFalse(should_refresh(fresh, turns, compressed=False))
        self.assertTrue(should_refresh(fresh, turns, compressed=True))
        stale = ProfileCard(updated_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat())
        self.assertTrue(should_refresh(stale, turns))


class TestProfileStore(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_by_chat_and_clear(self):
        db = InMemoryDatabase()
        await db.put_user_profile(
            platform="napcat",
            chat_id="9",
            user_id="1",
            display_name="Ada",
            card={"address": "Ada", "familiarity": "peer", "reply_pref": "短"},
        )
        row = await db.get_user_profile("napcat", "1", "9")
        assert row is not None
        self.assertEqual(card_from_row(row).reply_pref, "短")
        self.assertIsNone(await db.get_user_profile("napcat", "1", "other"))
        await db.clear_user_profile("napcat", "9", "1")
        self.assertIsNone(await db.get_user_profile("napcat", "1", "9"))

    async def test_extract_from_shard_turns_not_group_memory(self):
        db = InMemoryDatabase()
        redis = InMemoryRedis()
        await redis.init()
        hot = HotStore(redis)
        pipeline = GroupContextPipeline(hot, db)
        trigger = decide_trigger(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="@bot docker 起不来",
            mentioned=True,
        )
        prepared = await pipeline.prepare_reply(
            platform="napcat",
            chat_id="9",
            user_id="1",
            sender_name="Ada",
            text="docker 起不来",
            trigger=trigger,
            reply_to_ids=[],
            bot_id="1001",
            bot_name="小Lu",
        )
        assert prepared.route is not None
        shard_id = prepared.route.shard_id
        await pipeline.ingest_background(
            platform="napcat",
            chat_id="9",
            user_id="99",
            sender_name="Eve",
            role="user",
            content="旁听里的八卦不要写进画像",
        )
        pairs = [
            ("user", "我在写 compose"),
            ("assistant", "先看 postgres 日志"),
            ("user", "别写教程，直接给命令"),
            ("assistant", "docker compose logs postgres --tail 50"),
        ]
        for role, content in pairs:
            await pipeline.write_turn(
                platform="napcat",
                chat_id="9",
                user_id="1",
                shard_id=shard_id,
                role=role,
                content=content,
                sender_name="Ada" if role == "user" else "小Lu",
            )
        llm = FakeLLM(
            '{"address":"Ada","familiarity":"peer","reply_pref":"给命令","stack":["docker"],'
            '"taboos":"别写教程","recent":"compose","evidence":"别写教程，直接给命令"}'
        )
        skipped = await pipeline.maybe_refresh_profile(
            platform="napcat",
            chat_id="9",
            user_id="1",
            sender_name="Ada",
            shard_id=shard_id,
            mock=True,
            llm=llm,
        )
        self.assertIsNone(skipped)
        self.assertEqual(llm.calls, 0)
        merged = await pipeline.maybe_refresh_profile(
            platform="napcat",
            chat_id="9",
            user_id="1",
            sender_name="Ada",
            shard_id=shard_id,
            mock=False,
            llm=llm,
        )
        assert merged is not None
        self.assertEqual(llm.calls, 1)
        human = str(llm.messages[-1].content)
        self.assertIn("compose", human)
        self.assertNotIn("旁听里的八卦", human)
        row = await db.get_user_profile("napcat", "1", "9")
        card = card_from_row(row)
        self.assertEqual(card.reply_pref, "给命令")
        self.assertEqual(card.stack, ["docker"])
