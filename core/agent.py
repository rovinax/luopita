"""Thin HTTP client for the same Agent Graph used by /api/chat. The old tag protocol REPL is retired."""

from __future__ import annotations

import os
import sys

import httpx


def main() -> None:
    base = os.environ.get("LUOPITA_URL", "http://127.0.0.1:5170").rstrip("/")
    session_id = None
    user_id = os.environ.get("LUOPITA_CLI_USER", "cli")
    print(f"Luopita CLI -> {base}/api/chat  (Ctrl-C to exit)")
    while True:
        try:
            user_input = input("User:>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not user_input:
            continue
        try:
            response = httpx.post(
                f"{base}/api/chat",
                json={"text": user_input, "user_id": user_id, "session_id": session_id},
                timeout=120.0,
            )
            data = response.json()
        except Exception as exc:
            print(f"request failed: {exc}", file=sys.stderr)
            continue
        if not data.get("ok"):
            print(f"Agent:>> error: {data}")
            continue
        session_id = data.get("session_id")
        print(f"Agent:>> {data.get('reply')}")


if __name__ == "__main__":
    main()
