import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Self, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from ticketing.cache.backend import (
    CacheMetrics,
    CacheUnavailableError,
    RateLimitDecision,
)
from ticketing.core.config import Settings

logger = logging.getLogger(__name__)

_FIXED_WINDOW_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


@dataclass(slots=True)
class _MutableMetrics:
    hits: int = 0
    misses: int = 0
    writes: int = 0
    invalidations: int = 0
    failures: int = 0
    rate_limited: int = 0


class RedisCache:
    """Fail-open cache operations plus an atomic fixed-window rate limiter."""

    def __init__(self, client: Redis, *, namespace: str = "ticketing") -> None:
        self._client = client
        self._namespace = namespace
        self._metrics = _MutableMetrics()

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        client = Redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=settings.redis_timeout_seconds,
            socket_timeout=settings.redis_timeout_seconds,
        )
        return cls(client)

    async def get(self, key: str) -> str | None:
        try:
            value = await self._client.get(key)
        except RedisError:
            self._record_failure("get")
            return None
        if value is None:
            self._metrics.misses += 1
            return None
        self._metrics.hits += 1
        return cast(str, value)

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        try:
            await self._client.set(key, value, ex=ttl_seconds)
            self._metrics.writes += 1
        except RedisError:
            self._record_failure("set")

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete(key)
            self._metrics.invalidations += 1
        except RedisError:
            self._record_failure("delete")

    async def rate_limit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        try:
            raw_result = await cast(
                Awaitable[object],
                self._client.eval(
                    _FIXED_WINDOW_SCRIPT,
                    1,
                    key,
                    str(window_seconds),
                ),
            )
            result = cast(list[int], raw_result)
            current, ttl = int(result[0]), max(1, int(result[1]))
        except (RedisError, TypeError, ValueError, IndexError):
            self._record_failure("rate_limit")
            return RateLimitDecision(
                allowed=True,
                limit=limit,
                remaining=limit,
                retry_after_seconds=0,
                degraded=True,
            )
        allowed = current <= limit
        if not allowed:
            self._metrics.rate_limited += 1
        return RateLimitDecision(
            allowed=allowed,
            limit=limit,
            remaining=max(0, limit - current),
            retry_after_seconds=ttl,
        )

    async def ping(self) -> None:
        try:
            await cast(Awaitable[bool], self._client.ping())
        except RedisError as exc:
            self._record_failure("ping")
            raise CacheUnavailableError("Redis ping failed") from exc

    def metrics_snapshot(self) -> CacheMetrics:
        return CacheMetrics(
            hits=self._metrics.hits,
            misses=self._metrics.misses,
            writes=self._metrics.writes,
            invalidations=self._metrics.invalidations,
            failures=self._metrics.failures,
            rate_limited=self._metrics.rate_limited,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _record_failure(self, operation: str) -> None:
        self._metrics.failures += 1
        logger.warning(
            "Redis operation failed; continuing with degraded behavior",
            extra={"cache_operation": operation, "cache_namespace": self._namespace},
        )
