"""Redis-backed cache and rate-limiting infrastructure."""

from ticketing.cache.backend import (
    CacheBackend,
    CacheMetrics,
    CacheUnavailableError,
    RateLimitDecision,
)
from ticketing.cache.redis import RedisCache

__all__ = [
    "CacheBackend",
    "CacheMetrics",
    "CacheUnavailableError",
    "RateLimitDecision",
    "RedisCache",
]
