def estimate_cost_usd(prompt_tokens: int, completion_tokens: int, price_per_1k: float = 0.0) -> float:
    """
    MVP stub. Later: per-model pricing table.
    """
    total_tokens = prompt_tokens + completion_tokens
    return (total_tokens / 1000.0) * price_per_1k
