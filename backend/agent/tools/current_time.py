"""Get the wall-clock time — LLMs have no clock, so they bluff dates."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from langchain_core.tools import tool


@tool
def current_time(timezone: str = "UTC") -> str:
    """Get the current wall-clock time in the given IANA timezone.

    Use this whenever the question depends on "now" — relative dates
    ("yesterday", "next Monday"), age calculations, freshness checks
    on web search results, scheduling, etc. NEVER guess the current
    date from your training data.

    Args:
        timezone: IANA timezone name like "Asia/Shanghai", "UTC",
                  "America/Los_Angeles". Defaults to "UTC".
    """
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        return f"Unknown timezone {timezone!r}. Try 'UTC' or an IANA name like 'Asia/Shanghai'."
    now = dt.datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S %Z (%A, week %V)")
