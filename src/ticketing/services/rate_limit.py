from ticketing.cache.backend import CacheBackend, RateLimitDecision
from ticketing.cache.keys import rate_limit_key


class LoginRateLimiter:
    def __init__(
        self,
        cache: CacheBackend,
        *,
        limit: int,
        window_seconds: int,
    ) -> None:
        self._cache = cache
        self._limit = limit
        self._window_seconds = window_seconds

    async def check(self, *, email: str, client_host: str) -> RateLimitDecision:
        normalized_identity = f"{client_host}:{email.strip().lower()}"
        return await self._cache.rate_limit(
            rate_limit_key("login", normalized_identity),
            limit=self._limit,
            window_seconds=self._window_seconds,
        )
