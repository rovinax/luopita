from __future__ import annotations

import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="luopita-media-")
os.environ["LUOPITA_PROVIDER"] = "mock"
os.environ["LUOPITA_API_KEY"] = ""
os.environ["LUOPITA_DATABASE_URL"] = "memory://"
os.environ["LUOPITA_NAPCAT_ENABLED"] = "false"
os.environ["LUOPITA_IDENTITY_FILE"] = os.path.join(_tmp, "identity.yaml")
os.environ["LUOPITA_PERSON_FILE"] = os.path.join(_tmp, "person.yaml")

import httpx

from app.runtime import Runtime
from core.group_talk import decide_group_reply
from core.identity import compose_system_prompt
from core.media import (
    FetchedMedia,
    MINI_PNG,
    build_user_content,
    messages_have_vision,
    normalize_deepseek_message,
    prepare_messages_for_llm,
    sniff_image_mime,
)
from interface.llm.factory import get_files_client, vision_model_name
from interface.llm.files import MockFilesClient
from interface.platform.napcat import (
    NapcatAdapter,
    extract_media,
    is_direct_downloadable,
    is_napcat_hosted_url,
    parse_onebot_event,
    rewrite_loopback_url,
    sanitize_media_url,
)
from langchain_core.messages import HumanMessage
from msg.schema import InboundMessage, MediaRef
from utils.config import NapcatSettings, PersonaSettings


class TestMediaParse(unittest.TestCase):
    def test_extract_image_and_file_segments(self):
        inbound = parse_onebot_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 42,
                "message": [
                    {"type": "image", "data": {"file": "a.jpg", "url": "http://127.0.0.1:3000/a.jpg"}},
                    {"type": "file", "data": {"file_id": "f1", "name": "note.txt"}},
                ],
            }
        )
        assert inbound is not None
        self.assertEqual(len(inbound.media), 2)
        self.assertEqual(inbound.media[0].kind, "image")
        self.assertEqual(inbound.media[1].kind, "file")
        self.assertIn("图片", inbound.text)

    def test_cq_image(self):
        items = extract_media("[CQ:image,file=b.png,url=http://127.0.0.1/b.png]")
        self.assertEqual(items[0].file_id, "b.png")
        self.assertEqual(items[0].url, "http://127.0.0.1/b.png")

    def test_stickers_are_not_media(self):
        inbound = parse_onebot_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 9,
                "user_id": 3,
                "message": [
                    {"type": "image", "data": {"file": "funny.gif", "url": "http://127.0.0.1/x.gif", "sub_type": 1}},
                    {"type": "mface", "data": {"emoji_id": "1", "emoji_package_id": "2", "summary": "[哇]"}},
                ],
            }
        )
        assert inbound is not None
        self.assertEqual(inbound.media, [])
        self.assertEqual(inbound.text, "[表情包][表情包:哇]")
        cq = extract_media("[CQ:image,file=sticker.gif,subType=1,url=http://127.0.0.1/s.gif]")
        self.assertEqual(cq, [])
        market = extract_media(
            [{"type": "image", "data": {"file": "marketface", "url": "http://127.0.0.1/m.gif", "summary": "[动画表情]"}}]
        )
        self.assertEqual(market, [])
        photo = extract_media([{"type": "image", "data": {"file": "shot.png", "url": "http://127.0.0.1/shot.png"}}])
        self.assertEqual(photo[0].file_id, "shot.png")
        cq_msg = parse_onebot_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 42,
                "message": "[CQ:image,file=sticker.gif,subType=1,url=http://127.0.0.1/s.gif]",
            }
        )
        assert cq_msg is not None
        self.assertEqual(cq_msg.media, [])
        self.assertEqual(cq_msg.text, "[表情包]")

    def test_rewrite_loopback(self):
        out = rewrite_loopback_url("http://127.0.0.1:3000/foo", "http://napcat:3000")
        self.assertEqual(out, "http://napcat:3000/foo")

    def test_unescape_qq_cdn_url(self):
        raw = "https://multimedia.nt.qq.com.cn/download?appid=1407&amp;fileid=abc&amp;rkey=zz"
        clean = sanitize_media_url(raw)
        self.assertNotIn("&amp;", clean)
        self.assertIn("appid=1407&fileid=abc", clean)
        self.assertFalse(is_direct_downloadable("not-a-url", "http://napcat:3000"))
        self.assertTrue(is_direct_downloadable(clean, "http://napcat:3000"))
        self.assertTrue(is_direct_downloadable("http://napcat:3000/img.png", "http://napcat:3000"))
        self.assertTrue(is_napcat_hosted_url("http://127.0.0.1:3000/img.png", "http://napcat:3000"))
        self.assertFalse(is_napcat_hosted_url(clean, "http://napcat:3000"))
        self.assertEqual(
            rewrite_loopback_url("http://127.0.0.1:6099/img.png", "http://napcat:3000"),
            "http://napcat:6099/img.png",
        )

    def test_flatten_nested_file_block(self):
        out = normalize_deepseek_message(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看下"},
                    {"type": "file", "file": {"file_id": "file-api-abc"}},
                ],
            }
        )
        self.assertEqual(out["content"][1], {"type": "file", "file_id": "file-api-abc"})

    def test_history_drops_old_file_blocks(self):
        old = HumanMessage(
            content=[{"type": "text", "text": "旧图"}, {"type": "file", "file": {"file_id": "file-api-old"}}]
        )
        new = HumanMessage(
            content=[{"type": "text", "text": "新图"}, {"type": "file", "file_id": "file-api-new"}]
        )
        prepared = prepare_messages_for_llm([old, HumanMessage(content="ok"), new])
        self.assertEqual(prepared[0].content, "旧图\n[图片]")
        self.assertEqual(prepared[2].content[1], {"type": "file", "file_id": "file-api-new"})

    def test_group_image_is_not_filler_but_not_forced(self):
        skipped = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="[图片]",
            has_media=True,
            rng=lambda: 1.0,
        )
        self.assertEqual(skipped, "ignore")

    def test_sticker_only_is_group_filler(self):
        skipped = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="[表情包]",
            has_media=False,
            engaged=True,
            same_speaker=True,
            replies_left=3,
            rng=lambda: 0.0,
        )
        self.assertEqual(skipped, "ignore")
        named = decide_group_reply(
            channel_type="group",
            role="user",
            group_require_at=True,
            text="[表情包:哇]",
            has_media=False,
            engaged=True,
            same_speaker=True,
            replies_left=3,
            rng=lambda: 0.0,
        )
        self.assertEqual(named, "ignore")

    def test_prompt_lists_vision(self):
        text = compose_system_prompt(PersonaSettings(), "owner", in_group=False, has_media=True)
        self.assertIn("看图", text)
        self.assertIn("先看再回", text)
        self.assertIn("run_shell", text)
        self.assertNotIn("看图（截图、报错、表情包）", text)
        self.assertIn("能读懂在表达什么", text)
        self.assertIn("不要围着它展开", text)
        self.assertIn("一层意思", text)

    def test_group_prompt_says_attached_images_may_be_from_others(self):
        text = compose_system_prompt(PersonaSettings(), "owner", in_group=True, has_media=True)
        self.assertIn("群里刚才别人发的", text)
        self.assertIn("不要只凭记忆或文件名编内容", text)

    def test_take_media_prefers_current_then_history(self):
        from interface.platform.napcat import media_from_event_text, take_media

        current = [MediaRef(kind="image", file_id="now.png", name="now.png")]
        hist = [
            MediaRef(kind="image", file_id="old1.png", name="old1.png"),
            MediaRef(kind="image", file_id="now.png", name="now.png"),
            MediaRef(kind="image", file_id="old2.png", name="old2.png"),
        ]
        out = take_media(current, hist, limit=2)
        self.assertEqual([item.file_id for item in out], ["now.png", "old1.png"])
        parsed = media_from_event_text("看这个 [图片:{abc}.image] 和 [文件:note.txt]")
        self.assertEqual(parsed[0].kind, "image")
        self.assertEqual(parsed[0].file_id, "{abc}.image")
        self.assertEqual(parsed[1].kind, "file")
        self.assertEqual(parsed[1].file_id, "note.txt")

    def test_vision_model_default(self):
        self.assertEqual(vision_model_name("deepseek", "deepseek-chat", ""), "deepseek-flash")
        self.assertEqual(vision_model_name("deepseek", "deepseek-chat", "custom-v"), "custom-v")


class TestFilesAndContent(unittest.IsolatedAsyncioTestCase):
    async def test_mock_upload_and_file_block(self):
        client = MockFilesClient()
        content, _notes = await build_user_content(
            "看下",
            [FetchedMedia(kind="image", name="a.png", data=MINI_PNG, mime="image/png")],
            client,
        )
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "file")
        self.assertTrue(str(content[1]["file_id"]).startswith("file-api-mock-"))
        self.assertNotIn("file", content[1])
        self.assertEqual(len(client.uploads), 1)
        self.assertTrue(messages_have_vision([HumanMessage(content=content)]))

    async def test_text_file_preview(self):
        content, notes = await build_user_content(
            "",
            [FetchedMedia(kind="file", name="a.txt", data=b"hello world", mime="text/plain")],
            MockFilesClient(),
        )
        self.assertIn("hello world", notes)
        self.assertIn("hello world", content if isinstance(content, str) else str(content))

    async def test_binary_file_refused(self):
        _content, notes = await build_user_content(
            "这个",
            [FetchedMedia(kind="file", name="a.zip", data=b"PK\x03\x04\x00binary\x00", mime="application/zip")],
            MockFilesClient(),
        )
        self.assertIn("打不开", notes)

    def test_sniff_png(self):
        self.assertEqual(sniff_image_mime(MINI_PNG, "x.bin"), "image/png")

    async def test_empty_bytes_are_not_images(self):
        content, notes = await build_user_content(
            "看",
            [FetchedMedia(kind="image", name="a.jpg", data=b"", mime="")],
            MockFilesClient(),
        )
        self.assertIn("打不开", notes)
        if isinstance(content, list):
            self.assertFalse(any(isinstance(block, dict) and block.get("type") in {"file", "image_url"} for block in content))
        else:
            self.assertNotIn("file_id", content)


class TestFetchMedia(unittest.IsolatedAsyncioTestCase):
    async def test_get_image_url_rewrite_and_download(self):
        downloaded = {"url": ""}

        def handler(request: httpx.Request) -> httpx.Response:
            path = str(request.url)
            if path.endswith("/get_image"):
                return httpx.Response(
                    200,
                    json={"status": "ok", "retcode": 0, "data": {"url": "http://127.0.0.1:3000/img.png"}},
                )
            downloaded["url"] = str(request.url)
            return httpx.Response(200, content=MINI_PNG)

        adapter = NapcatAdapter(NapcatSettings(enabled=True, bot_url="http://napcat:3000"))
        await adapter.api.aclose()
        adapter.api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        blob = await adapter.fetch_media(MediaRef(kind="image", file_id="abc.jpg", name="abc.jpg"))
        assert blob is not None
        self.assertEqual(blob.data, MINI_PNG)
        self.assertIn("napcat:3000", downloaded["url"])
        await adapter.aclose()

    async def test_qq_cdn_goes_through_napcat_download_file(self):
        import base64
        import json

        seen: list[str] = []
        bodies: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = str(request.url)
            seen.append(f"{request.method} {path}")
            if path.endswith("/get_image"):
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "retcode": 0,
                        "data": {
                            "url": "https://multimedia.nt.qq.com.cn/download?appid=1407&amp;fileid=abc"
                        },
                    },
                )
            if path.endswith("/download_file"):
                bodies.append(request.content.decode("utf-8"))
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "retcode": 0,
                        "data": {"base64": base64.b64encode(MINI_PNG).decode("ascii")},
                    },
                )
            return httpx.Response(400, text="bad")

        adapter = NapcatAdapter(
            NapcatSettings(enabled=True, bot_url="http://napcat:3000", access_token="luopita")
        )
        await adapter.api.aclose()
        adapter.api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        blob = await adapter.fetch_media(
            MediaRef(
                kind="image",
                file_id="abc.jpg",
                name="abc.jpg",
                url="https://multimedia.nt.qq.com.cn/download?appid=1407&amp;fileid=abc",
            )
        )
        assert blob is not None
        self.assertEqual(blob.data, MINI_PNG)
        self.assertTrue(any(item.endswith("/download_file") for item in seen))
        self.assertFalse(any("multimedia.nt.qq.com.cn" in item and item.startswith("GET") for item in seen))
        payload = json.loads(bodies[0])
        self.assertIn("appid=1407&fileid=abc", payload["url"])
        self.assertNotIn("&amp;", payload["url"])
        await adapter.aclose()

    async def test_image_file_only_calls_get_image(self):
        import base64
        import json

        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = str(request.url)
            if path.endswith("/get_image"):
                seen.append(json.loads(request.content.decode("utf-8")))
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "retcode": 0,
                        "data": {
                            "file_name": "pic.png",
                            "base64": base64.b64encode(MINI_PNG).decode("ascii"),
                        },
                    },
                )
            return httpx.Response(400, text="bad")

        adapter = NapcatAdapter(NapcatSettings(enabled=False, bot_url="http://napcat:3000"))
        await adapter.api.aclose()
        adapter.api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        blob = await adapter.fetch_media(MediaRef(kind="image", file_id="{abc}.image"))
        assert blob is not None
        self.assertEqual(blob.data, MINI_PNG)
        self.assertEqual(seen[0].get("file"), "{abc}.image")
        await adapter.aclose()

    async def test_get_image_base64(self):
        import base64

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/get_image"):
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "retcode": 0,
                        "data": {
                            "file_name": "a.png",
                            "base64": base64.b64encode(MINI_PNG).decode("ascii"),
                        },
                    },
                )
            return httpx.Response(400, text="bad")

        adapter = NapcatAdapter(NapcatSettings(enabled=True, bot_url="http://napcat:3000"))
        await adapter.api.aclose()
        adapter.api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        blob = await adapter.fetch_media(MediaRef(kind="image", file_id="a.png", name="a.png"))
        assert blob is not None
        self.assertEqual(blob.data, MINI_PNG)
        await adapter.aclose()


class TestPrivateImageChat(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = await Runtime.start()

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_private_image_uses_file_block(self):
        from unittest.mock import patch

        from langchain_core.messages import HumanMessage as RealHuman

        orch = self.runtime.orchestrator
        captured: list = []

        class SpyHuman(RealHuman):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured.append(self)

        with patch("core.chat.HumanMessage", SpyHuman):
            resp = await orch.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="private",
                    chat_id="42",
                    user_id="42",
                    text="[图片:a.jpg]",
                    media=[MediaRef(kind="image", file_id="a.jpg", name="a.jpg")],
                ),
                deliver=False,
            )
        self.assertFalse(resp.ignored)
        self.assertTrue(captured)
        content = captured[0].content
        self.assertIsInstance(content, list)
        self.assertTrue(any(isinstance(b, dict) and b.get("type") == "file" for b in content))

    async def test_disabled_napcat_still_fetches_group_image(self):
        from unittest.mock import patch

        from langchain_core.messages import HumanMessage as RealHuman

        self.assertFalse(self.runtime.napcat.enabled())
        self.assertTrue(self.runtime.napcat.can_fetch())
        seen: list[tuple[str, str]] = []

        async def spy(item, group_id: str = ""):
            seen.append((item.file_id, group_id))
            return FetchedMedia(kind="image", name="pic.png", data=MINI_PNG, mime="image/png")

        self.runtime.napcat.fetch_media = spy  # type: ignore[method-assign]
        captured: list = []

        class SpyHuman(RealHuman):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured.append(self)

        with patch("core.chat.HumanMessage", SpyHuman):
            resp = await self.runtime.orchestrator.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="group",
                    chat_id="967712135",
                    user_id="3337902728",
                    text="[图片] @llluopita.",
                    media=[MediaRef(kind="image", file_id="{abc}.image", name="{abc}.image")],
                    at_user_ids=["1001"],
                    self_id="1001",
                ),
                deliver=False,
            )
        self.assertFalse(resp.ignored)
        self.assertEqual(seen, [("{abc}.image", "967712135")])
        self.assertTrue(captured)
        content = captured[0].content
        self.assertIsInstance(content, list)
        self.assertTrue(any(isinstance(b, dict) and b.get("type") == "file" for b in content))

    async def test_ignored_group_image_is_fetched_on_later_mention(self):
        from unittest.mock import patch

        from langchain_core.messages import HumanMessage as RealHuman

        seen: list[str] = []

        async def spy(item, group_id: str = ""):
            seen.append(item.file_id)
            return FetchedMedia(kind="image", name=item.name or "pic.png", data=MINI_PNG, mime="image/png")

        self.runtime.napcat.fetch_media = spy  # type: ignore[method-assign]
        orch = self.runtime.orchestrator
        ignored = await orch.handle(
            InboundMessage(
                platform="napcat",
                channel_type="group",
                chat_id="pic-timeline-1",
                user_id="2",
                text="[图片:cat.png]",
                sender_name="张三",
                media=[MediaRef(kind="image", file_id="cat.png", name="cat.png")],
            ),
            deliver=False,
        )
        self.assertTrue(ignored.ignored)
        self.assertEqual(seen, [])
        events = await self.runtime.db.load_group_recent("napcat", "pic-timeline-1")
        self.assertEqual(events[-1]["media"][0]["file_id"], "cat.png")

        captured: list = []

        class SpyHuman(RealHuman):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured.append(self)

        with patch("core.chat.HumanMessage", SpyHuman):
            resp = await orch.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="group",
                    chat_id="pic-timeline-1",
                    user_id="3337902728",
                    text="[@1001] 刚才那张图是什么",
                    sender_name="小草莓",
                    at_user_ids=["1001"],
                    self_id="1001",
                ),
                deliver=False,
            )
        self.assertFalse(resp.ignored)
        self.assertEqual(seen, ["cat.png"])
        self.assertTrue(captured)
        content = captured[0].content
        self.assertIsInstance(content, list)
        self.assertTrue(any(isinstance(b, dict) and b.get("type") == "file" for b in content))
        texts = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
        self.assertIn("群里刚才有人发过图", texts)
        self.assertIn("刚才那张图是什么", texts)

    async def test_text_reply_does_not_fetch_recent_group_images(self):
        from unittest.mock import patch

        from langchain_core.messages import HumanMessage as RealHuman

        await self.runtime.db.append_group_event(
            platform="napcat",
            chat_id="pic-skip-1",
            user_id="2",
            sender_name="张三",
            content="[图片:cat.png]",
            media=[{"kind": "image", "file_id": "cat.png", "url": "", "name": "cat.png", "mime": ""}],
        )
        seen: list[str] = []

        async def spy(item, group_id: str = ""):
            seen.append(item.file_id)
            return FetchedMedia(kind="image", name=item.name or "pic.png", data=MINI_PNG, mime="image/png")

        self.runtime.napcat.fetch_media = spy  # type: ignore[method-assign]
        captured: list = []

        class SpyHuman(RealHuman):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured.append(self)

        with patch("core.chat.HumanMessage", SpyHuman):
            resp = await self.runtime.orchestrator.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="group",
                    chat_id="pic-skip-1",
                    user_id="3337902728",
                    text="[@1001] 那不叫锐评，这叫没词了硬撑",
                    sender_name="小草莓",
                    at_user_ids=["1001"],
                    self_id="1001",
                ),
                deliver=False,
            )
        self.assertFalse(resp.ignored)
        self.assertEqual(seen, [])
        self.assertTrue(captured)
        content = captured[0].content
        blob = content if isinstance(content, str) else str(content)
        self.assertNotIn("群里刚才有人发过图", blob)

    async def test_legacy_group_image_placeholder_is_fetched(self):
        from unittest.mock import patch

        from langchain_core.messages import HumanMessage as RealHuman

        await self.runtime.db.append_group_event(
            platform="napcat",
            chat_id="pic-legacy-1",
            user_id="2",
            sender_name="张三",
            content="[图片:{old}.image]",
        )
        seen: list[str] = []

        async def spy(item, group_id: str = ""):
            seen.append(item.file_id)
            return FetchedMedia(kind="image", name=item.name or "pic.png", data=MINI_PNG, mime="image/png")

        self.runtime.napcat.fetch_media = spy  # type: ignore[method-assign]
        captured: list = []

        class SpyHuman(RealHuman):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured.append(self)

        with patch("core.chat.HumanMessage", SpyHuman):
            resp = await self.runtime.orchestrator.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="group",
                    chat_id="pic-legacy-1",
                    user_id="3337902728",
                    text="[@1001] 这图是什么",
                    sender_name="小草莓",
                    at_user_ids=["1001"],
                    self_id="1001",
                ),
                deliver=False,
            )
        self.assertFalse(resp.ignored)
        self.assertEqual(seen, ["{old}.image"])
        content = captured[0].content
        self.assertIsInstance(content, list)
        self.assertTrue(any(isinstance(b, dict) and b.get("type") == "file" for b in content))

    async def test_quoted_image_is_fetched_into_user_content(self):
        from unittest.mock import patch

        from langchain_core.messages import HumanMessage as RealHuman

        seen: list[str] = []

        async def spy(item, group_id: str = ""):
            seen.append(item.file_id)
            return FetchedMedia(kind="image", name=item.name or "wall.png", data=MINI_PNG, mime="image/png")

        async def fake_fetch(message_id: str):
            self.assertEqual(str(message_id), "999")
            return {
                "sender": {"nickname": "张三"},
                "message": [
                    {"type": "image", "data": {"file": "wall.png", "url": "http://127.0.0.1:3000/wall.png"}},
                ],
            }

        self.runtime.napcat.fetch_media = spy  # type: ignore[method-assign]
        self.runtime.napcat.fetch_message = fake_fetch  # type: ignore[method-assign]
        captured: list = []

        class SpyHuman(RealHuman):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured.append(self)

        with patch("core.chat.HumanMessage", SpyHuman):
            resp = await self.runtime.orchestrator.handle(
                InboundMessage(
                    platform="napcat",
                    channel_type="group",
                    chat_id="pic-quote-1",
                    user_id="3337902728",
                    text="[回复:999][@1001] 这图啥意思",
                    sender_name="小草莓",
                    at_user_ids=["1001"],
                    reply_to_ids=["999"],
                    self_id="1001",
                ),
                deliver=False,
            )
        self.assertFalse(resp.ignored)
        self.assertEqual(seen, ["wall.png"])
        self.assertTrue(captured)
        content = captured[0].content
        self.assertIsInstance(content, list)
        self.assertTrue(any(isinstance(b, dict) and b.get("type") == "file" for b in content))
        texts = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
        self.assertIn("被回复的原话", texts)
        self.assertIn("这图啥意思", texts)

    async def test_factory_mock_files(self):
        client = get_files_client("mock", "", "")
        file_id = await client.upload_user_file("a.png", MINI_PNG, "image/png")
        self.assertTrue(file_id.startswith("file-api-mock-"))


if __name__ == "__main__":
    unittest.main()
