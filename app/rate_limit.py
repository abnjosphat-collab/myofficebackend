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

limiter = Limiter(key_func=get_remote_address, default_limits=[RATE_LIMIT_DEFAULT])
