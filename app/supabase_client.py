# backend/app/supabase_client.py

from supabase import create_client, Client
from typing import Any, Optional, cast
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.warning(
        "SUPABASE_URL or SUPABASE_KEY environment variables are not set. "
        "Database operations will fail. Set them in your .env file or deployment environment."
    )

supabase: Client = create_client(SUPABASE_URL or "", SUPABASE_KEY or "")


# postgrest types `.execute().data` as a recursive JSON union (str | int | float |
# bool | None | Sequence[JSON] | Mapping[str, JSON]), not dict — a PostgREST response
# is technically arbitrary JSON. Every row from a table query is in practice always a
# JSON object, so the whole codebase already treats rows as dicts everywhere
# (`row.get(...)`, `row["x"]`) — pyright just can't see that. This asserts it once at
# the boundary instead of every call site individually fighting the union type; this
# is the single root cause behind the large majority of this project's pyright errors.
def rows(resp) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], resp.data or [])


def one_row(resp) -> Optional[dict[str, Any]]:
    """The common `.data[0]` pattern — None if the response had no rows."""
    data = rows(resp)
    return data[0] if data else None