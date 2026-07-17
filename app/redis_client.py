# backend/app/redis_client.py

import os
import logging
from dotenv import load_dotenv
import redis.asyncio as redis

load_dotenv()

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# A healthy Redis connects in milliseconds; the only thing a long connect
# timeout buys is a long stall when Redis is absent. At 5s, every request on a
# Redis-less machine paid ~5s in cache_get and ~5s again in cache_set — ~10s
# per API call (the cache layer swallows the errors, so it surfaced only as
# mysterious slowness, not failures).
redis_client: redis.Redis = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=2,
)


async def ping_redis() -> bool:
    """Check Redis connectivity. Returns True if reachable, False otherwise."""
    try:
        return await redis_client.ping()
    except Exception as e:
        logger.warning(f"Redis ping failed: {e}")
        return False


async def close_redis() -> None:
    """Close the Redis connection pool on shutdown."""
    try:
        await redis_client.aclose()
    except Exception as e:
        logger.warning(f"Error closing Redis connection: {e}")
