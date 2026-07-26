from __future__ import annotations

from core.memory import MemoryStore
from .registry import ToolDef


def create_memory_tools(memory: MemoryStore) -> list[ToolDef]:
    async def remember_fact(fact: str) -> str:
        memory.add_fact(fact)
        auto = memory.extract_facts(fact)
        extra = f" ({len(auto)} patterns extracted)" if auto else ""
        return f"Remembered: {fact}{extra}"

    async def recall() -> str:
        summary = memory.summarize()
        return summary if summary else "No memories stored yet."

    async def set_preference(key: str, value: str) -> str:
        memory.set_preference(key, value)
        return f"Preference set: {key} = {value}"

    return [
        ToolDef(
            name="remember",
            description="Store a fact about the user or project for future sessions.",
            parameters={
                "fact": {
                    "type": "string",
                    "description": "The fact to remember",
                },
            },
            required=["fact"],
            handler=remember_fact,
        ),
        ToolDef(
            name="recall",
            description="Retrieve stored memories, preferences, and project state.",
            parameters={},
            handler=recall,
        ),
        ToolDef(
            name="set_preference",
            description="Set a user preference (e.g. language, style, conventions).",
            parameters={
                "key": {"type": "string", "description": "Preference key"},
                "value": {"type": "string", "description": "Preference value"},
            },
            required=["key", "value"],
            handler=set_preference,
        ),
    ]
