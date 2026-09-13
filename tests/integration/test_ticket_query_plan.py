import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.support.api_factory import build_test_settings
from ticketing.db.session import Database

pytestmark = pytest.mark.integration


def test_status_filter_uses_composite_index_with_large_tenant_dataset() -> None:
    async def exercise_query_plan() -> tuple[str, str]:
        database = Database.from_settings(build_test_settings())
        user_id = uuid4()
        tenant_id = uuid4()
        membership_id = uuid4()
        salt = str(uuid4())
        try:
            async with database.session() as session:
                await session.execute(
                    text(
                        """
                        INSERT INTO users (id, email, password_hash)
                        VALUES (:user_id, :email, 'query-plan-test-only')
                        """
                    ),
                    {"user_id": user_id, "email": f"query-plan-{user_id}@example.com"},
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO tenants (id, name, slug, owner_user_id)
                        VALUES (:tenant_id, 'Query Plan Tenant', :slug, :user_id)
                        """
                    ),
                    {"tenant_id": tenant_id, "slug": f"query-plan-{tenant_id}", "user_id": user_id},
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO memberships
                            (id, tenant_id, user_id, status, joined_at)
                        VALUES (:membership_id, :tenant_id, :user_id, 'active', now())
                        """
                    ),
                    {
                        "membership_id": membership_id,
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                    },
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO tickets (
                            id,
                            tenant_id,
                            customer_id,
                            created_by_membership_id,
                            assignee_membership_id,
                            subject,
                            description,
                            status,
                            priority,
                            created_at,
                            updated_at
                        )
                        SELECT
                            md5(:salt || series::text)::uuid,
                            :tenant_id,
                            NULL,
                            :membership_id,
                            NULL,
                            'Synthetic ticket ' || series,
                            'Query plan fixture',
                            CASE series % 4
                                WHEN 0 THEN 'open'
                                WHEN 1 THEN 'in_progress'
                                WHEN 2 THEN 'resolved'
                                ELSE 'closed'
                            END,
                            CASE series % 4
                                WHEN 0 THEN 'low'
                                WHEN 1 THEN 'normal'
                                WHEN 2 THEN 'high'
                                ELSE 'urgent'
                            END,
                            now() - make_interval(secs => series),
                            now() - make_interval(secs => series)
                        FROM generate_series(1, 20000) AS series
                        """
                    ),
                    {
                        "salt": salt,
                        "tenant_id": tenant_id,
                        "membership_id": membership_id,
                    },
                )
                await session.commit()

            async with database.session() as session:
                await session.execute(text("ANALYZE tickets"))
                optimized_result = await session.execute(
                    text(
                        """
                        EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                        SELECT id
                        FROM tickets
                        WHERE tenant_id = :tenant_id AND status = 'resolved'
                        ORDER BY created_at DESC, id DESC
                        LIMIT 50
                        """
                    ),
                    {"tenant_id": tenant_id},
                )
                optimized_plan: object = optimized_result.scalar_one()

                await session.execute(text("SET LOCAL enable_indexscan = off"))
                await session.execute(text("SET LOCAL enable_indexonlyscan = off"))
                await session.execute(text("SET LOCAL enable_bitmapscan = off"))
                baseline_result = await session.execute(
                    text(
                        """
                        EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                        SELECT id
                        FROM tickets
                        WHERE tenant_id = :tenant_id AND status = 'resolved'
                        ORDER BY created_at DESC, id DESC
                        LIMIT 50
                        """
                    ),
                    {"tenant_id": tenant_id},
                )
                baseline_plan: object = baseline_result.scalar_one()
                return plan_text(optimized_plan), plan_text(baseline_plan)
        finally:
            await database.dispose()

    optimized, baseline = asyncio.run(exercise_query_plan())

    assert "ix_tickets_tenant_status_created_id" in optimized
    assert '"Actual Rows": 50' in optimized
    assert '"Node Type": "Seq Scan"' in baseline


def plan_text(plan: object) -> str:
    return plan if isinstance(plan, str) else json.dumps(plan)
