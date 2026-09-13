import pytest

from ticketing.models.ticket import TicketStatus
from ticketing.services.ticket_workflow import (
    InvalidTicketTransitionError,
    TicketAction,
    next_ticket_status,
)

ALLOWED_TRANSITIONS = {
    (TicketAction.CLAIM, TicketStatus.OPEN): TicketStatus.IN_PROGRESS,
    (TicketAction.TRANSFER, TicketStatus.IN_PROGRESS): TicketStatus.IN_PROGRESS,
    (TicketAction.RESOLVE, TicketStatus.IN_PROGRESS): TicketStatus.RESOLVED,
    (TicketAction.REOPEN, TicketStatus.RESOLVED): TicketStatus.OPEN,
    (TicketAction.REOPEN, TicketStatus.CLOSED): TicketStatus.OPEN,
    (TicketAction.CLOSE, TicketStatus.RESOLVED): TicketStatus.CLOSED,
}


@pytest.mark.parametrize(
    ("action", "current_status", "expected_status"),
    [(*transition, expected) for transition, expected in ALLOWED_TRANSITIONS.items()],
)
def test_defined_ticket_transitions_are_allowed(
    action: TicketAction,
    current_status: TicketStatus,
    expected_status: TicketStatus,
) -> None:
    assert next_ticket_status(current_status, action) == expected_status


def test_every_undefined_ticket_transition_is_rejected() -> None:
    for action in TicketAction:
        for current_status in TicketStatus:
            if (action, current_status) in ALLOWED_TRANSITIONS:
                continue
            with pytest.raises(InvalidTicketTransitionError) as captured:
                next_ticket_status(current_status, action)
            assert captured.value.action == action
            assert captured.value.current_status == current_status
