"""Cache TTL checks must use total elapsed time, not timedelta.seconds (wraps at 24h)."""

from datetime import datetime, timedelta

from src.ai_brain.config.model_config_manager import ConfigurationCache
from src.prompts.manager import PromptCache


def _age(cache, key: str) -> None:
    cache._timestamps[key] = datetime.now() - timedelta(days=1, seconds=5)


def test_prompt_cache_entry_older_than_a_day_is_expired():
    cache = PromptCache(ttl_seconds=3600)
    cache.set("k", "v")
    _age(cache, "k")
    assert cache.get("k") is None


def test_config_cache_entry_older_than_a_day_is_expired():
    cache = ConfigurationCache(ttl_seconds=300)
    cache.set("k", "v")
    _age(cache, "k")
    assert cache.get("k") is None


def test_fresh_entries_are_still_served():
    p = PromptCache(ttl_seconds=3600)
    p.set("k", "v")
    assert p.get("k") == "v"
    c = ConfigurationCache(ttl_seconds=300)
    c.set("k", "v")
    assert c.get("k") == "v"
