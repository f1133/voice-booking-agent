# PRD: AI Voice Calling Agent — Clinic & Hospital Receptionist

| | |
|---|---|
| **Product** | AI Voice Calling Agent ("AI Receptionist") |
| **Version** | 1.0 (Draft) |
| **Date** | June 29, 2026 |
| **Owner** | Aditya |
| **Status** | Draft for review |
| **Scope** | Local v0 prototype → clinic MVP (v1) → full vision |

---

## 1. TL;DR

When a patient calls a clinic or hospital to book an appointment, the AI Voice Calling Agent answers like a human receptionist: it greets the caller, understands what they need, gauges how urgent it is, identifies the right type of care, checks real availability, and books the appointment — end to end, 24/7, with no hold time. Anything it can't safely handle (medical emergencies, complex cases, frustrated callers) is escalated immediately to a human or to emergency services.

The MVP focuses on the highest-value, lowest-risk slice: **inbound appointment booking for a single clinic, in English, with conservative safety triage and a clean handoff to staff.** Later phases add EHR write-back, multi-specialty scheduling, multi-language support, outbound calls, and richer clinical triage.

**Build path:** the first thing we build is a **local, zero-cost v0 prototype** — every model self-hosted on a single GPU, a browser/web (or text-first) demo instead of phone lines, and a fake local calendar instead of a real EHR. Its one success criterion is **booking an appointment end to end**: collect the request, find an open slot, write it, mark the slot taken so it can't be double-booked, and read back a confirmation. The clinic-grade MVP (real telephony, real scheduling system, compliance) is built on top of that proven core.

---

## 2. Problem Statement

Front-desk phone lines are a chronic bottleneck in healthcare. A large share of patient calls go unanswered or land in long hold queues, especially during peak hours and after hours. Every missed call is a missed appointment, a frustrated patient, and lost revenue — and staff who do answer are pulled away from in-person patients to handle routine scheduling.

Patients experience this as long waits, voicemail black holes, and "please call back during business hours." Clinics experience it as lost bookings, overworked staff, and no-shows. Comparable AI receptionist deployments have cut hold times by ~89% and captured over $1M in after-hours bookings at a single multi-provider organization, while raising patient satisfaction scores from 2.6 to 4.4 out of 5 — a strong signal that automating this layer is both wanted and valuable.

**The cost of not solving it:** lost revenue from unbooked appointments, patient churn to more responsive providers, staff burnout, and degraded patient experience.

---

## 3. Goals

1. **Answer every call instantly** — 0 hold time, 24/7 availability, including nights/weekends/overflow.
2. **Autonomously complete routine bookings** — handle the full intent → triage → care-type → availability → book flow without human help for the common cases.
3. **Be safe by design** — reliably detect emergencies and high-risk symptoms and escalate to humans / 911 rather than scheduling.
4. **Reduce front-desk load** — deflect a meaningful share of routine scheduling calls away from staff.
5. **Capture lost revenue** — convert after-hours and overflow calls into booked appointments.

*Goals are outcomes, not outputs — see Success Metrics (§13) for targets.*

---

## 4. Non-Goals (v1)

1. **Clinical diagnosis or medical advice.** The agent triages *urgency for routing*, not *diagnosis*. It never tells a patient what condition they have or what treatment to take. *(Out of scope: regulatory risk, clinical liability.)*
2. **Full nurse-grade clinical triage.** v1 uses a conservative red-flag + urgency model, not a complete symptom-by-symptom triage protocol library. *(Deferred to vision phase.)*
3. **Outbound calling** (reminders, recalls, follow-ups). *(Fast-follow, not v1.)*
4. **Billing, insurance eligibility, and prior authorization.** *(Separate initiative.)*
5. **Multi-language support.** v1 is English-only. *(Phase 2.)*
6. **Deep bi-directional EHR write-back across many vendors.** v1 integrates with one scheduling system; broad EHR coverage comes later. *(Phased.)*

---

## 5. Target Users & Personas

**Primary — The Patient (caller).** Wants to book/reschedule/cancel quickly, be understood, and get to a human if needed. May be elderly, stressed, unwell, non-native English speaker, or calling on behalf of someone else.

**Primary — Front-Desk / Practice Staff.** Wants fewer routine calls, accurate bookings written into their system, clear escalations, and zero double-bookings. This is the buyer's day-to-day user.

**Secondary — Practice Manager / Clinic Owner (buyer).** Cares about captured revenue, reduced staffing cost, patient satisfaction, compliance, and a dashboard showing what the agent did.

**Secondary — Clinical Staff (nurses/providers).** Cares that emergencies are never mishandled and that the agent's urgency routing maps correctly to their schedule rules.

---

## 6. User Stories

**Patient**
- As a patient, I want to call and book an appointment in one conversation so that I don't have to wait on hold or call back.
- As a patient describing my symptoms, I want the agent to understand how soon I need to be seen so that I get an appropriately urgent slot.
- As a patient with an emergency, I want to be told immediately to call 911 / go to the ER so that I'm not stuck in a scheduling flow.
- As a returning patient, I want the agent to recognize me so that I don't repeat all my details.
- As a frustrated or confused caller, I want to reach a human easily so that I'm never trapped with the bot.

**Staff**
- As front-desk staff, I want bookings the agent makes to appear correctly in our scheduling system so that I don't have to re-enter them.
- As staff, I want clear, prioritized escalations (with a transcript/summary) so that I can pick up where the agent left off.
- As a practice manager, I want a dashboard of calls handled, booked, and escalated so that I can measure impact.

---

## 7. Scope: Local v0 → MVP (v1) → Vision

### 7.1 v0 — Local prototype (free, local models, end-to-end booking)

The first milestone is a learning/portfolio-grade prototype that proves the core booking loop with **zero spend**, running **entirely on one local GPU**.

- **Runs locally on one GPU** — no cloud APIs, no per-minute fees, no vendor accounts.
- **Web/browser demo, not phone lines** — audio over the browser (WebRTC), or text-only first; no telephony provider, phone number, or SIP cost.
- **Fake calendar, not a real EHR** — availability and bookings live in a local SQLite/JSON store, rendered as a live table that visibly updates.
- **No real patients → no PHI → no HIPAA scope** at this tier.
- **Text-first, then voice** — prove the booking state machine and tool-calling in text, then layer STT/TTS on top (voice adds latency and transcription noise that masks state-machine bugs).
- **Scope-limited flow:** greet → capture intent → collect required fields → check availability → propose slots → confirm → write booking → read-back. A lightweight red-flag interrupt is kept as a cheap nod to the domain; full triage is deferred.
- **Edge cases that prove robustness:** requested slot already taken → offer alternatives; missing required field → re-ask without losing prior answers; double-book attempt → refuse.

### 7.2 "Books end to end" — v0 definition of done

A booking counts as complete only when **all** of the following hold:

1. The agent extracts the required fields (intent, patient type, provider/visit type, preferred date/time) into a **validated structured record**.
2. It queries the local calendar and offers only **genuinely open** slots.
3. On confirmation, the appointment is **written to the store** and the slot is **atomically marked taken** so it cannot be double-booked.
4. The agent **reads back** the confirmed appointment (who, what, when).
5. The booked slot **no longer appears** in availability on the next query.

If any step fails, the agent recovers (re-ask / re-offer) rather than claiming success — **the model never says "you're booked" unless the write actually happened.**

### 7.3 MVP (v1) — "Book the common case, escalate the rest, safely"

- **Inbound only**, single clinic/location, English.
- **Intents:** new appointment, reschedule, cancel, basic FAQ (hours, location, what to bring), "talk to a human."
- **Safety triage:** detect emergency red flags → immediate 911/ER guidance + escalation. Detect "needs human" → warm/cold transfer.
- **Urgency routing (non-emergency):** classify as *urgent (see soon)* vs *routine* to choose appropriate slot timing/visit type.
- **Care-type routing:** map request to a configured visit type (e.g., sick visit, routine check-up, follow-up, telehealth vs in-person) within the clinic's defined catalog.
- **Availability + booking:** read real availability and book into **one** scheduling system (start with the clinic's calendar or one EHR via API).
- **Identity capture:** name, DOB, phone, reason for visit, new vs existing patient.
- **Confirmation:** read back details, confirm, send SMS/email confirmation.
- **Handoff:** transfer to staff with call summary + transcript; voicemail fallback if no one available.
- **Compliance baseline:** call recording disclosure/consent, encryption, audit log, signed BAAs with vendors.
- **Staff dashboard (basic):** call list, outcomes, transcripts, escalation queue.

### 7.4 Vision (later phases)

- Full bi-directional EHR write-back across major vendors (Epic, Cerner/Oracle Health, athenahealth, eClinicalWorks, etc.) via FHIR/vendor APIs.
- Multi-specialty, specialty-aware scheduling rules (e.g., ortho vs derm vs primary care logic).
- Richer clinical triage aligned to established telephone-triage protocol logic (e.g., Schmitt-Thompson–style decision trees) with nurse oversight.
- Multi-language (20+ languages).
- Outbound: reminders, recalls, no-show recovery, waitlist fill, referral/fax automation.
- Insurance eligibility & pre-visit intake.
- Proactive analytics (demand patterns, schedule optimization, no-show prediction).

---

## 8. Core Experience: The Call Flow

The heart of the product is a natural, low-latency phone conversation. The agent runs a state machine under the hood while sounding conversational. *(In the local v0, the same state machine runs over a browser/text interface instead of phone lines — steps 6–9, capture → availability → book → confirm, are the prototype's core.)*

```
1. GREET & IDENTIFY INTENT
   "Thanks for calling [Clinic]. This is the virtual assistant — how can I help?"
   → Classify intent: book / reschedule / cancel / FAQ / human / emergency

2. SAFETY GATE (runs continuously, highest priority)
   → Scan every turn for emergency red flags (see §9).
   → If red flag: STOP scheduling → "This may be an emergency. Please hang up and
     call 911 / go to the nearest ER now." → log + escalate/notify.
   → If caller asks for a human or is distressed: transfer.

3. UNDERSTAND THE NEED (for booking intent)
   → Reason for visit (free-text symptom/need) → map to care type.
   → Ask targeted, bounded follow-ups (duration, severity cues) — NOT a diagnosis interview.

4. CLASSIFY URGENCY (non-emergency)
   → Urgent (needs to be seen soon) vs Routine → drives slot timing & visit type.

5. DETERMINE CARE TYPE
   → Visit type (sick visit / check-up / follow-up / telehealth / in-person),
     provider/specialty, new vs existing patient → from the clinic's configured catalog.

6. CAPTURE / VERIFY IDENTITY
   → Name, DOB, callback number, existing vs new patient.

7. CHECK AVAILABILITY
   → Query scheduling system for matching slots (care type + urgency + provider rules).
   → Offer 2–3 concrete options; handle "earlier?", "a different day?", etc.

8. BOOK
   → Write appointment to the scheduling system; handle conflicts/failures gracefully.

9. CONFIRM & CLOSE
   → Read back details, confirm, send SMS/email confirmation, offer anything else.

10. FALLBACK (any step)
    → Low confidence / repeated misunderstanding / explicit request → human transfer
      or voicemail with summary. Never trap the caller.
```

---

## 9. Clinical Safety & Severity Logic

> **Design principle:** The agent is a *router*, not a clinician. It errs on the side of caution. When in doubt, it escalates up (to a human or emergency services), never down.

### 9.1 Three-tier severity model (v1)

| Tier | Definition | Agent action |
|---|---|---|
| **Emergency (red flag)** | Symptoms suggesting a life- or limb-threatening condition | Stop scheduling. Instruct to call 911 / go to ER. Log and alert staff. Do **not** book a routine slot. |
| **Urgent** | Needs to be seen soon but not an emergency | Route to soonest appropriate slot / urgent visit type / same-day or telehealth per clinic rules. |
| **Routine** | Standard, non-time-critical | Route to standard visit type and normal availability. |

This is intentionally simpler than formal ED triage scales (e.g., the **Emergency Severity Index**, a 5-level system) and nurse phone-triage protocol libraries (e.g., **Schmitt-Thompson**, used by a large majority of U.S. triage nurses). Those are the reference targets for the *vision* phase under clinical oversight — v1 uses a conservative subset focused on safe routing.

### 9.2 Red-flag detection (non-exhaustive, configured with clinical input)

Examples that trigger the Emergency tier: chest pain/pressure, difficulty breathing, signs of stroke (face droop, arm weakness, slurred speech), severe bleeding, suicidal/self-harm statements, severe allergic reaction, symptoms in infants with high fever, pregnancy emergencies, loss of consciousness. The exact list is a configurable clinical ruleset reviewed and signed off by the clinic's clinical staff — not invented by the model alone.

### 9.3 Safety guardrails

- **Conservative bias:** ambiguous urgency rounds *up* a tier.
- **No diagnosis / no medical advice:** the agent never names a condition or recommends treatment.
- **Human-in-the-loop:** emergency and low-confidence cases always reach a human path.
- **Mental-health sensitivity:** crisis statements trigger a dedicated, careful response path (resources + escalation), never a scheduling dead-end.
- **Auditability:** every triage decision is logged with the transcript for clinical review.
- **Clinical governance:** red-flag rules and urgency mappings are owned and approved by clinical staff and reviewed regularly.

---

## 10. Functional Requirements

Prioritized with MoSCoW / P0–P2. Each P0 includes acceptance criteria.

*The **local v0** implements a focused subset over a web/text interface — FR-2 (intent), FR-6 (capture), FR-7 (availability), FR-8 (booking), FR-9 (confirmation) — held to the end-to-end definition in §7.2. Telephony FRs (FR-1, FR-11, FR-12) and the staff dashboard (FR-13) arrive at the clinic-MVP stage.*

### P0 — Must-have (MVP)

**FR-1: Answer inbound calls 24/7.**
- Given a patient calls the clinic number, when the line connects, then the agent answers within 2 rings and greets the caller.

**FR-2: Intent recognition.**
- Given the caller states a need, when they finish speaking, then the agent classifies intent (book / reschedule / cancel / FAQ / human / emergency) with a confidence score; low confidence triggers a clarifying question or handoff.

**FR-3: Emergency red-flag detection & escalation.**
- Given any caller turn contains a configured red-flag symptom, when detected, then the agent stops the scheduling flow, delivers 911/ER guidance, and logs + alerts — within the same turn.
- Negative case: the agent must **not** continue collecting booking details after a red flag is confirmed.

**FR-4: Urgency classification (non-emergency).**
- Given a non-emergency reason for visit, when assessed, then the agent assigns Urgent or Routine and selects slot timing accordingly per clinic rules.

**FR-5: Care-type / visit-type routing.**
- Given the reason for visit, when mapped, then the agent selects a visit type, provider/specialty, and new-vs-existing-patient path from the clinic's configured catalog.

**FR-6: Identity capture & verification.**
- Given a booking proceeds, when collecting details, then the agent captures and reads back name, DOB, callback number, and reason for visit, and confirms accuracy.

**FR-7: Real-time availability check.**
- Given a care type + urgency, when the agent queries the scheduling system, then it returns currently-open matching slots and offers 2–3 concrete options.

**FR-8: Booking / write to scheduling system.**
- Given the patient selects a slot, when the agent books, then the appointment is written to the scheduling system and a conflict/failure is handled gracefully (re-offer, or escalate).

**FR-9: Confirmation.**
- Given a successful booking, when the agent closes, then it reads back the appointment and sends an SMS/email confirmation.

**FR-10: Human handoff & fallback.**
- Given low confidence, repeated misunderstanding, distress, or an explicit request, when triggered, then the agent transfers to a human (or voicemail) with a call summary + transcript. The caller is never trapped in a loop.

**FR-11: Barge-in / interruption handling.**
- Given the caller speaks while the agent is talking, when detected, then the agent stops and listens.

**FR-12: Recording consent & disclosure.**
- Given a call starts, when greeting, then the agent discloses AI use and recording per jurisdiction, and captures consent where required.

**FR-13: Staff dashboard (basic).**
- Calls list, outcome (booked / escalated / abandoned), transcript, and an escalation queue.

### P1 — Should-have (fast follow)

- Reschedule & cancel with identity verification.
- Returning-patient recognition (skip redundant questions).
- Waitlist / "notify me if earlier opens up."
- Configurable FAQ knowledge base per clinic.
- After-call summary auto-written to the patient record.

### P2 — Future considerations (design for, don't build yet)

- Multi-language.
- Outbound calls (reminders, recalls, no-show recovery).
- Multi-location / multi-specialty routing.
- Insurance eligibility & pre-visit intake.
- Protocol-grade clinical triage with nurse oversight.

---

## 11. Technical Architecture

### 11.1 Pipeline (the voice loop)

A real-time voice agent is fundamentally **STT → LLM → TTS** wrapped in orchestration, connected to telephony on one end and clinic systems on the other.

```
 Patient ──PSTN/SIP──> Telephony ──audio──> STT ──text──> Orchestrator/LLM
                                                              │
                          ┌───────────────────────────────────┤ (function calling)
                          ▼                                   ▼
                  Tools / Integrations                  Dialog + Safety policy
            (availability, booking, identity,           (state machine,
             SMS, transfer, knowledge base)              red-flag gate)
                          │                                   │
                          └──────────► response text ──> TTS ──audio──> Patient
```

**Component choices — all open-source, self-hosted, built in-house** (decision: **build, not buy**):

- **Telephony / SIP:** self-hosted open-source telephony (**Asterisk** or **FreeSWITCH**) for call control, transfer, and recording, connected to a carrier SIP trunk for PSTN access. *(A managed provider such as Twilio/Telnyx is retained only as a pilot fallback if self-hosting delays launch.)*
- **Speech-to-text (STT):** self-hosted open-source streaming ASR — e.g., **Whisper / faster-whisper** or **NVIDIA Parakeet** — tuned for medical vocabulary.
- **LLM / reasoning:** a self-hosted **open-weight** model (e.g., **Llama**, **Qwen**, or **Mistral**) with function calling, governed by a deterministic safety/state layer (the LLM does not freelance on emergencies).
- **Text-to-speech (TTS):** a self-hosted open-source streaming voice (e.g., **Kokoro**, **Coqui XTTS**, **Orpheus**, or **Piper**).
- **Orchestration:** built in-house on an **open-source framework** (**Pipecat** or **LiveKit Agents**) — no managed voice-agent platform. Orchestration quality (VAD, turn-taking, interruption handling, function-call latency) is what separates production-grade from demo.
- **Tools layer:** scheduling/availability, booking, identity lookup, SMS/email, warm transfer, FAQ retrieval.

**v0 local prototype stack (zero-cost, single GPU):** browser mic/speaker over **WebRTC** — or a text box first, no telephony; **faster-whisper** (STT); a local **open-weight LLM via Ollama** (e.g., Llama 3.1 8B or Qwen2.5 7B–14B, sized to available VRAM) for intent + slot-filling + tool selection; **Piper** or **Kokoro** (TTS); **Pipecat** for orchestration (VAD, turn-taking); and a **local SQLite calendar** as the booking store. Tool-calling is made reliable with **structured/JSON output** (Ollama `format=json` or a llama.cpp grammar) plus a validate-and-re-ask loop — **the model proposes, the code writes** (the LLM never free-texts a booking).

### 11.2 Scheduling / EHR integration

- **v0:** a **local SQLite/JSON calendar** stands in for the EHR — read availability + **atomic write with a double-booking guard**, rendered as a live table for demo and debugging.
- **v1:** integrate with **one** scheduling source (clinic calendar or a single EHR's scheduling API). Read availability + write appointment.
- **Vision:** standardize on **FHIR** (Appointment, Slot, Schedule, Patient resources) where supported, plus vendor-specific adapters for major EHRs (Epic, Cerner/Oracle Health, athenahealth, eClinicalWorks, etc.).
- **Abstraction:** put the local calendar and every real EHR behind **one scheduling-provider interface** — the fake calendar is just the first adapter — so swapping in a real system is not a rewrite (P2 architectural insurance).

### 11.3 Data model (core entities)

`Call` (id, timestamp, recording, transcript, outcome, latency metrics), `Caller/Patient` (name, DOB, phone, new/existing, EHR id), `IntentEvent`, `TriageDecision` (tier, red flags, confidence), `VisitType`, `Slot`, `Appointment`, `EscalationEvent`.

---

## 12. Non-Functional Requirements

**Latency (most critical for "human-like").**
- Target **end-to-end voice response latency ≤ 800ms**, stretch ≤ 600ms (industry-competitive 2026 voice agents run ~500–800ms; >1s feels robotic). Budget across STT + LLM TTFT (~150–300ms) + TTS + network.
- Continuous monitoring with per-component latency breakdown.
- **v0 exception:** for the local end-to-end-booking prototype, *correctness beats speed* — a slightly slow but correct booking is the goal, so the latency bar is relaxed and a larger, more reliable local model is preferred.

**Reliability & availability.**
- 99.9% uptime for the voice service; graceful degradation to voicemail/forward-to-staff if any component fails. No call should ever silently drop.

**Comprehension accuracy.**
- High ASR accuracy on names, DOBs, and medical terms; always read back critical details for confirmation.

**Security & compliance (HIPAA).**
- All PHI encrypted in transit and at rest; signed **BAAs** with every vendor touching PHI; access controls + full audit logging; PII/PHI redaction in logs where possible; data retention policy; SOC 2 as a maturity target.
- Recording/AI disclosure and consent per jurisdiction (one- vs two-party consent states).
- **v0:** runs locally with **no real patient data**, so HIPAA/BAA obligations don't apply to the prototype; they attach the moment real PHI enters at the clinic-MVP stage.

**Scalability & cost.**
- Handle concurrent call spikes (peak-hour + after-hours overflow). Self-hosting open-source models shifts cost from per-minute API fees to **GPU/infrastructure + ops**; the managed-API benchmark to beat is ~$0.15–$0.30/min all-in. Track effective cost/minute against staff-cost-displaced to prove ROI.

**Accessibility.**
- Handle elderly callers, accents, background noise, slow speech; patient pacing; easy repeat/clarify; frictionless human exit.

---

## 13. Success Metrics

### v0 (local prototype) — the metric that matters
- **End-to-end booking success rate:** % of booking attempts that end in a correctly written, confirmed, non-double-booked appointment — reliable on the happy path plus the three named edge cases (§7.1).
- **Paired guardrails:** **zero double-bookings**, and **zero false "you're booked" claims** (no success reported without a real write).

### Leading indicators (days–weeks)
- **Automation / containment rate:** % of calls fully handled without human (target: 60%+ of eligible routine calls in 90 days).
- **Booking completion rate:** % of booking-intent calls that end in a confirmed appointment.
- **Call answer rate / 0-hold:** % answered immediately (target: ~100%).
- **Escalation accuracy:** % of emergencies/red flags correctly escalated (target: ~100% — safety-critical, measured by audit).
- **Containment without complaint:** escalation rate, abandonment rate, repeat-call rate.
- **Latency:** median end-to-end response time (target ≤800ms).
- **Comprehension:** intent-classification accuracy, booking-detail error rate.

### Lagging indicators (weeks–months)
- **Missed-call reduction** vs. baseline.
- **After-hours / overflow revenue captured** (new bookings that would have been lost).
- **Staff time saved** (routine calls deflected × avg handle time).
- **Patient satisfaction (CSAT/NPS)** for AI-handled calls vs. baseline.
- **No-show rate** (once confirmations/reminders are added).

**Guardrail metric (never regress):** zero mishandled emergencies. This is a release-blocking metric.

---

## 14. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Mishandling a medical emergency** | Severe (safety/legal) | Conservative red-flag gate, escalate-up bias, clinical sign-off on rules, human-in-the-loop, continuous audit. |
| **Mis-booking / double-booking** | High | Read-back confirmation, atomic writes, conflict handling, write to system of record only. |
| **ASR errors on names/DOB/meds** | Medium | Medical-tuned ASR, mandatory read-back, spelling confirmation for critical fields. |
| **Latency makes it feel robotic** | Medium | Streaming pipeline, latency budget + monitoring, model/vendor selection on latency. |
| **HIPAA / privacy violation** | Severe | BAAs, encryption, access control, audit logs, minimal retention, redaction. |
| **Patient distrust of "talking to a bot"** | Medium | Upfront disclosure, natural voice, instant human exit, warm transfers. |
| **EHR integration brittleness** | High | Start with one system; abstraction layer; robust failure → escalate, never drop. |
| **Edge cases trap the caller** | Medium | Universal fallback to human/voicemail; no infinite loops; max-retry → handoff. |

---

## 15. Open Questions

- **[Stakeholder] Which scheduling system / EHR is the v1 integration target?** *Blocking for FR-7/FR-8.*
- **[Legal/Clinical] Consent & disclosure requirements** for the launch jurisdiction(s) (one- vs two-party recording states)? *Blocking for launch.*
- **[Clinical] Who owns and signs off on the red-flag ruleset and urgency→slot mappings?** *Blocking for FR-3/FR-4.*
- **[Product] Pilot clinic + baseline metrics** — which clinic, and what are current missed-call/hold-time numbers to measure against? *Blocking for success measurement.*
- **[Eng] Warm vs. cold transfer** capability with the chosen telephony provider and staff phone setup?

---

## 16. Roadmap / Phasing

**Phase 0 — Local v0 prototype (free, local).** Build the end-to-end booking loop on one GPU: text-first (mic/speaker added after), local LLM via Ollama + faster-whisper + Piper/Kokoro + Pipecat, SQLite fake calendar with a double-booking guard, structured-output tool-calling. **Done when it books end to end (per §7.2)** and handles the three named edge cases.

**Phase 1 — Foundation & pilot prep.** Stand up the self-hosted open-source stack (telephony + STT/LLM/TTS + orchestration), provision telephony number, one scheduling integration, clinical red-flag ruleset, consent/compliance baseline, baseline metrics from pilot clinic.

**Phase 2 — MVP (P0).** Inbound booking + reschedule/cancel, safety triage, urgency + care-type routing, availability + booking into one system, confirmations, human handoff, basic dashboard. Launch with one pilot clinic, English-only.

**Phase 3 — Hardening & fast-follows (P1).** Returning-patient recognition, waitlist, configurable FAQ, after-call summary to record, deeper analytics. Expand to a few more clinics.

**Phase 4 — Scale (P2).** Multi-EHR via FHIR + adapters, multi-specialty scheduling rules, multi-language, outbound (reminders/recalls/no-show recovery), insurance eligibility, protocol-grade triage with nurse oversight.

---

## 17. Sources

- [Schmitt-Thompson Clinical Content — nurse triage guidelines](https://www.stcc-triage.com/) · [Phone triage using Schmitt-Thompson protocols](https://intellatriage.com/blog/phone-triage-using-schmitt-thompson-protocols/) · [TriageLogic: What are Schmitt-Thompson protocols?](https://triagelogic.com/what-is-schmitt-thompson/)
- [How real-time voice AI works (STT → LLM → TTS) — Retell AI](https://www.retellai.com/blog/how-real-time-voice-ai-works-stt-llm-tts) · [The voice AI stack for building agents in 2026 — AssemblyAI](https://www.assemblyai.com/blog/the-voice-ai-stack-for-building-agents) · [Best AI voice platforms for virtual receptionists 2026 — Retell AI](https://www.retellai.com/blog/best-ai-voice-platforms-virtual-receptionists)
- [AI voice agents for healthcare 2026 — Rasa](https://rasa.com/blog/ai-voice-agents-for-healthcare-top-platforms-for-2026) · [AI voice agents in healthcare: handling patient calls 24/7 — Assort Health](https://www.assorthealth.com/blog/ai-voice-agent-in-healthcare) · [Assort Health](https://www.assorthealth.com/)
