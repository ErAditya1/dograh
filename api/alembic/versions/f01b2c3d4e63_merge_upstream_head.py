"""merge heads ee1b2c3d4e62 and d6a83e2b19f0

Revision ID: f01b2c3d4e63
Revises: ee1b2c3d4e62, d6a83e2b19f0
Create Date: 2026-10-22 10:31:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f01b2c3d4e63"
down_revision: Union[str, Sequence[str], None] = ("ee1b2c3d4e62", "d6a83e2b19f0")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
