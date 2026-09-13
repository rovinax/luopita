from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, Log, Static
from textual import on
import json
import os
import websockets


class ChatApp(App[None]):
    CSS_PATH = "tcss/chat.tcss"
    TITLE = "Luopita TUI"

    def __init__(self) -> None:
        super().__init__()
        host = os.environ.get("LUOPITA_TUI_WS", "ws://127.0.0.1:5170/ws/tui?chat_id=local&user_id=tui")
        self.ws_url = host
        self._ws = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"正在连接 {self.ws_url}", id="status")
        yield Log(id="transcript")
        yield Input(placeholder="输入消息，回车发送", id="composer")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#composer", Input).focus()
        self.run_worker(self._listen, exclusive=True, group="ws")

    async def _listen(self) -> None:
        status = self.query_one("#status", Static)
        log = self.query_one("#transcript", Log)
        try:
            async with websockets.connect(self.ws_url) as ws:
                self._ws = ws
                status.update("已连接 · 与 QQ / 管理端共用同一 Agent")
                async for raw in ws:
                    data = json.loads(raw)
                    if data.get("type") == "reply":
                        log.write_line(f"Luopita ▸ {data.get('text')}")
                    elif data.get("type") == "error":
                        log.write_line(f"错误 ▸ {data.get('error')}")
        except Exception as exc:
            status.update(f"连接失败：{exc}")
            self._ws = None

    @on(Input.Submitted, "#composer")
    async def send_text(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        event.input.value = ""
        if not text:
            return
        log = self.query_one("#transcript", Log)
        if self._ws is None:
            log.write_line("尚未连上后端，请先启动 uv run python main.py")
            return
        log.write_line(f"你 ▸ {text}")
        await self._ws.send(json.dumps({"text": text, "user_id": "tui", "chat_id": "local"}))


if __name__ == "__main__":
    ChatApp().run()
