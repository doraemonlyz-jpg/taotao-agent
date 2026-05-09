"""Tools that let the LLM read/write the structured user profile."""
from __future__ import annotations

import json

from langchain_core.tools import tool

from ..memory.profile import get_profile


@tool
def update_profile(key: str, value: str) -> str:
    """Set a stable user-profile field that should ALWAYS be loaded into
    every future prompt. Use for things like name, language preference,
    job role, response style, do/don't lists.

    For ephemeral facts ("she likes blue at the moment"), use `remember`
    instead — that goes to vector memory and is recalled by similarity.

    Args:
        key:   The profile field (e.g. "name", "preferred_language", "tone").
        value: The value (string). For lists, pass JSON like '["a","b"]'.
    """
    parsed: object = value
    try:
        parsed = json.loads(value)  # auto-parse if user passed JSON
    except (json.JSONDecodeError, TypeError):
        pass
    data = get_profile().update(key, parsed)
    return f"profile.{key} set. Current keys: {sorted(data.keys())}"


@tool
def read_profile() -> str:
    """Return the entire user profile as JSON. Useful for self-diagnosis."""
    data = get_profile().all()
    return json.dumps(data, ensure_ascii=False, indent=2) if data else "(profile is empty)"
