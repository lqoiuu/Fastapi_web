from enum import StrEnum

from ticketing.models.ticket import TicketStatus


class TicketAction(StrEnum):
    CLAIM = "claim"
    TRANSFER = "transfer"
    RESOLVE = "resolve"
    REOPEN = "reopen"
    CLOSE = "close"


class InvalidTicketTransitionError(Exception):
    def __init__(self, action: TicketAction, current_status: TicketStatus) -> None:
        self.action = action
        self.current_status = current_status
        super().__init__(f"Cannot {action.value} a ticket in {current_status.value} status")


_TRANSITIONS: dict[TicketAction, dict[TicketStatus, TicketStatus]] = {
    TicketAction.CLAIM: {TicketStatus.OPEN: TicketStatus.IN_PROGRESS},
    TicketAction.TRANSFER: {TicketStatus.IN_PROGRESS: TicketStatus.IN_PROGRESS},
    TicketAction.RESOLVE: {TicketStatus.IN_PROGRESS: TicketStatus.RESOLVED},
    TicketAction.REOPEN: {
        TicketStatus.RESOLVED: TicketStatus.OPEN,
        TicketStatus.CLOSED: TicketStatus.OPEN,
    },
    TicketAction.CLOSE: {TicketStatus.RESOLVED: TicketStatus.CLOSED},
}


def next_ticket_status(current_status: TicketStatus, action: TicketAction) -> TicketStatus:
    try:
        return _TRANSITIONS[action][current_status]
    except KeyError as exc:
        raise InvalidTicketTransitionError(action, current_status) from exc
