from __future__ import annotations

import unittest

from core.group_context.compress import cheap_summary, needs_compression, turns_char_count
from core.group_context.entity import resolve_pronouns
from core.group_context.keys import cosine_similarity, hash_embed
from core.group_context.pipeline import GroupContextPipeline
from core.group_context.redis_client import InMemoryRedis
from core.group_context.store import HotStore
from core.group_context.trigger import decide_trigger, has_command_prefix
from core.database import InMemoryDatabase


class TestTrigger(unittest.TestCase):
    def test_command_prefix(self):
        self.assertTrue(has_command_prefix("/help"))
        self.assertFalse(has_command_prefix("!ping"))
        self.assertFalse(has_command_prefix("。"))
        self.assertFalse(has_command_prefix("..."))
        self.assertFalse(has_command_prefix("hello"))

    def test_slash_command_owner_only(self):
        owner = decide_trigger(
            channel_type="group",
            role="owner",
            group_require_at=True,
            text="/help",
            command=True,
        )
        self.assertEqual(owner.mode, "direct")
        self.assertTrue(owner.explicit)
        user = decide_trigger(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="/help",
            command=True,
        )
        self.assertEqual(user.mode, "ignore")
        self.assertFalse(user.explicit)

    def test_owner_slash_after_reply_mark(self):
        from core.group_context.trigger import extract_trigger_flags

        mentioned, named, command = extract_trigger_flags(
            text="[回复:1][@1001] /status",
            at_user_ids=["1001"],
            bot_id="1001",
            wake_keywords=["小Lu"],
            persona_name="小Lu",
        )
        self.assertTrue(command)
        self.assertTrue(mentioned)

    def test_reply_bot_is_explicit(self):
        result = decide_trigger(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="继续",
            replied_bot=True,
        )
        self.assertEqual(result.mode, "direct")
        self.assertTrue(result.explicit)

    def test_filler_ignored(self):
        result = decide_trigger(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="哈哈",
        )
        self.assertEqual(result.mode, "ignore")


class TestEntity(unittest.TestCase):
    def test_pronoun_binds_recent_speaker(self):
        text, cands = resolve_pronouns(
            "他刚才说的方案可行吗？",
            [{"user_id": "2", "name": "李四"}, {"user_id": "1", "name": "张三"}],
            current_user_id="1",
        )
        self.assertIn("李四|2", text)
        self.assertEqual(cands[0]["user_id"], "2")


class TestCompress(unittest.TestCase):
    def test_needs_compression_threshold(self):
        turns = [{"role": "user", "sender_name": f"u{i}", "user_id": str(i), "content": "x" * 200} for i in range(20)]
        self.assertTrue(needs_compression(turns, threshold=500))
        self.assertGreater(turns_char_count(turns), 500)
        self.assertTrue(cheap_summary("a b c").startswith("更早对话"))


class TestPipeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = InMemoryDatabase()
        self.redis = InMemoryRedis()
        await self.redis.init()
        self.hot = HotStore(self.redis)
        self.pipeline = GroupContextPipeline(self.hot, self.db)

    async def test_ignore_still_writes_group_memory(self):
        await self.pipeline.ingest_background(
            platform="napcat",
            chat_id="9",
            user_id="1",
            sender_name="Ada",
            role="user",
            content="哈哈",
            message_id="m1",
        )
        mem = await self.hot.load_group_mem(platform="napcat", chat_id="9", limit=5)
        self.assertEqual(len(mem), 1)
        events = await self.db.load_group_recent("napcat", "9")
        self.assertEqual(len(events), 1)

    async def test_users_get_isolated_shards(self):
        t_a = decide_trigger(
            channel_type="group", role="user", group_require_at=True, text="@bot hi", mentioned=True
        )
        t_b = decide_trigger(
            channel_type="group", role="user", group_require_at=True, text="@bot yo", mentioned=True
        )
        a = await self.pipeline.prepare_reply(
            platform="napcat",
            chat_id="9",
            user_id="1",
            sender_name="Ada",
            text="docker 起不来",
            trigger=t_a,
            reply_to_ids=[],
            bot_id="1001",
            bot_name="小Lu",
        )
        b = await self.pipeline.prepare_reply(
            platform="napcat",
            chat_id="9",
            user_id="2",
            sender_name="Bob",
            text="react 怎么配",
            trigger=t_b,
            reply_to_ids=[],
            bot_id="1001",
            bot_name="小Lu",
        )
        self.assertIsNotNone(a.route)
        self.assertIsNotNone(b.route)
        self.assertNotEqual(a.route.shard_id, b.route.shard_id)
        await self.pipeline.write_turn(
            platform="napcat",
            chat_id="9",
            user_id="1",
            shard_id=a.route.shard_id,
            role="user",
            content="docker 起不来",
            sender_name="Ada",
            message_id="a1",
        )
        await self.pipeline.write_turn(
            platform="napcat",
            chat_id="9",
            user_id="2",
            shard_id=b.route.shard_id,
            role="user",
            content="react 怎么配",
            sender_name="Bob",
            message_id="b1",
        )
        turns_a = await self.hot.load_turns(
            platform="napcat", chat_id="9", user_id="1", shard_id=a.route.shard_id
        )
        turns_b = await self.hot.load_turns(
            platform="napcat", chat_id="9", user_id="2", shard_id=b.route.shard_id
        )
        self.assertEqual(len(turns_a), 1)
        self.assertIn("docker", turns_a[0]["content"])
        self.assertNotIn("react", turns_a[0]["content"])
        self.assertIn("react", turns_b[0]["content"])

    async def test_reply_chain_reuses_shard(self):
        t = decide_trigger(
            channel_type="group", role="user", group_require_at=True, text="帮我看下", mentioned=True
        )
        first = await self.pipeline.prepare_reply(
            platform="napcat",
            chat_id="9",
            user_id="1",
            sender_name="Ada",
            text="帮我看下",
            trigger=t,
            reply_to_ids=[],
            bot_id="1001",
            bot_name="小Lu",
        )
        assert first.route is not None
        await self.pipeline.write_turn(
            platform="napcat",
            chat_id="9",
            user_id="1",
            shard_id=first.route.shard_id,
            role="assistant",
            content="先看 logs",
            sender_name="小Lu",
            message_id="bot-1",
        )
        reply_trigger = decide_trigger(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="然后呢",
            replied_bot=True,
        )
        second = await self.pipeline.prepare_reply(
            platform="napcat",
            chat_id="9",
            user_id="1",
            sender_name="Ada",
            text="然后呢",
            trigger=reply_trigger,
            reply_to_ids=["bot-1"],
            bot_id="1001",
            bot_name="小Lu",
        )
        self.assertEqual(second.route.shard_id, first.route.shard_id)
        self.assertEqual(second.route.reason, "reply_chain")

    async def test_reply_foreign_shard_opens_new(self):
        t = decide_trigger(
            channel_type="group", role="user", group_require_at=True, text="帮我看下", mentioned=True
        )
        first = await self.pipeline.prepare_reply(
            platform="napcat",
            chat_id="9",
            user_id="1",
            sender_name="Ada",
            text="另有其人是什么意思",
            trigger=t,
            reply_to_ids=[],
            bot_id="1001",
            bot_name="小Lu",
        )
        assert first.route is not None
        await self.pipeline.write_turn(
            platform="napcat",
            chat_id="9",
            user_id="1",
            shard_id=first.route.shard_id,
            role="assistant",
            content="大概是说别人",
            sender_name="小Lu",
            message_id="bot-ada-1",
        )
        reply_trigger = decide_trigger(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="小Lu",
            replied_bot=True,
            mentioned=True,
        )
        second = await self.pipeline.prepare_reply(
            platform="napcat",
            chat_id="9",
            user_id="2",
            sender_name="Bob",
            text="小Lu",
            trigger=reply_trigger,
            reply_to_ids=["bot-ada-1"],
            bot_id="1001",
            bot_name="小Lu",
            bare_wake=True,
        )
        assert second.route is not None
        self.assertNotEqual(second.route.shard_id, first.route.shard_id)
        self.assertEqual(second.route.reason, "reply_foreign_shard")
        self.assertTrue(second.route.is_new)
        block = second.assembled.as_group_context_block()
        self.assertIn("[说话人隔离]", block)
        self.assertIn("[裸唤醒]", block)

    async def test_assembled_prompt_sections(self):
        await self.db.put_user_profile(
            platform="napcat", user_id="1", display_name="Ada", preferences="简洁"
        )
        t = decide_trigger(
            channel_type="group", role="user", group_require_at=True, text="@bot 他刚才说的可行吗", mentioned=True
        )
        await self.hot.push_entity("napcat", "9", user_id="2", name="Bob")
        prepared = await self.pipeline.prepare_reply(
            platform="napcat",
            chat_id="9",
            user_id="1",
            sender_name="Ada",
            text="他刚才说的可行吗",
            trigger=t,
            reply_to_ids=[],
            bot_id="1001",
            bot_name="小Lu",
        )
        block = prepared.assembled.as_group_context_block()
        self.assertIn("[说话人隔离]", block)
        self.assertIn("禁止主动续答", block)
        self.assertIn("[用户画像]", block)
        self.assertIn("简洁", block)
        self.assertIn("[当前消息]", block)
        self.assertIn("Bob|2", prepared.assembled.resolved_text)
        self.assertNotIn("[裸唤醒]", block)

    async def test_embedding_similarity_stable(self):
        a = hash_embed("docker compose 起不来 postgres")
        b = hash_embed("docker compose 起不来 postgres")
        c = hash_embed("今天天气怎么样")
        self.assertAlmostEqual(cosine_similarity(a, b), 1.0, places=5)
        self.assertGreater(cosine_similarity(a, b), cosine_similarity(a, c))


if __name__ == "__main__":
    unittest.main()
