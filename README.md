# AI Voice Calling Agent — Clinic Receptionist

A local-first AI agent that answers the phone for a clinic and **books appointments**.
One deterministic `Agent` brain runs the whole conversation — *the model proposes, the
code disposes*: the LLM only extracts intent and slot values as JSON, while
deterministic code owns every state transition and the **atomic booking write**. The
agent never says "you're booked" unless the database write actually happened.

The same brain is reachable over three channels — CLI, a browser "phone" with real
speech-to-text and text-to-speech, and Telegram — that differ only in transport.

> **Status:** `v0` — a zero-cost, single-GPU prototype that proves the core booking
> loop end-to-end in text and voice. See [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) for the
> roadmap to telephony, real EHR integration, and a staff dashboard.

---

## Quick start

Everything lives under [`v0/`](v0/). Run from the **project root** (this folder):

```bash
python -m v0.main --reset     # wipe + reseed the fake calendar, then chat in your terminal
```

No third-party packages are required to run the text loop — it uses stdlib `sqlite3` and
`urllib` only. For natural language understanding, run a local model with
[Ollama](https://ollama.com) (`ollama pull qwen2.5:7b`); without it, the agent falls back
to a built-in heuristic extractor so the loop still works (one value per answer).

Full setup for each channel — voice server, TTS model download, Telegram bot — is in
**[`v0/README.md`](v0/README.md)**.

## Channels

All three share one `Agent` (`v0/agent.py`); only the transport differs.

| Channel        | Run                          | Input            | Output                  |
|----------------|------------------------------|------------------|-------------------------|
| CLI (text)     | `python -m v0.main`          | keyboard         | terminal                |
| Web voice call | `python -m v0.voice_server`  | mic / text box   | Kokoro / browser voice  |
| Telegram       | `python -m v0.telegram_bot`  | text / voice note| text                    |

The voice pipeline is: browser mic → **faster-whisper** (STT, on GPU) → `Agent` → reply →
**Kokoro** neural TTS (or the browser's voice). A live calendar panel shows slots
disappearing as bookings are made.

## How it works

```
caller ─▶ safety gate ─▶ LLM extractor ─▶ state machine ─▶ atomic booking ─▶ read-back
         (red-flag /     (JSON slots,     (deterministic   (one winner under
          988 crisis)     never actions)   transitions)      concurrent booking)
```

| File | Role |
|---|---|
| `v0/agent.py`       | The booking state machine (the spine) |
| `v0/scheduling.py`  | `SchedulingProvider` interface + atomic SQLite booking |
| `v0/safety.py`      | Deterministic red-flag / crisis gate |
| `v0/state.py`       | `ConversationState` + slot validation |
| `v0/llm.py`         | `OllamaExtractor` (real) + `HeuristicExtractor` (fallback) |
| `v0/stt.py` / `v0/tts.py` | faster-whisper STT / Kokoro TTS adapters |
| `v0/voice_server.py` + `v0/web/` | Browser "phone" UI |
| `v0/telegram_bot.py`| Long-polling Telegram channel |
| `v0/tests/`         | Acceptance harness for the PRD invariants (no model required) |

## Documentation

- **[PRD_AI_Voice_Calling_Agent.md](PRD_AI_Voice_Calling_Agent.md)** — product requirements, goals, and acceptance criteria.
- **[SYSTEM_DESIGN.md](SYSTEM_DESIGN.md)** — architecture across tiers, from the v0 prototype to production.
- **[SCENARIOS.md](SCENARIOS.md)** — caller scenario & test-coverage catalog.
- **[v0/README.md](v0/README.md)** — detailed run/setup instructions for every channel.

## Tests

```bash
pip install pytest
pytest -q
```

The acceptance harness proves the core invariants **with no model required**: happy-path
booking removes the slot; double-books are refused (exactly one winner under concurrency);
missing/invalid fields are re-asked while prior answers are preserved; red-flag and crisis
phrases route correctly; and the agent never falsely claims "booked".

## Configuration

All configuration is via **environment variables** — no secrets are stored in the repo.

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST`      | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL`     | `qwen2.5:7b`             | Local LLM for slot extraction |
| `WHISPER_MODEL`    | `small`                  | faster-whisper size (`base`/`small`/`medium`) |
| `WHISPER_DEVICE`   | `auto`                   | STT device (`auto`/`cpu`/`cuda`) |
| `KOKORO_VOICE`     | `af_heart`               | Neural TTS voice |
| `TELEGRAM_TOKEN`   | —                        | Telegram bot token (from @BotFather) |
| `PORT`             | `8000`                   | Voice server port |

## Models & hardware

The Kokoro TTS model files (`v0/models/kokoro-v1.0.onnx` ~325 MB and `voices-v1.0.bin`)
are **not committed** — download them once per the instructions in
[`v0/README.md`](v0/README.md). The prototype is tuned for a single 8 GB GPU: a 7B model
at 4-bit is the sweet spot, STT runs on the GPU (auto-falling back to CPU), and the ONNX
TTS build installs cleanly on Python 3.14 without torch.

## License

No license file yet — all rights reserved by default. Open an issue or ask if you'd like
an open-source license (e.g. MIT) added.
