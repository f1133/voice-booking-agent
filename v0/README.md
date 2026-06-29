# v0 — Text-First Booking Loop

The zero-cost, single-GPU prototype from `../SYSTEM_DESIGN.md` §1. It proves the core
booking loop end-to-end in **text** before any voice/telephony is added:

> greet → capture fields → check availability → confirm → **atomic book** → read-back,
> and the booked slot disappears from the next availability query.

**Design principle:** the model proposes, the code disposes. The LLM only extracts
intent/slots as JSON; deterministic code in `agent.py` owns every state transition, and
`scheduling.py` owns the atomic write. The agent never says "you're booked" unless the
write actually happened.

## Run it

From the **project root** (the folder above `v0/`):

```bash
python -m v0.main --reset     # wipe + reseed the fake calendar, then chat
python -m v0.main             # chat (keeps existing bookings)
python -m v0.main --slots     # print current availability and exit
```

No third-party packages are required to run — it uses stdlib `sqlite3` + `urllib`.

### With a local model (recommended, your RTX 5060)

```bash
# install Ollama (current release — needs CUDA 12.8+ for Blackwell), then:
ollama pull qwen2.5:7b
ollama serve            # if not already running
export OLLAMA_MODEL=qwen2.5:7b      # PowerShell: $env:OLLAMA_MODEL="qwen2.5:7b"
python -m v0.main --reset
```

If Ollama isn't detected, the agent automatically falls back to a **heuristic extractor**
so the loop still runs (answer each prompt with one value at a time). The deterministic
state machine + atomic booking behave identically either way.

## Voice call (browser UI)

A browser "phone" where you press Call, talk, and the agent transcribes your speech
and talks back. Same `Agent` brain as the CLI — the mic just replaces the keyboard.

```bash
pip install faster-whisper          # one-time: local speech-to-text
python -m v0.voice_server           # then open http://localhost:8000
```

- **Pipeline:** browser mic → faster-whisper (STT, your GPU) → `Agent` → reply →
  browser speech synthesis (TTS). A live calendar panel shows slots disappearing.
- **Push-to-talk:** hold the "Hold to talk" button while you speak, release to send.
- **Hardware:** STT tries CUDA first, auto-falls back to CPU if the Blackwell build
  isn't ready (set `WHISPER_MODEL=base` for a lighter/faster model).
- **No faster-whisper?** The server still runs — use the text box; the mic reports
  that STT isn't installed.
- **Mic permission** needs `http://localhost` (a secure context), which is what the
  server uses. Best in Chrome/Edge.

For natural speech understanding, also run Ollama (`ollama pull qwen2.5:7b`); otherwise
the heuristic extractor expects one piece of info per utterance.

### Natural voice (TTS)

By default the agent speaks with the **browser's** best available voice — this is robotic
in Chrome (Windows SAPI) but quite natural in **Microsoft Edge** (neural "Aria/Online"
voices), so try Edge for a free upgrade.

For a fully natural **local** voice, install Kokoro (ONNX build — works on Python 3.14;
the torch-based `kokoro` package does not):

```bash
pip install kokoro-onnx soundfile
```

Then download the two model files into `v0/models/` (once, ~330MB):

```bash
# from v0/models/
curl -L -o kokoro-v1.0.onnx  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o voices-v1.0.bin   https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

Restart `python -m v0.voice_server` — you'll see `[tts: Kokoro ready · voice 'af_heart']`,
the pill shows `tts: kokoro`, and the agent speaks with a natural local voice. If the
package or model files are missing it silently falls back to the browser voice. Pick a
voice with `KOKORO_VOICE` (e.g. `af_heart`, `af_bella`, `am_michael`, `bf_emma`).

## Telegram channel

The same agent, reachable from a Telegram bot — text or voice notes. Uses long-polling
(no public webhook/HTTPS needed) so it runs locally with just a token.

```bash
# 1. In Telegram, message @BotFather → /newbot → copy the token
# 2. set the token and run:
$env:TELEGRAM_TOKEN="123456:ABC-DEF..."     # PowerShell
python -m v0.telegram_bot
# 3. Open your bot in Telegram and send /start
```

- **Text** works with no extra setup. **Voice notes** are transcribed via faster-whisper
  (if installed); otherwise the bot asks you to type.
- Each Telegram chat is its own session. Commands: `/start` or `/reset` (new call), `/help`.
- The LLM (Ollama) and STT run locally; only `api.telegram.org` needs internet.

## Channels at a glance

All three share one `Agent` brain (`agent.py`) — only the transport differs:

| Channel | Run | Input | Output |
|---|---|---|---|
| CLI (text) | `python -m v0.main` | keyboard | terminal |
| Web voice call | `python -m v0.voice_server` | mic / text box | Kokoro / browser voice |
| Telegram | `python -m v0.telegram_bot` | text / voice note | text |

## Test it

```bash
pip install pytest
pytest -q
```

The acceptance harness (`tests/test_acceptance.py`) proves the invariants from PRD §7.2 —
**no model required**:

- happy-path booking removes the slot from availability
- double-book refused; **concurrent** double-book → exactly one winner
- missing/invalid field is re-asked and prior answers are preserved
- red-flag phrase is detected; crisis phrase routes to the 988 path
- full end-to-end conversation books successfully (heuristic extractor)
- agent never falsely claims "booked" when the slot is taken before confirmation

## Files

| File | Role |
|---|---|
| `schema.sql` / `db.py` | Fake calendar schema + seeding |
| `scheduling.py` | `SchedulingProvider` interface + `SqliteCalendarAdapter` (atomic booking) |
| `safety.py` | Deterministic red-flag / crisis gate |
| `state.py` | `ConversationState` + slot validation |
| `llm.py` | `OllamaExtractor` (real) + `HeuristicExtractor` (fallback) |
| `agent.py` | The booking state machine (the spine) |
| `main.py` | CLI chat loop |
| `tests/` | Acceptance harness |

## What's intentionally NOT here (next layers)

Voice (faster-whisper + Piper + Pipecat VAD), telephony (Asterisk/SIP), real EHR adapter,
notifications, identity lookup, staff dashboard — all behind the interfaces already defined.
See `../SYSTEM_DESIGN.md` §2.
