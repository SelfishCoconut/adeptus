# 0006. Frontend testing: Vitest + React Testing Library for components, Playwright for critical journeys

Date: 2026-05-24
Status: Accepted

## Context

Frontend testing has multiple layers (unit, component, integration, E2E) and over-investing in any one layer wastes time. The 3-pane workspace, Cytoscape graph, and chat panel are stateful and interactive — pure logic tests don't catch real bugs, but full E2E for every interaction is slow and brittle.

## Decision

Three-layer split:

1. **Unit (Vitest)** for hooks, pure functions, reducers, store logic. Colocated as `*.test.ts`.
2. **Component (Vitest + React Testing Library)** for every component with non-trivial behavior. Tests use `userEvent` for interactions, MSW for mocked API calls. Colocated as `*.test.tsx`. Target 60% coverage on `src/features/*`.
3. **E2E (Playwright)** ONLY for critical user journeys. The initial set: login + create engagement + run a light tool + generate report. New E2E tests require explicit justification — they exist to catch integration regressions that components-in-isolation can't.

## Consequences

**Positive**
- Fast feedback for the majority of changes (component tests run in seconds)
- E2E suite stays small enough to run reliably in CI
- Real-browser confidence on the journeys that matter

**Negative**
- Three tools to learn, but each is the standard tool for its layer
- Some bugs slip between layers (component test passes, E2E doesn't cover) — but the gap is narrow

**Neutral**
- Playwright traces are kept on failure in CI artifacts for debugging

## Alternatives considered

- **Playwright-only**: too slow at any meaningful coverage level; debugging individual component logic via E2E is painful.
- **Vitest-only with jsdom**: misses real-browser behaviors (focus, paint, real Cytoscape rendering, real xterm.js).
- **Cypress instead of Playwright**: Playwright has better parallelism, tracing, and works well with the Playwright MCP we use during development.
