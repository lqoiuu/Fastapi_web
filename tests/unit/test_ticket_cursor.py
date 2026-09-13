from datetime import UTC, datetime
from uuid import UUID

import pytest

from ticketing.services.ticket_cursor import (
    InvalidTicketCursorError,
    TicketCursor,
    decode_ticket_cursor,
    encode_ticket_cursor,
)


def test_ticket_cursor_round_trip_is_url_safe_and_lossless() -> None:
    cursor = TicketCursor(
        created_at=datetime(2026, 9, 13, 12, 30, 45, 123456, tzinfo=UTC),
        ticket_id=UUID("12345678-1234-5678-1234-567812345678"),
    )

    encoded = encode_ticket_cursor(cursor)

    assert "=" not in encoded
    assert decode_ticket_cursor(encoded) == cursor


@pytest.mark.parametrize("encoded", ["invalid!", "e30", "W10"])
def test_invalid_ticket_cursor_is_rejected(encoded: str) -> None:
    with pytest.raises(InvalidTicketCursorError):
        decode_ticket_cursor(encoded)


def test_cursor_without_timezone_is_rejected() -> None:
    encoded = encode_ticket_cursor(
        TicketCursor(
            created_at=datetime(2026, 9, 13, 12, 30),
            ticket_id=UUID("12345678-1234-5678-1234-567812345678"),
        )
    )

    with pytest.raises(InvalidTicketCursorError):
        decode_ticket_cursor(encoded)
