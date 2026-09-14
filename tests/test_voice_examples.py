from __future__ import annotations

import unittest

from core.identity import compose_system_prompt
from core.voice_examples import (
    VOICE_EXAMPLES_FILE,
    VoiceExample,
    format_examples_block,
    infer_mode,
    infer_scene,
    load_examples,
    save_examples,
    select_examples,
)
from utils.config import PersonaSettings


def _ex(**kwargs) -> VoiceExample:
    data = {
        "id": "x",
        "scene": "chat",
        "mode": "direct",
        "relation": "peer",
        "input": "hi",
        "good": "嗯",
        "bad": "",
    }
    data.update(kwargs)
    return VoiceExample(**data)


class TestVoiceExamples(unittest.TestCase):
    def test_yaml_loads_matrix(self):
        examples = load_examples(VOICE_EXAMPLES_FILE)
        self.assertGreaterEqual(len(examples), 16)
        scenes = {item.scene for item in examples}
        modes = {item.mode for item in examples}
        self.assertTrue({"tech", "chat", "image", "silence"} <= scenes)
        self.assertTrue({"direct", "chime", "bare_wake"} <= modes)

    def test_infer_scene_and_mode(self):
        self.assertEqual(infer_scene(text="docker 起不来报错了"), "tech")
        self.assertEqual(infer_scene(text="今晚有空吗"), "chat")
        self.assertEqual(infer_scene(text="看图", has_media=True), "image")
        self.assertEqual(infer_mode(chime_in=True), "chime")
        self.assertEqual(infer_mode(bare_wake=True), "bare_wake")
        self.assertEqual(infer_mode(), "direct")

    def test_chime_skips_long_direct_tech(self):
        pool = [
            _ex(
                id="tech_port_direct_peer",
                scene="tech",
                mode="direct",
                input="5170 起不来",
                good="先看端口\n再看日志再看配置再看依赖",
                bad="好的，这个问题可以从以下几个方面排查",
            ),
            _ex(
                id="tech_chime_peer",
                scene="tech",
                mode="chime",
                input="redis 连不上",
                good="先看密码",
                bad="我来帮大家总结排查步骤",
            ),
            _ex(
                id="silence_chime_peer",
                scene="silence",
                mode="chime",
                input="两个人在互聊",
                good="[SILENCE]",
                bad="哈哈确实",
            ),
        ]
        picked = select_examples(
            pool,
            mode="chime",
            relation="peer",
            scene="tech",
            chime_in=True,
        )
        ids = {item.id for item in picked}
        self.assertNotIn("tech_port_direct_peer", ids)
        self.assertTrue(any(item.scene == "silence" for item in picked))
        self.assertTrue(any(item.bad.strip() for item in picked))

    def test_bare_wake_excludes_tech(self):
        pool = [
            _ex(id="tech", scene="tech", mode="direct", good="先看日志"),
            _ex(id="wake", scene="chat", mode="bare_wake", input="小Lu", good="嗯？", bad="您好我是助手"),
        ]
        picked = select_examples(
            pool,
            mode="bare_wake",
            relation="peer",
            scene="chat",
            bare_wake=True,
        )
        self.assertFalse(any(item.scene == "tech" for item in picked))
        self.assertEqual([item.id for item in picked], ["wake"])

    def test_format_block_is_system_text(self):
        block = format_examples_block(
            [
                _ex(
                    id="a",
                    input="5170 起不来",
                    good="多半是端口占了",
                    bad="好的，这个问题可以从以下几个方面排查",
                )
            ]
        )
        self.assertIsInstance(block, str)
        self.assertTrue(block.startswith("[说话样例]"))
        self.assertIn("不是记忆", block)
        self.assertIn("用户: 5170 起不来", block)
        self.assertIn("小Lu: 多半是端口占了", block)
        self.assertIn("不要像:", block)
        prompt = compose_system_prompt(
            PersonaSettings(name="小Lu"), "user", in_group=True, examples=block
        )
        self.assertIn("[说话样例]", prompt)
        self.assertNotIn("HumanMessage", prompt)

    def test_save_examples_roundtrip(self):
        import tempfile
        from pathlib import Path

        from core.voice_examples import reset_examples_cache

        folder = Path(tempfile.mkdtemp())
        path = folder / "voice_examples.yaml"
        saved = save_examples(
            [
                VoiceExample(
                    id="",
                    scene="chat",
                    mode="direct",
                    relation="peer",
                    input="在吗",
                    good="在",
                    bad="您好我是助手",
                )
            ],
            path=path,
        )
        self.assertEqual(saved[0].id, "chat_direct_peer")
        reset_examples_cache()
        loaded = load_examples(path)
        self.assertEqual(loaded[0].good, "在")
