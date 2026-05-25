# 0003. Server-side sessions in Postgres with opaque cookie identifiers

Date: 2026-05-24
Status: Accepted

## Context

Requirements §3 states sessions are long-lived ("stay logged in for days") and §3 also requires admin user management — by implication, the ability to revoke access. Two architectures meet the long-lived requirement: signed cookies (stateless JWTs or itsdangerous-signed payloads) or server-side sessions backed by a database table.

Signed cookies don't support revocation without a separate blocklist, defeating their statelessness. Server-side sessions trivially support revocation by deleting the row.

## Decision

Sessions are stored in a `sessions` table in Postgres with columns: `id` (random 256-bit opaque token), `user_id`, `created_at`, `last_used_at`, `expires_at`, `user_agent`, `ip`. The HTTP cookie carries only the session ID, not user identity. Cookie attributes: `HttpOnly; Secure; SameSite=Lax; Path=/`. Default expiry is 14 days, sliding (last_used_at refresh on each request).

Admin revocation deletes the row. Logout deletes the row. Cleanup job removes expired sessions hourly.

## Consequences

**Positive**
- Instant revocation by admins or on logout
- Session listing per user is trivial (used by future "active devices" feature)
- No JWT signing-key rotation complexity
- Server-side session data can be extended later (CSRF tokens, MFA flags) without cookie changes

**Negative**
- Every authenticated request makes a DB lookup (mitigated by an in-memory cache with short TTL)
- Session table can grow; the cleanup job is mandatory operational hygiene

**Neutral**
- The session lookup adds ~1ms latency per authenticated request

## Alternatives considered

- **Signed cookies (itsdangerous / JWT)**: no revocation, fragile rotation, and the "long-lived" requirement makes leaked cookies dangerous.
- **Redis-backed sessions**: adds a runtime dependency we don't otherwise need; Postgres is already there.
- **Cookies-with-blocklist hybrid**: combines the downsides of both approaches — stateful blocklist plus stateless cookie complexity.
