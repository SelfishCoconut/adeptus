# 0007. CSRF protection via SameSite=Lax + JSON POST, no anti-CSRF token (skeleton)

Date: 2026-05-28
Status: Accepted

## Context

Slice 00 introduces cookie-based authentication (ADR-0003): an opaque,
server-side session referenced by an `HttpOnly; Secure; SameSite=Lax` cookie.
Cookie-auth APIs are a classic CSRF target — a malicious page can cause the
browser to send the victim's cookie on a forged cross-site request. The FE8
security review asked whether shipping without a dedicated anti-CSRF token
(double-submit cookie or synchroniser token) is acceptable, and required the
decision to be recorded rather than left implicit.

Adeptus is deployed as a single Docker Compose stack on a LAN/operator network,
not as a public multi-tenant SaaS, and the frontend and backend are served from
the same origin (Nginx terminates TLS; in dev the Vite proxy keeps `/api`
same-origin).

## Decision

For the walking skeleton we rely on two layered, already-present mitigations
instead of a dedicated CSRF token:

1. **`SameSite=Lax` on the session cookie.** The browser does not attach the
   cookie to cross-site subresource requests, nor to cross-site `POST`
   top-level navigations. All state-changing endpoints are `POST`, so a
   cross-site form auto-submit cannot carry the session.
2. **JSON request bodies on all mutations** (`login`, `logout`, `accept-terms`).
   A cross-site HTML `<form>` can only send `application/x-www-form-urlencoded`,
   `multipart/form-data`, or `text/plain`. Sending `application/json`
   cross-origin triggers a CORS preflight, which the backend does not answer for
   foreign origins — so the forged request never reaches the handler.

The frontend reinforces same-origin by keeping `VITE_API_BASE_URL` origin-only
(documented in `client.ts` / `.env.example`); a cross-origin value would change
this model and is explicitly called out as requiring a deliberate CORS decision.

A dedicated CSRF token is deferred, not rejected. ADR-0003 already notes the
server-side session row can carry CSRF state later without a cookie change.

## Consequences

**Positive**
- No token plumbing, rotation, or per-form wiring in this slice.
- Mitigations are structural (cookie attribute + content-type), not bespoke
  code that can rot or be forgotten.

**Negative**
- Protection depends on `SameSite=Lax` being honoured and on the backend never
  enabling permissive CORS with credentials. Both are invariants future slices
  must not break.
- Older browsers without `SameSite` support fall back to no CSRF protection
  (acceptable for a LAN operator tool; not for public deployment).

**Neutral**
- Revisit before any internet-facing deployment, or when an endpoint must accept
  a non-preflighted content-type. At that point add a double-submit token keyed
  to the session row.

## Alternatives considered

- **Double-submit cookie token now**: extra wiring on every mutation for a risk
  largely covered by `SameSite=Lax` on a same-origin LAN tool; premature here.
- **Synchroniser token in the session row**: cleanest long-term option and the
  likely future choice, but more than the skeleton needs.
- **`SameSite=Strict`**: would break expected deep-link navigation flows
  (arriving at the app already authenticated) for marginal additional safety
  over `Lax` given mutations are already preflighted.
