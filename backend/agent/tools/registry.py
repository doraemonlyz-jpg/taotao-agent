"""Single source of truth for available tools."""
from __future__ import annotations

from .calculator import calculator
from .current_time import current_time
from .file_ops import grep_in_files, list_files, read_file, write_file
from .memory_tool import recall, remember
from .profile_tool import read_profile, update_profile
from .python_repl import python_repl
from .skills_tool import load_skill
from .web_search import web_search

all_tools = [
    calculator,
    current_time,
    web_search,
    read_file,
    write_file,
    list_files,
    grep_in_files,
    python_repl,
    remember,
    recall,
    update_profile,
    read_profile,
    load_skill,
]


def tool_descriptions() -> list[dict]:
    """For the frontend's 'capabilities' panel."""
    return [
        {"name": t.name, "description": (t.description or "").strip().split("\n")[0]}
        for t in all_tools
    ]
