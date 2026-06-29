"""Browser voice-call server for the v0 booking agent.

Reuses the exact same Agent brain as the CLI — only the input device changes
(microphone -> faster-whisper -> Agent -> reply -> browser speech synthesis).

Run:
    python -m v0.voice_server          # serves http://localhost:8000
    PORT=9000 python -m v0.voice_server

Endpoints:
    GET  /                     -> the phone UI
    POST /api/call/start       -> {session, greeting, extractor}
    POST /api/turn?session=ID  -> body = audio bytes; {transcript, reply, ...}
    POST /api/text?session=ID  -> {text}; {reply, ...}   (typing fallback)
    GET  /api/availability     -> {open, slots:[...]}
    POST /api/reset            -> reseed calendar, returns availability
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db, llm, stt, tts
from .agent import Agent
from .scheduling import SqliteCalendarAdapter

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "web", "index.html")

adapter = SqliteCalendarAdapter()
sessions: dict[str, Agent] = {}
_extractor = None
_lock = threading.Lock()


def get_extractor():
    global _extractor
    if _extractor is None:
        _extractor = llm.default_extractor()
    return _extractor


def availability() -> dict:
    slots = adapter.find_open_slots(date=None, limit=1000)
    return {
        "open": len(slots),
        "slots": [{"id": s.id, "label": s.pretty(), "visit_type": s.visit_type}
                  for s in slots[:40]],
    }


def _agent_state(agent: Agent) -> dict:
    return {
        "stage": agent.state.stage,
        "booked": agent.state.booked_appointment_id is not None,
        "escalated": agent.state.escalated,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the console quiet

    # -- helpers ---------------------------------------------------------
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _session(self):
        qs = parse_qs(urlparse(self.path).query)
        sid = qs.get("session", [None])[0]
        return sid, sessions.get(sid)

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            try:
                with open(INDEX, "rb") as f:
                    body = f.read()
            except FileNotFoundError:
                self._json({"error": "index.html missing"}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/availability":
            self._json(availability())
        elif path == "/api/appointments":
            appts = adapter.list_appointments()
            self._json({"count": len(appts), "appointments": appts})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/call/start":
            sid = uuid.uuid4().hex
            agent = Agent(adapter, get_extractor())
            sessions[sid] = agent
            self._json({
                "session": sid,
                "greeting": agent.greeting(),
                "extractor": agent.extractor.name,
                "stt_available": stt.available(),
                "server_tts": tts.available(),
            })

        elif path == "/api/turn":
            sid, agent = self._session()
            if not agent:
                self._json({"error": "no active call — press Call first"}, 400)
                return
            audio = self._body()
            if not audio:
                self._json({"error": "empty audio"}, 400)
                return
            try:
                text = stt.transcribe(audio)
            except RuntimeError as e:
                self._json({"transcript": "", "reply": str(e),
                            "stt_error": True, **_agent_state(agent)})
                return
            if not text:
                self._json({"transcript": "", "reply":
                            "Sorry, I didn't catch that — could you say it again?",
                            "stt": stt.backend(), **_agent_state(agent)})
                return
            reply = agent.handle(text)
            self._json({"transcript": text, "reply": reply,
                        "stt": stt.backend(), **_agent_state(agent),
                        "availability": availability()})

        elif path == "/api/text":
            sid, agent = self._session()
            if not agent:
                self._json({"error": "no active call — press Call first"}, 400)
                return
            try:
                text = json.loads(self._body() or b"{}").get("text", "").strip()
            except json.JSONDecodeError:
                text = ""
            if not text:
                self._json({"error": "empty text"}, 400)
                return
            reply = agent.handle(text)
            self._json({"transcript": text, "reply": reply,
                        **_agent_state(agent), "availability": availability()})

        elif path == "/api/tts":
            try:
                text = json.loads(self._body() or b"{}").get("text", "")
            except json.JSONDecodeError:
                text = ""
            try:
                wav = tts.synthesize(text)
            except Exception as e:
                self._json({"error": f"tts unavailable: {e}"}, 503)
                return
            if not wav:
                self._json({"error": "no audio"}, 503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)

        elif path == "/api/reset":
            with _lock:
                db.seed_slots(reset=True)
            self._json(availability())
        else:
            self._json({"error": "not found"}, 404)


def _warm_stt():
    if stt.available():
        try:
            stt.load()
            print(f"[stt: faster-whisper '{stt.MODEL_SIZE}' ready on {stt.backend()}]", flush=True)
        except Exception as e:  # pragma: no cover
            print(f"[stt: warm-up failed: {e}]", flush=True)
    else:
        print("[stt: faster-whisper not installed — voice disabled, text still works. "
              "Install with: pip install faster-whisper]", flush=True)


def _warm_tts():
    if tts.available():
        try:
            tts.load()
            print(f"[tts: Kokoro ready · voice '{tts.VOICE}']", flush=True)
        except Exception as e:  # pragma: no cover
            print(f"[tts: Kokoro install present but failed to load ({e}); "
                  "browser voice will be used]", flush=True)
    else:
        print("[tts: Kokoro not installed — using browser voice. "
              "For a natural local voice: pip install kokoro soundfile]", flush=True)


def main():
    db.bootstrap()
    ext = get_extractor()
    print(f"[extractor: {ext.name}]", flush=True)
    threading.Thread(target=_warm_stt, daemon=True).start()
    threading.Thread(target=_warm_tts, daemon=True).start()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"\n  Open your browser to:  http://localhost:{port}\n  (Ctrl+C to stop)\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
