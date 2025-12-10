"""Fetch and cache LLM pricing data from litellm's model prices database.

litellm maintains a comprehensive pricing database at:
https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json

This module fetches that data, caches it locally for 24 hours, and provides
a unified interface for looking up model prices with fallback to local data.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger(__name__)

LITELLM_PRICES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
CACHE_DIR = Path.home() / ".cache" / "shinka"
CACHE_FILE = CACHE_DIR / "litellm_prices.json"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# In-memory cache to avoid repeated disk reads
_memory_cache: Optional[dict] = None
_memory_cache_time: float = 0


def fetch_litellm_prices() -> Optional[dict]:
    """Fetch pricing data from litellm's GitHub repository.

    Returns:
        dict: The pricing data, or None if fetch failed.
    """
    try:
        req = Request(LITELLM_PRICES_URL, headers={"User-Agent": "shinka-pricing/1.0"})
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            logger.debug(f"Fetched litellm prices: {len(data)} models")
            return data
    except (URLError, json.JSONDecodeError, TimeoutError) as e:
        logger.warning(f"Failed to fetch litellm prices: {e}")
        return None


def save_cache(data: dict) -> None:
    """Save pricing data to local cache file."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_data = {"timestamp": time.time(), "data": data}
        CACHE_FILE.write_text(json.dumps(cache_data))
        logger.debug(f"Saved pricing cache to {CACHE_FILE}")
    except OSError as e:
        logger.warning(f"Failed to save pricing cache: {e}")


def load_cached_prices() -> Optional[dict]:
    """Load pricing data from cache if fresh.

    Returns:
        dict: The cached pricing data, or None if cache is stale/missing.
    """
    global _memory_cache, _memory_cache_time

    # Check memory cache first
    if _memory_cache and (time.time() - _memory_cache_time) < CACHE_TTL_SECONDS:
        return _memory_cache

    # Check disk cache
    if not CACHE_FILE.exists():
        return None

    try:
        cache_data = json.loads(CACHE_FILE.read_text())
        timestamp = cache_data.get("timestamp", 0)

        if (time.time() - timestamp) < CACHE_TTL_SECONDS:
            data = cache_data.get("data", {})
            # Update memory cache
            _memory_cache = data
            _memory_cache_time = timestamp
            logger.debug(f"Loaded pricing cache: {len(data)} models")
            return data
        else:
            logger.debug("Pricing cache is stale")
            return None
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load pricing cache: {e}")
        return None


def get_litellm_prices() -> dict:
    """Get litellm pricing data, using cache when available.

    Returns:
        dict: The pricing data (may be empty if fetch failed and no cache).
    """
    global _memory_cache, _memory_cache_time

    # Try cache first
    cached = load_cached_prices()
    if cached:
        return cached

    # Fetch fresh data
    data = fetch_litellm_prices()
    if data:
        save_cache(data)
        _memory_cache = data
        _memory_cache_time = time.time()
        return data

    # Return empty dict if everything fails
    logger.warning("No litellm pricing data available (fetch failed, no cache)")
    return {}


def get_model_price(model: str, provider: Optional[str] = None) -> Optional[dict]:
    """Look up pricing for a model, with fallback to local data.

    Args:
        model: The model name (e.g., "gpt-4o", "claude-3-5-sonnet-20241022")
        provider: Optional provider hint (e.g., "openai", "anthropic", "google")

    Returns:
        dict with "input_price" and "output_price" per token, or None if not found.
    """
    # Import fallback data here to avoid circular imports
    from . import pricing as fallback

    # Try litellm prices first
    litellm_data = get_litellm_prices()

    # litellm uses various key formats, try common patterns
    model_keys_to_try = [model]
    if provider:
        model_keys_to_try.append(f"{provider}/{model}")

    for key in model_keys_to_try:
        if key in litellm_data:
            entry = litellm_data[key]
            input_cost = entry.get("input_cost_per_token")
            output_cost = entry.get("output_cost_per_token")

            if input_cost is not None and output_cost is not None:
                return {"input_price": input_cost, "output_price": output_cost}

    # Fall back to local pricing data
    fallback_dicts = [
        fallback.CLAUDE_MODELS,
        fallback.OPENAI_MODELS,
        fallback.GEMINI_MODELS,
        fallback.DEEPSEEK_MODELS,
        fallback.BEDROCK_MODELS,
    ]

    for d in fallback_dicts:
        if model in d:
            return d[model]

    logger.debug(f"No pricing found for model: {model}")
    return None


def clear_cache() -> None:
    """Clear the pricing cache (useful for testing)."""
    global _memory_cache, _memory_cache_time
    _memory_cache = None
    _memory_cache_time = 0

    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
        logger.debug("Cleared pricing cache")
