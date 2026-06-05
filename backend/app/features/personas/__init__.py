"""Personas feature: named AI personas with distinct system prompts (Slice 15, §5.3).

Four global read-only built-ins (``general`` / ``recon`` / ``web-exploit`` /
``report-writer``) seeded idempotently at startup, plus per-user custom personas each
user can create / edit / delete. Chat resolves a ``persona_id`` to a system prompt when
building a turn — the dependency flows chat → personas (personas never imports chat).
"""
