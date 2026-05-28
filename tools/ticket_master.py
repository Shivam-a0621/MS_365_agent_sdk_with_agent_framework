"""IT-ticket tools for the ticket_raiser agent (mock bodies kept verbatim from the POC)."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool

_TICKETS: dict[str, str] = {}


@tool(approval_mode="never_require")
def check_existing_tickets(
    error_signature: Annotated[
        str, "Short signature of the error, e.g. 'ConnectionTimeout to db1'"
    ],
) -> str:
    """Look up whether an open ticket already exists for this error signature."""
    if error_signature in _TICKETS:
        return f"Existing ticket {_TICKETS[error_signature]} already covers this issue."
    return f"No existing ticket for signature {error_signature!r}."


@tool(approval_mode="always_require")
def create_ticket(
    title: Annotated[str, "Short title for the ticket"],
    description: Annotated[str, "Details of the issue"],
    department: Annotated[
        str, "Target dept: IT / Networking / Security / Hardware / Software"
    ],
    error_signature: Annotated[str, "Same signature used in check_existing_tickets"],
) -> str:
    """Create a new IT support ticket. Requires human approval."""
    ticket_id = f"TKT-{1000 + len(_TICKETS) + 1}"
    _TICKETS[error_signature] = ticket_id
    return f"Ticket {ticket_id} created for {department}: {title}"
