import codecs
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.cache.backend import CacheBackend
from ticketing.models.ticket_collaboration import (
    CommentVisibility,
    TicketAttachment,
    TicketComment,
)
from ticketing.repositories.ticket_collaboration import (
    TicketAttachmentRepository,
    TicketCommentRepository,
)
from ticketing.services.rbac import PermissionCode, RBACService
from ticketing.services.tenant import CurrentTenant
from ticketing.services.ticket import TicketService
from ticketing.storage.local import AttachmentStorage

_ALLOWED_CONTENT_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".txt": "text/plain",
}


class TicketCollaborationError(Exception):
    pass


class InternalCommentForbiddenError(TicketCollaborationError):
    pass


class AttachmentNotFoundError(TicketCollaborationError):
    pass


class AttachmentFilenameInvalidError(TicketCollaborationError):
    pass


class AttachmentContentTypeInvalidError(TicketCollaborationError):
    pass


class AttachmentEmptyError(TicketCollaborationError):
    pass


class AttachmentTooLargeError(TicketCollaborationError):
    pass


@dataclass(frozen=True, slots=True)
class AttachmentDownload:
    attachment: TicketAttachment
    content: AsyncIterator[bytes]


class TicketCollaborationService:
    def __init__(
        self,
        session: AsyncSession,
        storage: AttachmentStorage,
        *,
        max_attachment_size_bytes: int,
        cache: CacheBackend | None = None,
        permission_cache_ttl_seconds: int = 300,
        permission_cache_ttl_jitter_seconds: int = 30,
    ) -> None:
        self._session = session
        self._storage = storage
        self._max_attachment_size_bytes = max_attachment_size_bytes
        self._tickets = TicketService(
            session,
            cache,
            permission_cache_ttl_seconds=permission_cache_ttl_seconds,
            permission_cache_ttl_jitter_seconds=permission_cache_ttl_jitter_seconds,
        )
        self._comments = TicketCommentRepository(session)
        self._attachments = TicketAttachmentRepository(session)
        self._rbac = RBACService(
            session,
            cache,
            cache_ttl_seconds=permission_cache_ttl_seconds,
            cache_ttl_jitter_seconds=permission_cache_ttl_jitter_seconds,
        )

    async def create_comment(
        self,
        context: CurrentTenant,
        ticket_id: UUID,
        *,
        body: str,
        visibility: CommentVisibility,
    ) -> TicketComment:
        await self._tickets.get(context, ticket_id)
        if visibility == CommentVisibility.INTERNAL and not await self._can_view_internal(context):
            raise InternalCommentForbiddenError
        comment = TicketComment(
            id=uuid4(),
            tenant_id=context.tenant.id,
            ticket_id=ticket_id,
            author_membership_id=context.membership.id,
            visibility=visibility,
            body=body.strip(),
        )
        self._comments.add(comment)
        await self._session.commit()
        await self._session.refresh(comment)
        return comment

    async def list_comments(
        self,
        context: CurrentTenant,
        ticket_id: UUID,
    ) -> Sequence[TicketComment]:
        await self._tickets.get(context, ticket_id)
        return await self._comments.list_for_ticket(
            context.tenant.id,
            ticket_id,
            include_internal=await self._can_view_internal(context),
        )

    async def upload_attachment(
        self,
        context: CurrentTenant,
        ticket_id: UUID,
        *,
        filename: str | None,
        content_type: str | None,
        chunks: AsyncIterable[bytes],
    ) -> TicketAttachment:
        await self._tickets.get(context, ticket_id)
        safe_filename, normalized_content_type = self._validate_metadata(filename, content_type)
        iterator = chunks.__aiter__()
        first_chunk = await anext(iterator, None)
        if not first_chunk:
            raise AttachmentEmptyError
        self._validate_signature(normalized_content_type, first_chunk)

        async def bounded_chunks() -> AsyncIterator[bytes]:
            total = 0
            decoder = (
                codecs.getincrementaldecoder("utf-8")()
                if normalized_content_type == "text/plain"
                else None
            )
            async for chunk in self._prepend(first_chunk, iterator):
                total += len(chunk)
                if total > self._max_attachment_size_bytes:
                    raise AttachmentTooLargeError
                if decoder is not None:
                    if b"\x00" in chunk:
                        raise AttachmentContentTypeInvalidError
                    try:
                        decoder.decode(chunk, final=False)
                    except UnicodeDecodeError as exc:
                        raise AttachmentContentTypeInvalidError from exc
                yield chunk
            if decoder is not None:
                try:
                    decoder.decode(b"", final=True)
                except UnicodeDecodeError as exc:
                    raise AttachmentContentTypeInvalidError from exc

        object_key = f"{context.tenant.id.hex}/{ticket_id.hex}/{uuid4().hex}"
        size_bytes = await self._storage.write(object_key, bounded_chunks())
        attachment = TicketAttachment(
            id=uuid4(),
            tenant_id=context.tenant.id,
            ticket_id=ticket_id,
            uploader_membership_id=context.membership.id,
            object_key=object_key,
            original_filename=safe_filename,
            content_type=normalized_content_type,
            size_bytes=size_bytes,
        )
        self._attachments.add(attachment)
        try:
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            await self._storage.delete(object_key)
            raise
        await self._session.refresh(attachment)
        return attachment

    async def list_attachments(
        self,
        context: CurrentTenant,
        ticket_id: UUID,
    ) -> Sequence[TicketAttachment]:
        await self._tickets.get(context, ticket_id)
        return await self._attachments.list_for_ticket(context.tenant.id, ticket_id)

    async def download_attachment(
        self,
        context: CurrentTenant,
        ticket_id: UUID,
        attachment_id: UUID,
    ) -> AttachmentDownload:
        await self._tickets.get(context, ticket_id)
        attachment = await self._attachments.get_by_id(
            context.tenant.id,
            ticket_id,
            attachment_id,
        )
        if attachment is None or not await self._storage.exists(attachment.object_key):
            raise AttachmentNotFoundError
        return AttachmentDownload(
            attachment=attachment,
            content=self._storage.read(attachment.object_key),
        )

    async def _can_view_internal(self, context: CurrentTenant) -> bool:
        return await self._rbac.has_permission(
            context.membership,
            PermissionCode.TICKETS_COMMENT_INTERNAL,
        )

    @staticmethod
    async def _prepend(
        first_chunk: bytes,
        remainder: AsyncIterator[bytes],
    ) -> AsyncIterator[bytes]:
        yield first_chunk
        async for chunk in remainder:
            if chunk:
                yield chunk

    @staticmethod
    def _validate_metadata(filename: str | None, content_type: str | None) -> tuple[str, str]:
        if filename is None:
            raise AttachmentFilenameInvalidError
        safe_filename = PurePosixPath(filename.replace("\\", "/")).name.strip()
        if (
            not safe_filename
            or safe_filename in {".", ".."}
            or len(safe_filename) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in safe_filename)
        ):
            raise AttachmentFilenameInvalidError
        extension = PurePosixPath(safe_filename).suffix.lower()
        expected_content_type = _ALLOWED_CONTENT_TYPES.get(extension)
        normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
        if expected_content_type is None or normalized_content_type != expected_content_type:
            raise AttachmentContentTypeInvalidError
        return safe_filename, normalized_content_type

    @staticmethod
    def _validate_signature(content_type: str, first_chunk: bytes) -> None:
        valid = (
            (content_type == "image/png" and first_chunk.startswith(b"\x89PNG\r\n\x1a\n"))
            or (content_type == "image/jpeg" and first_chunk.startswith(b"\xff\xd8\xff"))
            or (content_type == "application/pdf" and first_chunk.startswith(b"%PDF-"))
            or content_type == "text/plain"
        )
        if not valid:
            raise AttachmentContentTypeInvalidError
