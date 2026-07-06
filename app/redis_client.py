# backend/app/redis_client.py

import os
import logging
from dotenv import load_dotenv
import redis.asyncio as redis

load_dotenv()

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client: redis.Redis = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
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
