from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from core.clock import SHANGHAI
from core.identity import (
    OWNER_REFUSAL,
    IdentityStore,
    compose_system_prompt,
    current_role,
    default_session_key,
    mentioned_bot,
    reset_current_role,
    set_current_role,
    should_ignore_inbound,
)
from utils.config import IdentitySettings, OwnerEntry, PersonaSettings


class TestIdentityStore(unittest.TestCase):
    def test_roundtrip_and_builtin_owners(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.yaml"
            store = IdentityStore(path)
            store.save(
                IdentitySettings(
                    owners=[OwnerEntry(platform="napcat", user_id="123456", nickname="rovina")],
                    group_require_at=True,
                )
            )
            loaded = IdentityStore(path)
            settings = loaded.load()
            keys = {(o.platform, o.user_id) for o in settings.owners}
            self.assertIn(("napcat", "123456"), keys)
            self.assertIn(("admin", "admin"), keys)
            self.assertIn(("tui", "tui"), keys)
            self.assertTrue(loaded.is_owner("napcat", "123456"))
            self.assertFalse(loaded.is_owner("napcat", "999"))
            self.assertEqual(loaded.resolve_role("admin", "admin"), "owner")
            self.assertEqual(loaded.resolve_role("napcat", "42"), "user")
            self.assertEqual(settings.group_engage_sec, 90)
            self.assertEqual(settings.group_engage_replies, 2)

    def test_save_cannot_drop_console_owners(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.yaml"
            store = IdentityStore(path)
            saved = store.save(IdentitySettings(owners=[], group_require_at=False))
            keys = {(o.platform, o.user_id) for o in saved.owners}
            self.assertEqual(keys, {("admin", "admin"), ("tui", "tui")})
            self.assertFalse(saved.group_require_at)


class TestGroupGating(unittest.TestCase):
    def test_session_key_includes_group_user(self):
        self.assertEqual(default_session_key("napcat", "group", "9", "3"), "napcat:group:9:3")
        self.assertEqual(default_session_key("napcat", "private", "42", "42"), "napcat:private:42")

    def test_ignore_group_without_at(self):
        self.assertTrue(
            should_ignore_inbound(
                channel_type="group",
                role="user",
                group_require_at=True,
                at_user_ids=[],
                bot_id="1001",
            )
        )
        self.assertFalse(
            should_ignore_inbound(
                channel_type="group",
                role="user",
                group_require_at=True,
                at_user_ids=["1001"],
                bot_id="1001",
            )
        )
        self.assertTrue(
            should_ignore_inbound(
                channel_type="group",
                role="owner",
                group_require_at=True,
                at_user_ids=[],
                bot_id="1001",
            )
        )
        self.assertFalse(
            should_ignore_inbound(
                channel_type="group",
                role="owner",
                group_require_at=True,
                at_user_ids=[],
                bot_id="1001",
                text="小lu 帮我看下",
                rng=lambda: 1.0,
            )
        )
        self.assertFalse(
            should_ignore_inbound(
                channel_type="private",
                role="user",
                group_require_at=True,
                at_user_ids=[],
                bot_id="1001",
            )
        )
        self.assertTrue(mentioned_bot(["all"], "1001"))

    def test_keyword_and_tech_not_ignored(self):
        self.assertFalse(
            should_ignore_inbound(
                channel_type="group",
                role="user",
                group_require_at=True,
                at_user_ids=[],
                bot_id="1001",
                text="小lu 来一下",
            )
        )
        self.assertFalse(
            should_ignore_inbound(
                channel_type="group",
                role="user",
                group_require_at=True,
                at_user_ids=[],
                bot_id="1001",
                text="docker 起不来报错了怎么修",
            )
        )
        self.assertTrue(
            should_ignore_inbound(
                channel_type="group",
                role="user",
                group_require_at=True,
                at_user_ids=[],
                bot_id="1001",
                text="hello everyone",
            )
        )
        self.assertTrue(
            should_ignore_inbound(
                channel_type="group",
                role="user",
                group_require_at=True,
                at_user_ids=[],
                bot_id="1001",
                text="今天下午把那份方案再对一下细节吧",
            )
        )

    def test_thread_follow_up_not_lottery(self):
        from core.group_talk import decide_group_reply

        chatty = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="今天下午把那份方案再对一下细节吧",
        )
        self.assertEqual(chatty, "ignore")
        question = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="这端口被占了该怎么修？",
        )
        self.assertEqual(question, "chime")
        tech = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="docker 起不来报错了怎么修",
        )
        self.assertEqual(tech, "chime")
        blocked_open = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="docker 起不来报错了怎么修",
            can_open=False,
        )
        self.assertEqual(blocked_open, "ignore")
        follow = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="还是不行，日志在这",
            engaged=True,
            same_speaker=True,
            can_open=False,
            replies_left=2,
        )
        self.assertEqual(follow, "chime")
        bystander = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="晚上吃饭不",
            engaged=True,
            same_speaker=False,
            can_open=False,
            replies_left=2,
        )
        self.assertEqual(bystander, "ignore")
        exhausted = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="还是不行，日志在这",
            engaged=True,
            same_speaker=True,
            replies_left=0,
        )
        self.assertEqual(exhausted, "ignore")
        still_at = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            mentioned=True,
            text="还是不行，日志在这",
            engaged=True,
            replies_left=0,
        )
        self.assertEqual(still_at, "direct")

    def test_owner_is_not_always_replied(self):
        from core.group_talk import decide_group_reply

        skipped = decide_group_reply(
            channel_type="group",
            role="owner",
            group_require_at=True,
            text="哈哈",
        )
        self.assertEqual(skipped, "ignore")
        considered = decide_group_reply(
            channel_type="group",
            role="owner",
            group_require_at=True,
            text="今天下午把那份方案再对一下细节吧",
        )
        self.assertEqual(considered, "ignore")
        named = decide_group_reply(
            channel_type="group",
            role="owner",
            group_require_at=True,
            named=True,
            text="今天下午把那份方案再对一下细节吧",
        )
        self.assertEqual(named, "direct")
        mentioned = decide_group_reply(
            channel_type="group",
            role="owner",
            group_require_at=True,
            mentioned=True,
            text="今天下午把那份方案再对一下细节吧",
        )
        self.assertEqual(mentioned, "direct")
        tech_cooled = decide_group_reply(
            channel_type="group",
            role="owner",
            group_require_at=True,
            text="docker 起不来报错了怎么修",
            can_open=False,
        )
        self.assertEqual(tech_cooled, "ignore")


class TestVoiceAndRole(unittest.TestCase):
    def test_owner_and_user_prompts_differ(self):
        persona = PersonaSettings(name="Luopita", owner_address="rovina", relationship="一起写代码")
        owner = compose_system_prompt(persona, "owner", in_group=True, owner_nickname="rovina")
        user = compose_system_prompt(persona, "user", in_group=True)
        frozen = datetime(2026, 9, 12, 14, 41, tzinfo=SHANGHAI)
        timed = compose_system_prompt(persona, "user", in_group=False, now=frozen)
        self.assertIn("现在是北京时间 2026-09-12 星期六 14:41（UTC+8）", timed)
        self.assertIn("不要猜", timed)
        self.assertIn("北京时间", owner)
        self.assertIn("不要猜", owner)
        self.assertIn("北京时间", user)
        self.assertIn("不要猜", user)
        self.assertIn("主人", owner)
        self.assertIn("群聊", owner)
        self.assertIn("最近群聊", owner)
        self.assertIn("禁止续答其他群友", owner)
        self.assertIn("接得上当前说话人这句", owner)
        self.assertIn("看图", owner)
        self.assertIn("能读懂在表达什么", owner)
        self.assertIn("不要围着它展开", owner)
        self.assertIn("一层意思", owner)
        self.assertIn("run_shell", owner)
        self.assertIn("普通用户", user)
        self.assertIn("这我做不了", user)
        self.assertIn("最近群聊", user)
        self.assertIn("禁止续答其他群友", user)
        bare = compose_system_prompt(persona, "user", in_group=True, bare_wake=True)
        self.assertIn("开场或问一句意图", bare)
        self.assertIn("不要从旁人未完话题里找题答", bare)
        chime = compose_system_prompt(persona, "owner", in_group=True, chime_in=True)
        self.assertIn("[SILENCE]", chime)
        self.assertIn("默认只输出", chime)
        self.assertIn("不要当成这轮题目", chime)
        staying = compose_system_prompt(persona, "owner", in_group=True, chime_in=True, engaged=True)
        self.assertIn("已经在这场讨论里", staying)
        self.assertIn("不要每句都回", staying)
        self.assertIn("当前要回最后这句", owner)
        self.assertNotIn("不要只盯最后一句", owner)
        private = compose_system_prompt(
            persona, "owner", in_group=False, allowed_commands=["ls", "curl"]
        )
        self.assertIn("---", private)
        self.assertIn("curl", private)
        self.assertIn("必须再跑一次", private)
        self.assertIn("不是 bash", private)
        self.assertIn("不能用管道", private)
        self.assertIn("/help", private)

    def test_role_contextvar(self):
        token = set_current_role("owner")
        try:
            self.assertEqual(current_role(), "owner")
        finally:
            reset_current_role(token)
        self.assertEqual(current_role(), "user")


class TestBubbles(unittest.TestCase):
    def test_split_on_dashes(self):
        from core.bubbles import split_reply_bubbles

        parts = split_reply_bubbles("先看日志\n---\n多半是端口被占了")
        self.assertEqual(parts, ["先看日志", "多半是端口被占了"])

    def test_short_paragraphs_become_bubbles(self):
        from core.bubbles import split_reply_bubbles

        parts = split_reply_bubbles("诶这报错眼熟\n\n把 docker logs 甩我一下")
        self.assertEqual(len(parts), 2)

    def test_long_block_splits_on_blank_line(self):
        from core.bubbles import split_reply_bubbles

        text = "这是很长的一段说明，" * 8 + "\n\n" + "后面还有很长的第二段，" * 8
        parts = split_reply_bubbles(text)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].startswith("这是很长的一段说明"))
        self.assertTrue(parts[1].startswith("后面还有很长的第二段"))

    def test_code_fence_blank_lines_stay_one(self):
        from core.bubbles import split_reply_bubbles

        text = "看这段\n\n```\nline1\n\nline2\n```"
        parts = split_reply_bubbles(text)
        self.assertEqual(parts[0], "看这段")
        self.assertIn("```", parts[1])
        self.assertIn("line2", parts[1])

    def test_too_many_paragraphs_cap(self):
        from core.bubbles import split_reply_bubbles

        text = "一\n\n二\n\n三\n\n四\n\n五"
        parts = split_reply_bubbles(text, max_bubbles=3)
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], "一")
        self.assertIn("四", parts[2])
        self.assertIn("五", parts[2])

    def test_single_newlines_become_bubbles(self):
        from core.bubbles import split_reply_bubbles

        text = (
            "那不叫锐评，这叫没词了硬撑\n"
            "你们一个接一个甩图，我总不能全回「好看好看」，只能现场编\n"
            "它是在指责我吗"
        )
        parts = split_reply_bubbles(text)
        self.assertEqual(
            parts,
            [
                "那不叫锐评，这叫没词了硬撑",
                "你们一个接一个甩图，我总不能全回「好看好看」，只能现场编",
                "它是在指责我吗",
            ],
        )

    def test_chime_cap_still_splits(self):
        from core.bubbles import split_reply_bubbles

        text = "那不叫锐评，这叫没词了硬撑\n\n只能现场编\n\n它是在指责我吗"
        parts = split_reply_bubbles(text, max_bubbles=3)
        self.assertEqual(len(parts), 3)


class TestToolDeny(unittest.IsolatedAsyncioTestCase):
    async def test_user_cannot_run_shell_or_qq_write(self):
        from agent.napcat_tools import build_napcat_tools
        from agent.tools import build_tools
        from core.agent_runtime import CommandPolicy
        from interface.platform.napcat import NapcatAdapter
        from utils.config import NapcatSettings

        token = set_current_role("user")
        try:
            tools = {t.name: t for t in build_tools(lambda: CommandPolicy(allow={"ls", "curl"}, workdir=".", timeout_sec=5), role="owner")}
            self.assertEqual(tools["run_shell"].invoke({"command": "ls"}), OWNER_REFUSAL)
            qq = {t.name: t for t in build_napcat_tools(NapcatAdapter(NapcatSettings(enabled=True)))}
            result = await qq["qq_set_group_kick"].ainvoke({"group_id": "1", "user_id": "2"})
            self.assertEqual(result, OWNER_REFUSAL)
        finally:
            reset_current_role(token)

    def test_run_shell_description_lists_allowlist(self):
        from agent.tools import build_tools
        from core.agent_runtime import CommandPolicy

        tools = {
            t.name: t
            for t in build_tools(
                lambda: CommandPolicy(allow={"ls", "curl"}, workdir=".", timeout_sec=5),
                role="owner",
            )
        }
        self.assertIn("curl", tools["run_shell"].description)
        self.assertIn("not bash", tools["run_shell"].description)
        self.assertIn("no pipes", tools["run_shell"].description)

    async def test_user_graph_has_no_dangerous_tools(self):
        from agent.tools import build_tools
        from core.agent_runtime import CommandPolicy

        names = {t.name for t in build_tools(lambda: CommandPolicy(allow={"ls"}, workdir=".", timeout_sec=5), role="user")}
        self.assertEqual(names, {"get_current_date"})
        tools = {
            t.name: t
            for t in build_tools(lambda: CommandPolicy(allow={"ls"}, workdir=".", timeout_sec=5), role="user")
        }
        clock = tools["get_current_date"].invoke({})
        self.assertIn("现在是北京时间", clock)
        self.assertIn("UTC+8", clock)
        self.assertNotEqual(clock, datetime.now().strftime("%Y-%m-%d"))


if __name__ == "__main__":
    unittest.main()
