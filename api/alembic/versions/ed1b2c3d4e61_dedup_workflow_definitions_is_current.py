"""dedup workflow definitions is_current

Revision ID: ed1b2c3d4e61
Revises: ec1b2c3d4e60
Create Date: 2026-10-15 11:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ed1b2c3d4e61"
down_revision: Union[str, None] = "ec1b2c3d4e60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dedup is_current=true in workflow_definitions so that at most one definition
    # is marked is_current per workflow (preferring released_definition_id, then newest id).
    op.execute(
        """
        WITH ranked AS (
            SELECT
                wd.id,
                wd.workflow_id,
                ROW_NUMBER() OVER (
                    PARTITION BY wd.workflow_id
                    ORDER BY
                        CASE WHEN w.released_definition_id = wd.id THEN 0 ELSE 1 END,
                        wd.id DESC
                ) as rn
            FROM workflow_definitions wd
            JOIN workflows w ON w.id = wd.workflow_id
            WHERE wd.is_current = true
        )
        UPDATE workflow_definitions
        SET is_current = false
        WHERE id IN (
            SELECT id FROM ranked WHERE rn > 1
        );
        """
    )


def downgrade() -> None:
    pass
