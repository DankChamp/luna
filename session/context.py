from __future__ import annotations

import tiktoken


MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "meta/llama-3.1-8b-instruct": 131072,
    "meta/llama-3.1-70b-instruct": 131072,
    "meta/llama-3.3-70b-instruct": 131072,
    "meta/llama-3.1-405b-instruct": 131072,
    "llama3.1:8b": 131072,
    "llama3.1:70b": 131072,
    "llama3.3:70b": 131072,
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3.5-sonnet": 200000,
    "gemini-1.5-pro": 1000000,
    "gemini-1.5-flash": 1000000,
}

DEFAULT_MAX_TOKENS = 128_000
RESERVE_TOKENS = 4000

# Cache for token encoders
_encoder_cache: dict[str, tiktoken.Encoding] = {}


def _get_encoder(model: str | None = None) -> tiktoken.Encoding:
    """Get appropriate tiktoken encoder for model."""
    if model and model in _encoder_cache:
        return _encoder_cache[model]
    
    # Map model names to tiktoken encodings
    if model:
        model_lower = model.lower()
        if "gpt-4" in model_lower or "gpt-3.5" in model_lower or "gpt-4o" in model_lower:
            enc = tiktoken.encoding_for_model("gpt-4")
        elif "claude" in model_lower:
            enc = tiktoken.get_encoding("cl100k_base")
        elif "gemini" in model_lower:
            enc = tiktoken.get_encoding("cl100k_base")
        elif "llama" in model_lower:
            enc = tiktoken.get_encoding("cl100k_base")  # Best approximation
        else:
            enc = tiktoken.get_encoding("cl100k_base")
    else:
        enc = tiktoken.get_encoding("cl100k_base")
    
    if model:
        _encoder_cache[model] = enc
    return enc


def estimate_tokens(text: str, model: str | None = None) -> int:
    """Accurate token estimation using tiktoken."""
    if not text:
        return 0
    try:
        enc = _get_encoder(model)
        return len(enc.encode(text))
    except Exception:
        # Fallback to rough estimation
        return len(text) // 4


def count_messages_tokens(messages: list[dict], model: str | None = None) -> int:
    total = 0
    for m in messages:
        total += estimate_tokens(m.get("content", "") or "", model)
        if "tool_calls" in m:
            for tc in m["tool_calls"]:
                total += estimate_tokens(str(tc), model)
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
    if count_messages_tokens(messages, model) <= budget:
        return messages

    system = [m for m in messages if m["role"] == "system"]
    non_system = [m for m in messages if m["role"] != "system"]

    while non_system and count_messages_tokens(system + non_system, model) > budget:
        non_system.pop(0)

    return system + non_system
