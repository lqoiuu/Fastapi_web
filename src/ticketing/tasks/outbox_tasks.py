import asyncio

from ticketing.core.config import get_settings
from ticketing.db.session import Database
from ticketing.tasks.celery_app import celery_app
from ticketing.tasks.outbox import CeleryTaskPublisher, OutboxDispatcher


async def dispatch_outbox_once() -> int:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            dispatcher = OutboxDispatcher(
                session,
                CeleryTaskPublisher(celery_app),
                settings,
            )
            return await dispatcher.dispatch_once()
    finally:
        await database.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="ticketing.dispatch_outbox",
    ignore_result=True,
)
def dispatch_outbox() -> int:
    return asyncio.run(dispatch_outbox_once())
