from __future__ import annotations


def estimate_tokens(text: str) -> int:
    rough = len(text) / 4
    return int(rough)


def count_messages_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += estimate_tokens(m.get("content", "") or "")
        if "tool_calls" in m:
            for tc in m["tool_calls"]:
                total += estimate_tokens(str(tc))
    return total


def trim_to_budget(
    messages: list[dict],
    max_tokens: int = 128_000,
    reserve: int = 4000,
) -> list[dict]:
    budget = max_tokens - reserve
    if count_messages_tokens(messages) <= budget:
        return messages

    system = [m for m in messages if m["role"] == "system"]
    non_system = [m for m in messages if m["role"] != "system"]

    while non_system and count_messages_tokens(system + non_system) > budget:
        non_system.pop(0)

    return system + non_system
