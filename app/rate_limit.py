# backend/app/rate_limit.py — shared slowapi Limiter instance.
#
# Lives in its own module (not main.py) so router files can import `limiter`
# for a stricter per-route limit (e.g. bulk delete/import endpoints) without
# a circular import back to main.py, which is what registers all the routers.
import os
from slowapi import Limiter
from slowapi.util import get_remote_address

# A single global default applied to every route via SlowAPIMiddleware (see
# main.py) — keyed by client IP. Override via env var if 300/minute is too
# strict/loose for your traffic pattern.
RATE_LIMIT_DEFAULT = os.environ.get("RATE_LIMIT_DEFAULT", "300/minute")


def client_ip(request) -> str:
    """The key rate limits count against: the direct peer by default, or the
    first X-Forwarded-For hop when TRUST_PROXY_HEADERS=true.

    Behind Render (or any reverse proxy) request.client.host is the proxy's
    own address, so every visitor would share one limit bucket without this.
    Only honour the header when explicitly enabled: X-Forwarded-For is
    client-spoofable, and trusting it by default would let anyone rotate
    identities past every limit. On Render set TRUST_PROXY_HEADERS=true (the
    platform overwrites the leftmost hop itself); anywhere without a trusted
    proxy in front, leave it off.
    """
    if os.environ.get("TRUST_PROXY_HEADERS", "false").strip().lower() in ("1", "true", "yes"):
        forwarded = request.headers.get("x-forwarded-for", "")
        first_hop = forwarded.split(",")[0].strip()
        if first_hop:
            return first_hop
    return get_remote_address(request)


limiter = Limiter(key_func=client_ip, default_limits=[RATE_LIMIT_DEFAULT])
