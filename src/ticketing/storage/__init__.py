"""Attachment storage adapters."""

from ticketing.storage.local import AttachmentStorage, InvalidObjectKeyError, LocalAttachmentStorage

__all__ = ["AttachmentStorage", "InvalidObjectKeyError", "LocalAttachmentStorage"]
