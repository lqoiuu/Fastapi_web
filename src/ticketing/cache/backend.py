from dataclasses import dataclass
from typing import Protocol


class CacheUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CacheMetrics:
    hits: int
    misses: int
    writes: int
    invalidations: int
    failures: int
    rate_limited: int


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int
    degraded: bool = False


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def rate_limit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision: ...

    async def ping(self) -> None: ...

    def metrics_snapshot(self) -> CacheMetrics: ...

    async def close(self) -> None: ...
