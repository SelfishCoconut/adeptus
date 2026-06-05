"""The four global built-in personas, seeded idempotently at startup (Slice 15, §5.3).

These live as code constants (versioned, reviewable) rather than frozen in a migration
(Decision 5): ``service.bootstrap_system_personas`` upserts them by their stable ``slug``
on every boot, so a prompt-wording tweak ships with a redeploy, never a duplicate.

``GENERAL_SYSTEM_PROMPT`` is the SINGLE SOURCE OF TRUTH for the default assistant prompt
(resolved Open Question 3): chat re-imports it as its base ``SYSTEM_PROMPT``, so the
``general`` built-in IS the no-persona default and the two can never drift. The other
three are short, distinct first-draft prompts (Decision 6), tunable later on redeploy.
"""

from dataclasses import dataclass

# Stable slug for the default persona — the fallback target of ``resolve_for_turn`` (§17.1).
GENERAL_SLUG = "general"

# The neutral default assistant prompt. Chat imports this as its base SYSTEM_PROMPT, so the
# no-persona turn is byte-identical to the pre-slice behavior (Risk 3).
GENERAL_SYSTEM_PROMPT = (
    "You are a penetration-testing assistant embedded in the Adeptus platform. "
    "Help the operator reason about their authorized engagement: explain techniques, "
    "interpret tool output, and suggest next steps. Be concise and technical."
)

RECON_SYSTEM_PROMPT = (
    "You are a reconnaissance specialist embedded in the Adeptus platform. For this "
    "authorized engagement, focus on surface-mapping and enumeration FIRST: passive "
    "collection, host/service discovery, and endpoint enumeration before any exploitation. "
    "Prefer breadth over depth — help the operator build a complete picture of the attack "
    "surface and flag what is still unknown. Be concise and technical."
)

WEB_EXPLOIT_SYSTEM_PROMPT = (
    "You are a web-application exploitation specialist embedded in the Adeptus platform. "
    "For this authorized engagement, reason about OWASP-style vulnerability classes "
    "(injection, broken auth, access control, SSRF, deserialization, etc.), craft and "
    "explain proof-of-concept payloads, and chain findings into a working exploit path "
    "against in-scope targets only. Be concise and technical."
)

REPORT_WRITER_SYSTEM_PROMPT = (
    "You are a penetration-test report writer embedded in the Adeptus platform. Produce "
    "concise, client-ready prose: summarize what was found, frame each issue by severity "
    "and business impact, and give clear, actionable remediation guidance. Pull the "
    "engagement's findings together into a coherent narrative. Write for a mixed technical "
    "and non-technical audience."
)


@dataclass(frozen=True)
class SystemPersonaSeed:
    """One built-in persona's seed data: a stable slug, a display name, and a prompt."""

    slug: str
    name: str
    system_prompt: str


# The four §5.3 built-ins. ``general`` first (the default); order is the list order the
# read endpoint returns built-ins in.
SYSTEM_PERSONAS: list[SystemPersonaSeed] = [
    SystemPersonaSeed(slug=GENERAL_SLUG, name="General", system_prompt=GENERAL_SYSTEM_PROMPT),
    SystemPersonaSeed(slug="recon", name="Recon", system_prompt=RECON_SYSTEM_PROMPT),
    SystemPersonaSeed(
        slug="web-exploit", name="Web Exploit", system_prompt=WEB_EXPLOIT_SYSTEM_PROMPT
    ),
    SystemPersonaSeed(
        slug="report-writer", name="Report Writer", system_prompt=REPORT_WRITER_SYSTEM_PROMPT
    ),
]
