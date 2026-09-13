import hashlib
import re
from uuid import UUID

_SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_PREFIX = "ticketing:v1"


def permission_snapshot_key(tenant_id: UUID, membership_id: UUID) -> str:
    return f"{_PREFIX}:tenant:{tenant_id}:membership:{membership_id}:permissions"


def rate_limit_key(scope: str, identifier: str) -> str:
    if not _SCOPE_PATTERN.fullmatch(scope):
        raise ValueError("Invalid rate-limit scope")
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    return f"{_PREFIX}:rate-limit:{scope}:{digest}"
