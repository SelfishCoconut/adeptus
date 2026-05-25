# 0002. Admin user bootstrapped via environment variables on first boot

Date: 2026-05-24
Status: Accepted

## Context

Adeptus needs a deterministic way to create the first admin user without manual UI steps after deployment. Requirements §3 forbids self-signup; admins create all users. Two reasonable mechanisms exist: an interactive first-boot CLI prompt, or environment variables that seed an admin on first migration.

## Decision

On first boot, if no admin user exists in the database, the backend reads `ADEPTUS_ADMIN_USER` and `ADEPTUS_ADMIN_PASSWORD_HASH` from environment variables and creates the admin. The hash is an argon2 hash, not a plaintext password — generating it is a documented one-liner in the deployment runbook.

Subsequent boots are no-ops; the admin is created exactly once. After bootstrap, the admin can create other users (including additional admins) through the normal UI.

## Consequences

**Positive**
- Reproducible deployments — same compose file produces the same initial state
- No plaintext passwords in compose files or environment
- Bootstrap works on systems without an interactive TTY (cloud images, containers)

**Negative**
- Operator must run a one-liner to compute the hash before deployment
- A misconfigured `ADEPTUS_ADMIN_PASSWORD_HASH` produces a useless admin account that requires manual DB intervention

**Neutral**
- Bootstrap creates exactly one admin — additional admins via UI after first login

## Alternatives considered

- **Interactive CLI first-boot prompt**: requires a TTY, conflicts with docker-compose-up-and-go flows, and doesn't compose well with automated deployments.
- **Hardcoded default credentials forced-change on first login**: easy to forget to change, anti-pattern in security-sensitive systems.
- **Random password printed to logs on first boot**: log scraping is fragile and the password ends up in log aggregators.
