# 0005. License is Apache-2.0

Date: 2026-05-24
Status: Accepted

## Context

The project needs a license set on day 0. Adeptus is a pentest tool — choice of license affects what teams (including commercial pentest consultancies) can do with it. The main candidates are MIT, Apache-2.0, BSD-3, and a copyleft option like AGPL-3.0.

## Decision

The project is licensed under Apache-2.0. The `LICENSE` file is committed at the repo root on day 0. The required notice headers are NOT added to every source file (Apache-2.0 only requires the LICENSE file to be present in the distribution).

## Consequences

**Positive**
- Permissive enough that commercial pentest teams can use Adeptus on engagements without legal review
- Explicit patent grant — protects users from contributor patent claims
- Industry-standard, recognized by every tooling pipeline
- Compatible with the Apache-2.0-licensed Qwen model we ship as default

**Negative**
- Permissive licenses allow proprietary forks. We accept this tradeoff — the goal is adoption, not control.

**Neutral**
- License header policy can be revisited later if contributors want it

## Alternatives considered

- **MIT**: even more permissive but lacks the explicit patent grant.
- **AGPL-3.0**: would block proprietary SaaS forks but also blocks ordinary commercial use by consultancies, contrary to project goals.
- **BSD-3-Clause**: nearly equivalent to MIT; chose Apache for the patent grant.
