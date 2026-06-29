"""v0 — local text-first booking-loop prototype for the AI Voice Calling Agent.

See SYSTEM_DESIGN.md §1. The model proposes, the code disposes:
the LLM only extracts intent/slots; deterministic code owns every state
transition and the atomic booking write.
"""
