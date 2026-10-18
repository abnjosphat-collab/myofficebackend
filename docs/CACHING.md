# Response caching

`app/cache.py` caches GET responses for the read-heavy, rarely-changing
endpoints the homepage hits on every load. Two layers, one contract.

## Layers

- **Redis (primary).** Configured by `REDIS_URL` (default
  `redis://localhost:6379/0`). While Redis answers, it is the single source
  of truth: a Redis miss is a miss even if the fallback holds the key.
- **In-memory fallback (free, zero infrastructure).** A bounded per-process
  store (1000 keys, TTL-respecting). It is only *read* while Redis is in
  cooldown; every `cache_set` warms it, so a machine with no Redis at all —
  or an outage — still serves repeat reads from memory instead of
  re-querying Supabase on every request.

No Redis provisioning is required to benefit: the fallback is the free
tier. Pointing `REDIS_URL` at a real Redis (local `redis-server` or any
managed instance) adds a shared layer across workers and restarts.

## Invalidation contract

Every write endpoint for a cached resource must call
`invalidate_namespace("<namespace>")` after a successful write. It clears
both layers (Redis via the namespace keyset, memory by namespace scan), so
the next read after a write always sees fresh data. Deliberately NOT behind
the circuit breaker: on flaky Redis, skipping invalidation would serve
stale entries until TTL.

Current namespaces and TTLs: `employees`, `equipment`, `breakdowns`,
`work_orders` — 60 seconds each. The `@cached(namespace, ttl)` decorator
folds the function name into the key, so endpoints sharing a namespace
never collide.

## Failure semantics

- Redis down: reads fall back to memory; the circuit breaker skips Redis
  for 30 s after a failure so requests never pay connect timeouts
  repeatedly. Writes still invalidate memory, so correctness holds.
- Several workers: each holds its own memory copy. While Redis is up this
  is invisible (Redis decides); during an outage a worker can serve its
  own copy up to its TTL. With 60 s TTLs this is accepted staleness.
- The cache never raises: encode failures, Redis errors and decode
  failures all degrade to miss/skip with a logged warning.

## Observability

`GET /api/health` carries a `cache` object: `redis_hits`, `memory_hits`,
`misses`, `sets`, `invalidations`, `redis_outages`, plus live
`memory_keys` and `redis_in_cooldown`. Counters are per-process lifetime.

## Tests

`tests/test_cache.py` covers hits, invalidation, key stability, outage
fallback, TTL expiry, the memory bound, and the counters, against fake
Redis clients — no real Redis needed. `tests/conftest.py` resets the
module-global cache state before every test so entries, a tripped
breaker, or counters can never leak between tests.

## Auth cache (separate from the response cache above)

`app/auth.py` caches verified `get_current_user()` results in-process:
without it every authenticated request costs 2 Supabase round-trips
(`auth.get_user` + the role RPC). Keyed by `sha256(token)`; the value is
the user dict plus the MFA-ok flag. Tokens and hashes never leave the
process — nothing auth-related is ever written to Redis.

- **TTL:** `AUTH_CACHE_TTL_SECONDS`, default 30. Expired entries re-verify.
- **Bound:** 1000 entries; expired entries are evicted first, then the
  oldest-expiring live entry.
- **Failures are never cached:** 401s, 503s and the MFA 401 raise before
  the store, so a token that becomes valid (or a service that recovers)
  works on the next request, not at TTL expiry.
- **Trade-off — role lag up to TTL:** a role change is visible to other
  requests within `AUTH_CACHE_TTL_SECONDS`, not instantly. The admin
  role-change, deactivate and MFA-factor-reset endpoints call
  `invalidate_user()` so those take effect immediately; anything else that
  changes effective permissions (e.g. a dashboard-side edit) lags up to TTL.
- The MFA factor-list fallback answer is cached per user id on the same
  TTL and cleared by the same `invalidate_user()`.

## Rate limiting and `TRUST_PROXY_HEADERS`

Public endpoints carry stricter per-route slowapi limits on top of the
global default: `POST /api/admin/password-reset-requests` (5/minute),
Tools `POST /auth/register` (5/minute) and Tools `POST /auth/login`
(10/minute). Tools login additionally counts failed attempts per username
in-process (5 failures/15 min → 429): a restart clears it, and with several
workers each counts its own — it bounds casual stuffing, not a distributed
attack. (Tools self-registration and first-account-is-admin are unchanged
by design — owner decision.)

All slowapi limits are keyed by `client_ip()` (`app/rate_limit.py`): the
direct peer by default, or the first `X-Forwarded-For` hop only when
`TRUST_PROXY_HEADERS=true`. The header is client-spoofable, so trusting it
is opt-in. **On Render, set `TRUST_PROXY_HEADERS=true`** (the platform
overwrites the leftmost hop itself, and without it every visitor shares the
proxy's single limit bucket). Anywhere without a trusted proxy in front,
leave it off.
