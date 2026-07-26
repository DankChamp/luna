from __future__ import annotations
from enum import Enum


class AgentMode(Enum):
    BUILD = "build"
    PLAN = "plan"


MODE_TOOL_BLOCKS: dict[AgentMode, set[str]] = {
    AgentMode.BUILD: set(),
    AgentMode.PLAN: {"write", "edit", "bash", "git_commit", "git_push"},
}

MODE_PROMPTS: dict[AgentMode, str] = {
    AgentMode.BUILD: "",
    AgentMode.PLAN: (
        "\n\n## Mode: PLAN\n"
        "You are in read-only investigation mode.\n"
        "You may ONLY read, search, explore, and gather information.\n"
        "You CANNOT write, edit, run commands, commit, or push.\n"
        "Focus on understanding the codebase, identifying issues,"
        " and proposing solutions without making changes."
    ),
}

MODE_INDICATORS: dict[AgentMode, dict[str, str]] = {
    AgentMode.BUILD: {"icon": "▲", "color": "#ff00ff", "label": "build"},
    AgentMode.PLAN: {"icon": "△", "color": "#00ffff", "label": "plan"},
}