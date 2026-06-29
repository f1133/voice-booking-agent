# System Design: AI Voice Calling Agent — Clinic Receptionist

> Companion to `PRD_AI_Voice_Calling_Agent.md`. The PRD defines *what* and *why*;
> this document defines *how*. It is organized by the same build path:
> **v0 local prototype → v1 clinic MVP → Vision**, going deepest on v0 because
> that is the next thing actually built.

---

## 0. Design Principles (carry through every tier)

1. **The model proposes, the code disposes.** The LLM extracts intent and slots and
   *suggests* a tool call. Deterministic code validates and performs every state-changing
   write (bookings, escalations). The LLM never free-texts a confirmed booking.
2. **Safety is a deterministic gate, not a model judgment call.** Red-flag detection runs
   on every turn, outside the LLM's discretion, and can halt the flow regardless of what
   the LLM "wants" to do.
3. **One interface per external dependency.** Scheduling, telephony, STT, LLM, TTS, and
   notifications each sit behind a narrow interface. The fake calendar is just the first
   `SchedulingProvider` adapter; a real EHR is a later adapter, not a rewrite.
4. **Correctness before latency (v0); latency co-equal (v1+).** v0 optimizes for a
   provably-correct booking loop. The 800ms latency budget becomes a hard NFR only when
   real voice/telephony arrives.
5. **Never trap the caller.** Every state has an escape hatch to a human/voicemail.

---

## 1. v0 — Local Prototype Architecture

### 1.1 Goal restated
One GPU, zero spend, no PHI. Prove the booking loop end-to-end (PRD §7.2): extract →
check availability → atomically book → read back → slot disappears. Text-first, then
voice layered on. The three robustness edge cases (slot-taken, missing-field, double-book)
must pass.

### 1.2 Component diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│  Browser (single-page demo)                                            │
│   ┌──────────────┐   text mode: chat box                              │
│   │  Chat UI     │   voice mode: mic capture + audio playback (WebRTC)│
│   │  Live        │                                                     │
│   │  Calendar    │◄── polls/streams calendar state (proves no double- │
│   │  Table       │     booking visibly)                                │
│   └──────────────┘                                                     │
└─────────┬──────────────────────────────────────────▲──────────────────┘
          │ WebSocket (text or audio frames)          │ responses
          ▼                                            │
┌──────────────────────────────────────────────────────────────────────┐
│  Orchestrator (Pipecat pipeline, Python)                              │
│                                                                        │
│   [VAD] → [STT] → [Dialog Manager] → [LLM] → [TTS]                    │
│            (voice          │                                           │
│             mode)          ▼                                           │
│                    ┌───────────────────┐                              │
│                    │ SAFETY GATE        │  runs on every user turn,    │
│                    │ (red-flag regex/   │  highest priority, pre-LLM   │
│                    │  classifier)       │  AND post-LLM check          │
│                    └───────────────────┘                              │
│                            │                                           │
│                    ┌───────────────────┐                              │
│                    │ STATE MACHINE      │  greet→intent→capture→       │
│                    │ + slot store       │  avail→confirm→book→close    │
│                    └─────────┬─────────┘                              │
│                              │ validated tool calls (JSON)            │
└──────────────────────────────┼────────────────────────────────────────┘
                               ▼
                  ┌─────────────────────────────┐
                  │ Tools layer (pure functions) │
                  │  • check_availability        │
                  │  • book_appointment (atomic) │
                  │  • get_clinic_info (FAQ)     │
                  └──────────────┬──────────────┘
                                 ▼
                  ┌─────────────────────────────┐
                  │ SchedulingProvider interface │
                  │   → SqliteCalendarAdapter    │  (v0)
                  └──────────────┬──────────────┘
                                 ▼
                         SQLite (WAL mode)
                    slots, appointments, calls, transcripts
```

### 1.3 Local model stack — target hardware: RTX 5060 8GB (Blackwell, sm_120)

8GB is the binding constraint. In **voice mode** STT + LLM + TTS share the same 8GB, so the
LLM gets the bulk, **TTS runs on CPU** (Piper is fast on CPU, 0 VRAM), and STT stays small.
A 14B model is **out** (≈8.5GB at Q4 even before STT/TTS — it would spill to system RAM).

| Stage | v0 choice (8GB) | VRAM | Notes |
|---|---|---|---|
| STT | `faster-whisper` **small int8** (or `base` ≈0.3GB) | ~0.6GB | Streaming; loaded only in voice mode. |
| LLM | Ollama, **Qwen2.5-7B-Instruct Q4_K_M**, 8k ctx | ~5.5GB (4.7 weights + 0.8 KV) | Sweet spot for instruction-following + JSON. |
| TTS | **Piper on CPU** (Kokoro-82M if GPU, ~0.4GB) | 0GB | Keep on CPU to free VRAM for the LLM. |
| Orchestration | Pipecat | — | VAD, turn-taking, barge-in for voice mode. |
| Store | SQLite (WAL) | — | Atomic writes via transaction + unique constraint. |

**Voice-mode VRAM budget:** ~5.5 (LLM) + 0.6 (STT) + ~0.7 (CUDA ctx/overhead) ≈ **6.5–7GB** → fits 8GB with margin.

**Sizing options on this card:**
- **Sweet spot:** Qwen2.5-7B-Instruct Q4_K_M. Reliable JSON + slot-filling, fits with STT/TTS.
- **Headroom/lower-latency fallback:** Llama-3.2-3B / Qwen2.5-3B Q4 (~2.5GB) — fine here because
  the constrained JSON tool-calling + validate-and-re-ask loop keeps the reasoning load light.
- **Text-first phase only:** STT/TTS aren't loaded, so give the LLM most of the card — run the
  7B at Q5_K_M/Q6 or extend context. Still avoid 14B.

**Concurrency:** v0 on 8GB is effectively **one concurrent call** — acceptable for a prototype.
Multi-call concurrency is a v1 GPU-scaling concern, not a v0 one.

**Blackwell setup gotcha (RTX 5060 = sm_120):** requires **CUDA 12.8+** and Blackwell-aware
builds — a current **Ollama** release (bundles its own CUDA), **PyTorch cu128**, and a recent
**faster-whisper/CTranslate2**. Older wheels fail with "no kernel image available for device."

**Phasing within v0:** build text mode first (chat box ↔ LLM ↔ tools ↔ SQLite). Only
after the state machine and booking are correct do you add STT/TTS/VAD. Voice adds latency
and transcription noise that mask state-machine bugs.

### 1.4 The booking state machine

```
                 ┌─────────┐
                 │  GREET  │
                 └────┬────┘
                      ▼
              ┌───────────────┐   emergency red flag (any state)
   ┌─────────│ CLASSIFY      │──────────────────────────────────┐
   │         │ INTENT        │                                   │
   │         └───────┬───────┘                                   ▼
   │   book          │ faq / human                       ┌──────────────┐
   │                 └──────────────────────►            │  ESCALATE /  │
   │                                                      │  911 GUIDANCE│
   ▼                                                      └──────────────┘
┌──────────────────┐  missing field (re-ask, keep prior answers)
│ CAPTURE FIELDS   │◄─────────────┐
│ intent, patient, │              │
│ visit type,      │──────────────┘
│ preferred time   │
└────────┬─────────┘
         ▼
┌──────────────────┐  no open slot → re-offer / widen search
│ CHECK            │◄─────────────┐
│ AVAILABILITY     │──────────────┘
│ (offer 2–3)      │
└────────┬─────────┘
         ▼
┌──────────────────┐  caller wants different time → back to AVAILABILITY
│ CONFIRM          │──────────────┐
│ (read back slot) │◄─────────────┘
└────────┬─────────┘
         ▼  yes
┌──────────────────┐  write fails / slot now taken (race) → back to AVAILABILITY
│ BOOK (atomic)    │──────────────┐
└────────┬─────────┘◄─────────────┘
         ▼  write succeeded
┌──────────────────┐
│ READ-BACK & CLOSE│   "You're booked" is emitted ONLY here, after a confirmed write
└──────────────────┘
```

State lives in a `ConversationState` object, **not** in the LLM context alone. Slots are
filled by the LLM but stored and validated by code. The LLM gets the current state + the
user turn each loop; it returns either a slot-fill update or a proposed tool call as JSON.

### 1.5 Tool-calling contract (reliability mechanism)

Every LLM turn is constrained to emit one of a small set of JSON shapes:

```json
// slot update
{ "action": "update_slots", "slots": { "visit_type": "sick_visit", "preferred_date": "2026-07-02" } }

// tool call
{ "action": "call_tool", "tool": "check_availability",
  "args": { "visit_type": "sick_visit", "date": "2026-07-02", "urgency": "routine" } }

// speak to user
{ "action": "say", "text": "What day works best for you?" }
```

Validate-and-re-ask loop: parse JSON → validate against schema → if invalid, re-prompt the
LLM with the error (max N retries) → on repeated failure, fall back to a human/escape path.
This is what makes a 7B–14B local model reliable enough to book.

### 1.6 Atomic booking (the heart of v0)

```sql
-- slots(id, start_ts, visit_type, provider, status)  status ∈ {open, held, booked}
-- appointments(id, slot_id UNIQUE, patient_name, dob, phone, reason, created_ts)

BEGIN IMMEDIATE;                              -- write lock
  SELECT status FROM slots WHERE id=? AND status='open';   -- still open?
  -- if no row → abort, return SLOT_TAKEN (state machine re-offers)
  UPDATE slots SET status='booked' WHERE id=? AND status='open';
  INSERT INTO appointments(slot_id, ...) VALUES(?, ...);   -- UNIQUE(slot_id) is the
COMMIT;                                                    -- double-book backstop
```

Two guards make double-booking impossible: the `BEGIN IMMEDIATE` transaction + status
check, and the `UNIQUE(slot_id)` constraint as a hard backstop. The function returns a
typed result (`BOOKED` / `SLOT_TAKEN` / `ERROR`) that the state machine acts on — the LLM
only narrates the result.

### 1.7 v0 acceptance harness

A scripted test suite (no model) drives the tools layer directly to prove invariants, plus
LLM-in-the-loop conversation tests:
- Happy path books and the slot vanishes from the next availability query.
- Concurrent booking of the same slot → exactly one `BOOKED`, one `SLOT_TAKEN`.
- Missing field → re-ask preserves previously captured slots.
- Red-flag phrase at any turn → flow halts, no appointment written.

---

## 2. v1 — Clinic MVP Architecture

Same brain, real edges. The orchestrator, state machine, safety gate, and tool/provider
interfaces are reused; the telephony, scheduling, and notification adapters become real,
and compliance/observability are added.

### 2.1 System diagram

```
 PSTN ──SIP trunk──► Telephony (Asterisk/FreeSWITCH) ──RTP audio──┐
   ▲   (carrier)         │ call control, recording, transfer      │
   │                     │                                        ▼
 SMS/email ◄── Notifier   │                          ┌────────────────────────┐
   ▲                      │                          │ Orchestrator (Pipecat/  │
   │ transfer            ▼                          │  LiveKit Agents)        │
 Staff phone ◄── warm/cold transfer ─────────────── │  VAD→STT→[Safety]→[FSM] │
                                                     │  →LLM→TTS, barge-in     │
                                                     └───────┬─────────────────┘
                                                             │ tool calls
                            ┌────────────────────────────────┼───────────────┐
                            ▼                ▼                ▼               ▼
                     SchedulingProvider   IdentityProvider  Notifier   KnowledgeBase
                            │             (patient lookup)  (SMS/email)  (FAQ/RAG)
                            ▼
                   EHR/Calendar adapter (one system) ──► FHIR or vendor API
                            │
        ┌───────────────────┴───────────────────────────────────────────────┐
        ▼                          ▼                         ▼                ▼
   Postgres (app data)      Object store (recordings)   Audit log     Staff Dashboard
   calls, patients,         encrypted at rest           (append-only)  (web app:
   triage, appts                                                       call list,
                                                                       transcripts,
                                                                       escalation queue)
```

### 2.2 What changes from v0 → v1

| Concern | v0 | v1 |
|---|---|---|
| Transport | Browser WebRTC / text | PSTN via SIP trunk + Asterisk/FreeSWITCH |
| STT/LLM/TTS | Local, correctness-first | Same models, latency-tuned; medical-vocab ASR |
| Scheduling | SQLite adapter | Real EHR/calendar adapter behind same interface |
| Identity | None | Patient lookup (new vs existing) |
| Notifications | None | SMS/email confirmation |
| Escalation | Log only | Warm/cold transfer to staff + voicemail fallback |
| Safety | Regex red-flag | Reviewed clinical ruleset + classifier, audited |
| Data store | SQLite | Postgres + encrypted object store for recordings |
| Compliance | N/A (no PHI) | HIPAA: encryption, BAAs, audit log, retention, consent |
| Observability | Console | Per-component latency, dashboards, alerting |

### 2.3 Latency budget (NFR ≤ 800ms end-to-end, stretch ≤ 600ms)

```
caller stops speaking
   │  VAD endpointing            ~50–100ms
   ▼
  STT final partial              ~100–200ms (streaming)
   ▼
  Safety gate (parallel, cheap)  ~5–20ms
   ▼
  LLM time-to-first-token        ~150–300ms
   ▼
  TTS first audio chunk          ~100–200ms
   ▼
  network/jitter buffer          ~50–100ms
   = ~450–800ms
```

Levers: stream everything (no waiting for full sentences), speak the first chunk while the
rest generates, run the safety gate in parallel with LLM scheduling, keep models warm,
co-locate STT/LLM/TTS on the same host/GPU to avoid network hops. Continuous per-component
latency monitoring is a first-class requirement.

### 2.4 Safety gate (v1 detail)

- Runs as a **separate, fast path on every transcribed turn**, before and independent of the
  main LLM reasoning. Implementation: keyword/phrase rules + a lightweight classifier, both
  derived from a **clinician-owned, signed-off ruleset** (not invented by the model).
- On red-flag: interrupt TTS immediately, deliver scripted 911/ER guidance, write a
  `TriageDecision` + `EscalationEvent`, alert staff. The state machine cannot transition
  back into booking after a confirmed red flag.
- Conservative bias: ambiguous urgency rounds **up** a tier. Mental-health crisis statements
  route to a dedicated resources-and-escalation path, never a scheduling dead-end.
- Every triage decision is logged with transcript for clinical audit (release-blocking
  guardrail metric: zero mishandled emergencies).

### 2.5 Failure & degradation policy

- Any component down (STT/LLM/TTS/EHR) → graceful degradation to voicemail or
  forward-to-staff. **No call silently drops** (99.9% availability target).
- EHR write fails → re-offer or escalate with summary; never claim a booking that didn't
  persist.
- Max-retry on misunderstanding → human handoff with transcript + summary.

---

## 3. Data Model

Core entities (PRD §11.3), expressed as the v1 relational schema; v0 uses the subset in
SQLite.

```
Call(id, started_at, ended_at, channel, recording_uri, outcome,
     latency_metrics_json)                         outcome ∈ {booked,escalated,abandoned,faq}
Patient(id, name, dob, phone, is_existing, ehr_patient_id)
IntentEvent(id, call_id→Call, intent, confidence, turn_ts)
TriageDecision(id, call_id→Call, tier, red_flags_json, confidence, turn_ts)
                                                    tier ∈ {emergency,urgent,routine}
VisitType(id, name, default_duration, provider_or_specialty, in_person|telehealth)
Slot(id, start_ts, end_ts, visit_type_id→VisitType, provider, status)
                                                    status ∈ {open,held,booked}
Appointment(id, slot_id→Slot UNIQUE, patient_id→Patient, call_id→Call,
            reason, confirmation_sent_at, created_at)
EscalationEvent(id, call_id→Call, type, reason, summary, transcript_uri, created_at)
                                                    type ∈ {emergency,human_request,low_conf,distress}
```

Key invariants: `Appointment.slot_id` is UNIQUE (no double-book); a `Call` with a red-flag
`TriageDecision` cannot also own an `Appointment`; every escalation carries a transcript
pointer.

---

## 4. The Provider Interfaces (architectural insurance)

These narrow contracts are what let the same core run on a fake calendar in v0 and Epic in
the vision phase.

```python
class SchedulingProvider(Protocol):
    def find_open_slots(self, visit_type, urgency, date_range) -> list[Slot]: ...
    def book(self, slot_id, patient, reason) -> BookResult:  # atomic, returns BOOKED|SLOT_TAKEN|ERROR
    def cancel(self, appointment_id) -> Result: ...
    def reschedule(self, appointment_id, new_slot_id) -> BookResult: ...

# v0:  SqliteCalendarAdapter
# v1:  one real EHR/calendar adapter (clinic's chosen system)
# Vision: FhirSchedulingAdapter + per-vendor adapters (Epic, Oracle Health, athenahealth, ...)

class TelephonyProvider(Protocol): ...    # browser-webrtc (v0) → SIP/Asterisk (v1)
class STT(Protocol): ...                  # faster-whisper local
class TTS(Protocol): ...                  # piper/kokoro local
class LLMClient(Protocol): ...            # ollama local (open-weight)
class Notifier(Protocol): ...             # no-op (v0) → SMS/email (v1)
class IdentityProvider(Protocol): ...     # none (v0) → patient lookup (v1)
```

---

## 5. Vision-Phase Extensions (design-for, don't build yet)

- **Multi-EHR via FHIR** (`Appointment`, `Slot`, `Schedule`, `Patient` resources) + vendor
  adapters — all behind the existing `SchedulingProvider` interface.
- **Specialty-aware scheduling rules** (ortho vs derm vs primary care) as configurable
  routing policy on top of `VisitType`.
- **Protocol-grade triage** (Schmitt-Thompson–style decision trees) under nurse oversight,
  replacing the conservative v1 subset — still behind the deterministic safety gate.
- **Multi-language** — swap STT/TTS/LLM locale; the state machine is language-agnostic.
- **Outbound** (reminders, recalls, no-show recovery, waitlist fill) — same orchestrator
  driving outbound call sessions.
- **Analytics** — demand patterns, no-show prediction, schedule optimization off the
  `Call`/`Appointment`/`TriageDecision` history.

---

## 6. Deployment View

| Tier | Where it runs | Data |
|---|---|---|
| v0 | One developer GPU box; browser + Python process + SQLite file | Synthetic only, no PHI |
| v1 | Self-hosted: telephony host + GPU inference host + app/DB host (on-prem or private cloud under BAA) | Real PHI — encrypted in transit + at rest, audit-logged, retention policy, SOC 2 as maturity target |

Cost model to beat (PRD §12): managed-API benchmark ~$0.15–$0.30/min all-in; self-hosting
shifts cost to GPU/infra + ops. Track effective cost/minute vs. staff-cost-displaced.

---

## 7. Build Order (maps to PRD §16 roadmap)

1. **v0-text:** state machine + tools + SQLite atomic booking + JSON tool-calling +
   acceptance harness. *Done = books end-to-end in text, 3 edge cases pass.*
2. **v0-voice:** add faster-whisper + Piper/Kokoro + Pipecat VAD/barge-in over WebRTC.
3. **v1-foundation:** Asterisk/FreeSWITCH + SIP trunk; real scheduling adapter; clinical
   red-flag ruleset; consent/compliance baseline; Postgres; observability.
4. **v1-MVP:** identity capture, notifications, warm/cold transfer + voicemail, staff
   dashboard. Pilot one clinic, English-only.
5. **Hardening (P1):** returning-patient recognition, waitlist, configurable FAQ/RAG,
   after-call summary to record.
6. **Scale (P2):** multi-EHR/FHIR, multi-specialty, multi-language, outbound, eligibility,
   protocol-grade triage.

---

## 8. Open Questions That Shape Architecture (from PRD §15)

- **v1 scheduling target** (which EHR/calendar) → determines the first real
  `SchedulingProvider` adapter. *Blocking for FR-7/FR-8.*
- **Consent/recording jurisdiction** (one- vs two-party) → telephony + greeting logic.
- **Owner of red-flag ruleset** → safety gate content + governance.
- **Warm vs cold transfer** capability of chosen telephony/staff setup → escalation design.
- **Pilot clinic + baselines** → success measurement.
```
