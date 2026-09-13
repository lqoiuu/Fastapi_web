import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import uuid4

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    return uuid4().hex


def accepted_correlation_id(value: str | None) -> str:
    if value is not None and _CORRELATION_ID_PATTERN.fullmatch(value):
        return value
    return new_correlation_id()


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def get_or_create_correlation_id() -> str:
    current = get_correlation_id()
    if current is not None:
        return current
    generated = new_correlation_id()
    _correlation_id.set(generated)
    return generated


def set_correlation_id(value: str) -> Token[str | None]:
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str | None]) -> None:
    _correlation_id.reset(token)


@contextmanager
def correlation_context(value: str | None = None) -> Iterator[str]:
    correlation_id = accepted_correlation_id(value)
    token = set_correlation_id(correlation_id)
    try:
        yield correlation_id
    finally:
        reset_correlation_id(token)
