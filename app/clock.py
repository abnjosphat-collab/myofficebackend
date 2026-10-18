# backend/app/clock.py — one definition of "today" for the whole API.
#
# The mine runs on Africa/Harare time (CAT, UTC+2, no daylight saving). Code
# that means "today" / "this week" for people on site must use these helpers,
# not datetime.utcnow()/date.today(): the server runs on UTC, so in the two
# hours after local midnight UTC still says "yesterday", and a dashboard's
# "today" count goes wrong for every early-morning user.
#
# Deliberately NOT used for created_at/updated_at serialization: those stay
# datetime.utcnow().isoformat() (see task 2.6) so stored timestamps don't
# change shape.
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HARARE = ZoneInfo("Africa/Harare")


def now_harare() -> datetime:
    """Current time in Africa/Harare, timezone-aware."""
    return datetime.now(HARARE)


def today_harare() -> date:
    """Today's calendar date in Africa/Harare."""
    return now_harare().date()


def week_start_harare() -> date:
    """Monday of the current week in Africa/Harare."""
    today = today_harare()
    return today - timedelta(days=today.weekday())
