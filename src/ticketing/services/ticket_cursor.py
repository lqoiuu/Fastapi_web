import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class InvalidTicketCursorError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TicketCursor:
    created_at: datetime
    ticket_id: UUID


def encode_ticket_cursor(cursor: TicketCursor) -> str:
    payload = json.dumps(
        {"created_at": cursor.created_at.isoformat(), "ticket_id": str(cursor.ticket_id)},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_ticket_cursor(encoded: str) -> TicketCursor:
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"created_at", "ticket_id"}:
            raise ValueError
        created_at = datetime.fromisoformat(payload["created_at"])
        ticket_id = UUID(payload["ticket_id"])
        if created_at.utcoffset() is None:
            raise ValueError
    except (binascii.Error, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise InvalidTicketCursorError from exc
    return TicketCursor(created_at=created_at, ticket_id=ticket_id)
