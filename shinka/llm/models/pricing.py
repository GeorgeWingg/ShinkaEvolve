# Available models and pricing (fallback data)
#
# For up-to-date pricing, use get_model_price() which fetches from litellm's
# community-maintained database: https://github.com/BerriAI/litellm
#
# Official pricing sources:
# Anthropic: https://claude.com/pricing
# OpenAI: https://openai.com/api/pricing/
# DeepSeek: https://api-docs.deepseek.com/quick_start/pricing/
# Gemini: https://ai.google.dev/gemini-api/docs/pricing

M = 1000000

CLAUDE_MODELS = {
    "claude-3-5-haiku-20241022": {
        "input_price": 0.8 / M,
        "output_price": 4.0 / M,
    },
    "claude-3-5-sonnet-20241022": {
        "input_price": 3.0 / M,
        "output_price": 15.0 / M,
    },
    "claude-3-opus-20240229": {
        "input_price": 15.0 / M,
        "output_price": 75.0 / M,
    },
    "claude-3-7-sonnet-20250219": {
        "input_price": 3.0 / M,
        "output_price": 15.0 / M,
    },
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0": {
        "input_price": 3.0 / M,
        "output_price": 15.0 / M,
    },
    "claude-4-sonnet-20250514": {
        "input_price": 3.0 / M,
        "output_price": 15.0 / M,
    },
    "us.anthropic.claude-sonnet-4-20250514-v1:0": {
        "input_price": 3.0 / M,
        "output_price": 15.0 / M,
    },
    # Claude 4.5 models (Dec 2025)
    "claude-opus-4-5-20250910": {
        "input_price": 5.0 / M,
        "output_price": 25.0 / M,
    },
    "claude-sonnet-4-5-20250910": {
        "input_price": 3.0 / M,
        "output_price": 15.0 / M,
    },
    "claude-haiku-4-5-20250910": {
        "input_price": 1.0 / M,
        "output_price": 5.0 / M,
    },
}

OPENAI_MODELS = {
    "gpt-4o-mini": {
        "input_price": 0.15 / M,
        "output_price": 0.6 / M,
    },
    "gpt-4o-2024-08-06": {
        "input_price": 2.5 / M,
        "output_price": 10.0 / M,
    },
    "gpt-4o-mini-2024-07-18": {
        "input_price": 0.15 / M,
        "output_price": 0.6 / M,
    },
    "o1-2024-12-17": {
        "input_price": 15.0 / M,
        "output_price": 60.0 / M,
    },
    "o3-mini-2025-01-31": {
        "input_price": 1.1 / M,
        "output_price": 4.4 / M,
    },
    "o3-mini": {
        "input_price": 1.1 / M,
        "output_price": 4.4 / M,
    },
    "gpt-4.5-preview-2025-02-27": {
        "input_price": 75.0 / M,
        "output_price": 150.0 / M,
    },
    "gpt-4.1-2025-04-14": {
        "input_price": 2.0 / M,
        "output_price": 8.0 / M,
    },
    "gpt-4.1": {
        "input_price": 2.0 / M,
        "output_price": 8.0 / M,
    },
    "gpt-4.1-mini-2025-04-14": {
        "input_price": 0.4 / M,
        "output_price": 1.6 / M,
    },
    "gpt-4.1-mini": {
        "input_price": 0.4 / M,
        "output_price": 1.6 / M,
    },
    "gpt-4.1-nano-2025-04-14": {
        "input_price": 0.1 / M,
        "output_price": 0.4 / M,
    },
    "gpt-4.1-nano": {
        "input_price": 0.1 / M,
        "output_price": 0.4 / M,
    },
    "o3-2025-04-16": {
        "input_price": 2.0 / M,
        "output_price": 8.0 / M,
    },
    "o4-mini-2025-04-16": {
        "input_price": 1.1 / M,
        "output_price": 4.4 / M,
    },
    "o4-mini": {
        "input_price": 1.1 / M,
        "output_price": 4.4 / M,
    },
    "gpt-5": {
        "input_price": 1.25 / M,
        "output_price": 10.0 / M,
    },
    "gpt-5-mini": {
        "input_price": 0.25 / M,
        "output_price": 2.0 / M,
    },
    "gpt-5-nano": {
        "input_price": 0.05 / M,
        "output_price": 0.4 / M,
    },
}


DEEPSEEK_MODELS = {
    # DeepSeek V3.2 pricing (Dec 2025) - https://api-docs.deepseek.com/quick_start/pricing
    "deepseek-chat": {
        "input_price": 0.28 / M,
        "output_price": 0.42 / M,
    },
    "deepseek-reasoner": {
        "input_price": 0.28 / M,
        "output_price": 0.42 / M,
    },
}

# Gemini pricing as of Dec 2025 - https://ai.google.dev/gemini-api/docs/pricing
GEMINI_MODELS = {
    # Gemini 2.5 Pro (standard context ≤200K tokens)
    "gemini-2.5-pro": {
        "input_price": 1.25 / M,
        "output_price": 10.0 / M,
    },
    "gemini-2.5-pro-preview-05-06": {
        "input_price": 1.25 / M,
        "output_price": 10.0 / M,
    },
    # Gemini 3 Pro Preview (higher than 2.5 Pro)
    "gemini-3-pro-preview": {
        "input_price": 2.0 / M,
        "output_price": 12.0 / M,
    },
    # Gemini 2.5 Flash
    "gemini-2.5-flash": {
        "input_price": 0.30 / M,
        "output_price": 2.50 / M,
    },
    # Gemini 2.5 Flash Lite
    "gemini-2.5-flash-lite": {
        "input_price": 0.10 / M,
        "output_price": 0.40 / M,
    },
    "gemini-2.5-flash-lite-preview-06-17": {
        "input_price": 0.10 / M,
        "output_price": 0.40 / M,
    },
    # Gemini 2.0 Flash
    "gemini-2.0-flash": {
        "input_price": 0.10 / M,
        "output_price": 0.40 / M,
    },
    "gemini-2.0-flash-lite": {
        "input_price": 0.075 / M,
        "output_price": 0.30 / M,
    },
}

BEDROCK_MODELS = {
    "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0": CLAUDE_MODELS[
        "claude-3-5-sonnet-20241022"
    ],
    "bedrock/anthropic.claude-3-5-haiku-20241022-v1:0": CLAUDE_MODELS[
        "claude-3-5-haiku-20241022"
    ],
    "bedrock/anthropic.claude-3-opus-20240229-v1:0": CLAUDE_MODELS[
        "claude-3-opus-20240229"
    ],
    "bedrock/us.anthropic.claude-3-7-sonnet-20250219-v1:0": CLAUDE_MODELS[
        "claude-3-7-sonnet-20250219"
    ],
    "bedrock/us.anthropic.claude-sonnet-4-20250514-v1:0": CLAUDE_MODELS[
        "claude-4-sonnet-20250514"
    ],
}

REASONING_OAI_MODELS = [
    "o3-mini-2025-01-31",
    "o1-2024-12-17",
    "o3-2025-04-16",
    "o4-mini-2025-04-16",
    "o4-mini",
    "o3-mini",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
]

REASONING_CLAUDE_MODELS = [
    "claude-3-7-sonnet-20250219",
    "claude-4-sonnet-20250514",
]

REASONING_DEEPSEEK_MODELS = [
    "deepseek-reasoner",
]

REASONING_GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite-preview-06-17",
]

REASONING_AZURE_MODELS = [
    "azure-o3-mini",
    "azure-o4-mini",
    "azure-gpt-5",
    "azure-gpt-5-mini",
    "azure-gpt-5-nano",
]

REASONING_BEDROCK_MODELS = [
    "bedrock/us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    "bedrock/us.anthropic.claude-sonnet-4-20250514-v1:0",
]


def get_model_price(model: str, provider: str | None = None) -> dict | None:
    """Look up pricing for a model, with fallback to local data.

    This function first checks litellm's pricing database (cached for 24h),
    then falls back to the local dictionaries above.

    Args:
        model: The model name (e.g., "gpt-4o", "claude-3-5-sonnet-20241022")
        provider: Optional provider hint (e.g., "openai", "anthropic", "google")

    Returns:
        dict with "input_price" and "output_price" per token, or None if not found.

    Example:
        >>> price = get_model_price("gpt-4o")
        >>> if price:
        ...     cost = tokens * price["input_price"]
    """
    from .pricing_fetcher import get_model_price as _fetcher_get_price

    return _fetcher_get_price(model, provider)
