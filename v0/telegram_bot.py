"""Telegram channel for the v0 booking agent.

Reuses the exact same Agent brain as the CLI and the web/voice server — Telegram
is just another transport. Uses long-polling over the raw Bot API (stdlib urllib),
so it runs locally with only a bot token: no public webhook, HTTPS, or extra deps.

Setup:
    1. Message @BotFather on Telegram, /newbot, copy the token.
    2. set the token:  PowerShell: $env:TELEGRAM_TOKEN="123456:ABC-DEF..."
    3. python -m v0.telegram_bot
    4. Open your bot in Telegram and send /start.

Text messages just work. Voice notes are transcribed via faster-whisper (if installed).
The LLM (Ollama) and STT run locally; only api.telegram.org needs internet.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional

from . import db, llm, stt
from .agent import Agent
from .scheduling import SqliteCalendarAdapter


class TelegramBot:
    """Channel-agnostic core (on_text/on_voice) + a thin polling transport.

    The core is network-free and unit-testable; the transport talks to Telegram.
    """

    def __init__(self, token: str, adapter, extractor):
        self.token = token
        self.adapter = adapter
        self.extractor = extractor
        self.sessions: dict[int, Agent] = {}

    # -- channel core (testable, no network) -----------------------------
    def reset(self, chat_id: int) -> str:
        agent = Agent(self.adapter, self.extractor)
        self.sessions[chat_id] = agent
        return agent.greeting()

    def session(self, chat_id: int) -> Agent:
        if chat_id not in self.sessions:
            self.reset(chat_id)
        return self.sessions[chat_id]

    def on_text(self, chat_id: int, text: str) -> List[str]:
        t = (text or "").strip()
        if t.lower() in ("/start", "/reset", "/newcall"):
            return [self.reset(chat_id)]
        if t.lower() == "/help":
            return ["Send a message (or a voice note) to book an appointment. "
                    "/start begins a new call, /reset starts over."]
        out: List[str] = []
        if chat_id not in self.sessions:          # first contact → greet, then handle
            out.append(self.reset(chat_id))
        out.append(self.session(chat_id).handle(t))
        return out

    def on_voice(self, chat_id: int, audio_bytes: bytes) -> List[str]:
        if not stt.available():
            return ["I can't process voice messages right now — please type your message. "
                    "(Voice needs faster-whisper installed.)"]
        try:
            text = stt.transcribe(audio_bytes)
        except Exception as e:  # pragma: no cover
            return [f"Sorry, I couldn't process that voice message ({e}). Please type it."]
        if not text:
            return ["Sorry, I couldn't make out that voice message — could you type it?"]
        return [f'🎙️ "{text}"'] + self.on_text(chat_id, text)

    # -- transport (polling) ---------------------------------------------
    def run_polling(self) -> None:
        offset: Optional[int] = None
        print("[telegram] polling for messages… (Ctrl+C to stop)", flush=True)
        while True:
            try:
                updates = self._get_updates(offset)
            except KeyboardInterrupt:
                print("\nstopped.")
                return
            except Exception as e:                # network blip — keep going
                print(f"[telegram] getUpdates error: {e}", flush=True)
                time.sleep(2)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    self._dispatch(u)
                except Exception as e:            # one bad update shouldn't kill the bot
                    print(f"[telegram] dispatch error: {e}", flush=True)

    def _dispatch(self, update: dict) -> None:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]
        self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        if "voice" in msg or "audio" in msg:
            file_id = (msg.get("voice") or msg.get("audio"))["file_id"]
            replies = self.on_voice(chat_id, self._download(file_id))
        elif "text" in msg:
            replies = self.on_text(chat_id, msg["text"])
        else:
            replies = ["I can take a text or a voice message. How can I help?"]
        for r in replies:
            self._send(chat_id, r)

    # -- Telegram API helpers --------------------------------------------
    @property
    def _base(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def _call(self, method: str, payload: dict, timeout: int = 65) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{self._base}/{method}", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get_updates(self, offset: Optional[int]) -> list:
        payload = {"timeout": 50, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload).get("result", [])

    def _send(self, chat_id: int, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text})

    def _download(self, file_id: str) -> bytes:
        info = self._call("getFile", {"file_id": file_id})
        path = info["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{self.token}/{path}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read()


def main() -> int:
    token = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("No bot token. Get one from @BotFather, then set TELEGRAM_TOKEN.\n"
              '  PowerShell:  $env:TELEGRAM_TOKEN="123456:ABC-DEF..."', flush=True)
        return 1
    db.bootstrap()
    extractor = llm.default_extractor()
    bot = TelegramBot(token, SqliteCalendarAdapter(), extractor)
    print(f"[telegram] ready · nlu={extractor.name} · stt={'on' if stt.available() else 'off'}",
          flush=True)
    bot.run_polling()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
