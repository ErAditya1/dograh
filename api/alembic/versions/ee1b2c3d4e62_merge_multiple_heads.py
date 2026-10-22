"""merge heads c82d4f6a9b10 and ed1b2c3d4e61

Revision ID: ee1b2c3d4e62
Revises: c82d4f6a9b10, ed1b2c3d4e61
Create Date: 2026-10-22 09:56:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ee1b2c3d4e62"
down_revision: Union[str, Sequence[str], None] = ("c82d4f6a9b10", "ed1b2c3d4e61")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
