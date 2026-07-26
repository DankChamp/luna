from __future__ import annotations


MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "meta/llama-3.1-8b-instruct": 131072,
    "meta/llama-3.1-70b-instruct": 131072,
    "meta/llama-3.3-70b-instruct": 131072,
    "meta/llama-3.1-405b-instruct": 131072,
    "llama3.1:8b": 131072,
    "llama3.1:70b": 131072,
    "llama3.3:70b": 131072,
}

DEFAULT_MAX_TOKENS = 128_000
RESERVE_TOKENS = 4000


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


def get_context_limit(model: str | None = None) -> int:
    if model and model in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[model]
    return DEFAULT_MAX_TOKENS


def trim_to_budget(
    messages: list[dict],
    max_tokens: int | None = None,
    model: str | None = None,
) -> list[dict]:
    limit = max_tokens or get_context_limit(model)
    budget = limit - RESERVE_TOKENS
    if count_messages_tokens(messages) <= budget:
        return messages

    system = [m for m in messages if m["role"] == "system"]
    non_system = [m for m in messages if m["role"] != "system"]

    while non_system and count_messages_tokens(system + non_system) > budget:
        non_system.pop(0)

    return system + non_system
