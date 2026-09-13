import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.cache.backend import CacheBackend
from ticketing.models.ticket import Ticket, TicketPriority, TicketStatus
from ticketing.models.ticket_event import TicketEvent, TicketEventType
from ticketing.models.ticket_idempotency import TicketCreationKey
from ticketing.repositories.customer import CustomerRepository
from ticketing.repositories.membership import MembershipRepository
from ticketing.repositories.ticket import TicketFilters, TicketRepository
from ticketing.repositories.ticket_event import TicketEventRepository
from ticketing.repositories.ticket_idempotency import TicketCreationKeyRepository
from ticketing.services.customer import CustomerNotFoundError
from ticketing.services.rbac import PermissionCode, RBACService
from ticketing.services.tenant import CurrentTenant
from ticketing.services.ticket_cursor import (
    TicketCursor,
    decode_ticket_cursor,
    encode_ticket_cursor,
)
from ticketing.services.ticket_workflow import (
    InvalidTicketTransitionError,
    TicketAction,
    next_ticket_status,
)


class TicketServiceError(Exception):
    """Base class for expected ticket-domain failures."""


class TicketNotFoundError(TicketServiceError):
    pass


class TicketAssigneeNotFoundError(TicketServiceError):
    pass


class TicketActionForbiddenError(TicketServiceError):
    pass


class TicketAssignmentConflictError(TicketServiceError):
    pass


class TicketTransitionConflictError(TicketServiceError):
    def __init__(self, action: TicketAction, current_status: TicketStatus) -> None:
        self.action = action
        self.current_status = current_status
        super().__init__(f"Cannot {action.value} a ticket in {current_status.value} status")


class IdempotencyKeyConflictError(TicketServiceError):
    pass


@dataclass(frozen=True, slots=True)
class TicketChanges:
    fields: frozenset[str]
    subject: str | None
    description: str | None
    priority: TicketPriority | None


@dataclass(frozen=True, slots=True)
class TicketListOptions:
    status: TicketStatus | None
    assignee_membership_id: UUID | None
    priority: TicketPriority | None
    created_from: datetime | None
    created_to: datetime | None
    keyword: str | None
    cursor: str | None
    limit: int


@dataclass(frozen=True, slots=True)
class TicketPage:
    items: list[Ticket]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TicketCreationResult:
    ticket: Ticket
    replayed: bool


class TicketService:
    def __init__(
        self,
        session: AsyncSession,
        cache: CacheBackend | None = None,
        *,
        permission_cache_ttl_seconds: int = 300,
        permission_cache_ttl_jitter_seconds: int = 30,
    ) -> None:
        self._session = session
        self._tickets = TicketRepository(session)
        self._events = TicketEventRepository(session)
        self._creation_keys = TicketCreationKeyRepository(session)
        self._customers = CustomerRepository(session)
        self._memberships = MembershipRepository(session)
        self._rbac = RBACService(
            session,
            cache,
            cache_ttl_seconds=permission_cache_ttl_seconds,
            cache_ttl_jitter_seconds=permission_cache_ttl_jitter_seconds,
        )

    async def create(
        self,
        context: CurrentTenant,
        *,
        customer_id: UUID | None,
        subject: str,
        description: str,
        priority: TicketPriority,
        idempotency_key: str,
    ) -> TicketCreationResult:
        tenant_id = context.tenant.id
        membership_id = context.membership.id
        request_hash = self._creation_request_hash(
            customer_id=customer_id,
            subject=subject,
            description=description,
            priority=priority,
        )
        existing_key = await self._creation_keys.get(
            tenant_id,
            membership_id,
            idempotency_key,
        )
        if existing_key is not None:
            return await self._replay_creation(existing_key, request_hash)

        if customer_id is not None:
            customer = await self._customers.get_by_id(tenant_id, customer_id)
            can_read_customers = await self._rbac.has_permission(
                context.membership,
                PermissionCode.CUSTOMERS_READ,
            )
            if customer is None or not customer.is_active or not can_read_customers:
                raise CustomerNotFoundError

        ticket = Ticket(
            id=uuid4(),
            tenant_id=tenant_id,
            customer_id=customer_id,
            created_by_membership_id=membership_id,
            subject=subject.strip(),
            description=description.strip(),
            status=TicketStatus.OPEN,
            priority=priority,
        )
        self._tickets.add(ticket)
        self._events.add(
            TicketEvent(
                id=uuid4(),
                tenant_id=tenant_id,
                ticket_id=ticket.id,
                actor_membership_id=membership_id,
                event_type=TicketEventType.CREATED,
                from_status=None,
                to_status=TicketStatus.OPEN,
                from_assignee_membership_id=None,
                to_assignee_membership_id=None,
                note=None,
            )
        )
        self._creation_keys.add(
            TicketCreationKey(
                id=uuid4(),
                tenant_id=tenant_id,
                membership_id=membership_id,
                key=idempotency_key,
                request_hash=request_hash,
                ticket_id=ticket.id,
            )
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raced_key = await self._creation_keys.get(
                tenant_id,
                membership_id,
                idempotency_key,
            )
            if raced_key is None:
                raise
            return await self._replay_creation(raced_key, request_hash)
        await self._session.refresh(ticket)
        return TicketCreationResult(ticket=ticket, replayed=False)

    async def _replay_creation(
        self,
        record: TicketCreationKey,
        request_hash: str,
    ) -> TicketCreationResult:
        if record.request_hash != request_hash:
            raise IdempotencyKeyConflictError
        ticket = await self._tickets.get_by_id(record.tenant_id, record.ticket_id)
        if ticket is None:
            raise TicketNotFoundError
        return TicketCreationResult(ticket=ticket, replayed=True)

    @staticmethod
    def _creation_request_hash(
        *,
        customer_id: UUID | None,
        subject: str,
        description: str,
        priority: TicketPriority,
    ) -> str:
        canonical_request = json.dumps(
            {
                "customer_id": None if customer_id is None else str(customer_id),
                "subject": subject.strip(),
                "description": description.strip(),
                "priority": priority.value,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(canonical_request).hexdigest()

    async def list_events(self, context: CurrentTenant, ticket_id: UUID) -> Sequence[TicketEvent]:
        await self.get(context, ticket_id)
        return await self._events.list_for_ticket(context.tenant.id, ticket_id)

    async def claim(self, context: CurrentTenant, ticket_id: UUID, *, note: str | None) -> Ticket:
        ticket = await self._get_locked_visible(context, ticket_id)
        await self._require_workflow_permission(context)
        next_status = self._next_status(ticket.status, TicketAction.CLAIM)
        if ticket.assignee_membership_id is not None:
            raise TicketAssignmentConflictError
        return await self._save_action(
            context,
            ticket,
            action=TicketAction.CLAIM,
            next_status=next_status,
            next_assignee_membership_id=context.membership.id,
            note=note,
        )

    async def transfer(
        self,
        context: CurrentTenant,
        ticket_id: UUID,
        *,
        assignee_membership_id: UUID,
        note: str | None,
    ) -> Ticket:
        ticket = await self._get_locked_visible(context, ticket_id)
        await self._require_workflow_permission(context)
        await self._require_assignee_or_admin(context, ticket)
        next_status = self._next_status(ticket.status, TicketAction.TRANSFER)
        target = await self._memberships.get_by_id(
            context.tenant.id,
            assignee_membership_id,
        )
        if target is None or not await self._rbac.has_permission(
            target,
            PermissionCode.TICKETS_MANAGE_WORKFLOW,
        ):
            raise TicketAssigneeNotFoundError
        if target.id == ticket.assignee_membership_id:
            raise TicketAssignmentConflictError
        return await self._save_action(
            context,
            ticket,
            action=TicketAction.TRANSFER,
            next_status=next_status,
            next_assignee_membership_id=target.id,
            note=note,
        )

    async def resolve(self, context: CurrentTenant, ticket_id: UUID, *, note: str | None) -> Ticket:
        ticket = await self._get_locked_visible(context, ticket_id)
        await self._require_workflow_permission(context)
        await self._require_assignee_or_admin(context, ticket)
        next_status = self._next_status(ticket.status, TicketAction.RESOLVE)
        return await self._save_action(
            context,
            ticket,
            action=TicketAction.RESOLVE,
            next_status=next_status,
            next_assignee_membership_id=ticket.assignee_membership_id,
            note=note,
        )

    async def reopen(self, context: CurrentTenant, ticket_id: UUID, *, note: str | None) -> Ticket:
        ticket = await self._get_locked_visible(context, ticket_id)
        has_workflow_permission = await self._rbac.has_permission(
            context.membership,
            PermissionCode.TICKETS_MANAGE_WORKFLOW,
        )
        if ticket.created_by_membership_id != context.membership.id and not has_workflow_permission:
            raise TicketActionForbiddenError
        next_status = self._next_status(ticket.status, TicketAction.REOPEN)
        return await self._save_action(
            context,
            ticket,
            action=TicketAction.REOPEN,
            next_status=next_status,
            next_assignee_membership_id=None,
            note=note,
        )

    async def close(self, context: CurrentTenant, ticket_id: UUID, *, note: str | None) -> Ticket:
        ticket = await self._get_locked_visible(context, ticket_id)
        await self._require_workflow_permission(context)
        await self._require_assignee_or_admin(context, ticket)
        next_status = self._next_status(ticket.status, TicketAction.CLOSE)
        return await self._save_action(
            context,
            ticket,
            action=TicketAction.CLOSE,
            next_status=next_status,
            next_assignee_membership_id=ticket.assignee_membership_id,
            note=note,
        )

    async def _get_locked_visible(self, context: CurrentTenant, ticket_id: UUID) -> Ticket:
        ticket = await self._tickets.get_by_id(
            context.tenant.id,
            ticket_id,
            for_update=True,
        )
        if ticket is None or not await self._can_access(
            context,
            ticket,
            PermissionCode.TICKETS_READ_ALL,
        ):
            raise TicketNotFoundError
        return ticket

    async def _require_workflow_permission(self, context: CurrentTenant) -> None:
        if not await self._rbac.has_permission(
            context.membership,
            PermissionCode.TICKETS_MANAGE_WORKFLOW,
        ):
            raise TicketActionForbiddenError

    async def _require_assignee_or_admin(self, context: CurrentTenant, ticket: Ticket) -> None:
        if ticket.assignee_membership_id == context.membership.id:
            return
        if not await self._rbac.has_permission(
            context.membership,
            PermissionCode.ROLES_MANAGE,
        ):
            raise TicketActionForbiddenError

    @staticmethod
    def _next_status(current_status: TicketStatus, action: TicketAction) -> TicketStatus:
        try:
            return next_ticket_status(current_status, action)
        except InvalidTicketTransitionError as exc:
            raise TicketTransitionConflictError(action, current_status) from exc

    async def _save_action(
        self,
        context: CurrentTenant,
        ticket: Ticket,
        *,
        action: TicketAction,
        next_status: TicketStatus,
        next_assignee_membership_id: UUID | None,
        note: str | None,
    ) -> Ticket:
        event_types = {
            TicketAction.CLAIM: TicketEventType.CLAIMED,
            TicketAction.TRANSFER: TicketEventType.TRANSFERRED,
            TicketAction.RESOLVE: TicketEventType.RESOLVED,
            TicketAction.REOPEN: TicketEventType.REOPENED,
            TicketAction.CLOSE: TicketEventType.CLOSED,
        }
        self._events.add(
            TicketEvent(
                id=uuid4(),
                tenant_id=context.tenant.id,
                ticket_id=ticket.id,
                actor_membership_id=context.membership.id,
                event_type=event_types[action],
                from_status=ticket.status,
                to_status=next_status,
                from_assignee_membership_id=ticket.assignee_membership_id,
                to_assignee_membership_id=next_assignee_membership_id,
                note=None if note is None else note.strip(),
            )
        )
        ticket.status = next_status
        ticket.assignee_membership_id = next_assignee_membership_id
        await self._session.commit()
        await self._session.refresh(ticket)
        return ticket

    async def get(self, context: CurrentTenant, ticket_id: UUID) -> Ticket:
        ticket = await self._tickets.get_by_id(context.tenant.id, ticket_id)
        if ticket is None or not await self._can_access(
            context,
            ticket,
            PermissionCode.TICKETS_READ_ALL,
        ):
            raise TicketNotFoundError
        return ticket

    async def list(self, context: CurrentTenant, options: TicketListOptions) -> TicketPage:
        can_read_all = await self._rbac.has_permission(
            context.membership,
            PermissionCode.TICKETS_READ_ALL,
        )
        cursor = None if options.cursor is None else decode_ticket_cursor(options.cursor)
        records = await self._tickets.list_for_tenant(
            context.tenant.id,
            filters=TicketFilters(
                creator_membership_id=None if can_read_all else context.membership.id,
                status=options.status,
                assignee_membership_id=options.assignee_membership_id,
                priority=options.priority,
                created_from=options.created_from,
                created_to=options.created_to,
                keyword=options.keyword,
                cursor_created_at=None if cursor is None else cursor.created_at,
                cursor_ticket_id=None if cursor is None else cursor.ticket_id,
            ),
            limit=options.limit + 1,
        )
        items = list(records[: options.limit])
        next_cursor = None
        if len(records) > options.limit:
            last_item = items[-1]
            next_cursor = encode_ticket_cursor(
                TicketCursor(created_at=last_item.created_at, ticket_id=last_item.id)
            )
        return TicketPage(items=items, next_cursor=next_cursor)

    async def update(
        self,
        context: CurrentTenant,
        ticket_id: UUID,
        changes: TicketChanges,
    ) -> Ticket:
        ticket = await self._tickets.get_by_id(context.tenant.id, ticket_id)
        if ticket is None or not await self._can_access(
            context,
            ticket,
            PermissionCode.TICKETS_UPDATE_ALL,
        ):
            raise TicketNotFoundError

        if "subject" in changes.fields:
            assert changes.subject is not None
            ticket.subject = changes.subject.strip()
        if "description" in changes.fields:
            assert changes.description is not None
            ticket.description = changes.description.strip()
        if "priority" in changes.fields:
            assert changes.priority is not None
            ticket.priority = changes.priority

        await self._session.commit()
        await self._session.refresh(ticket)
        return ticket

    async def _can_access(
        self,
        context: CurrentTenant,
        ticket: Ticket,
        all_records_permission: PermissionCode,
    ) -> bool:
        return (
            ticket.created_by_membership_id == context.membership.id
            or await self._rbac.has_permission(context.membership, all_records_permission)
        )
