"""add platform_inventory_numbers table

Revision ID: f3a1c47b9e03
Revises: f3a1c47b9e02
Create Date: 2026-09-08 22:42:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a1c47b9e03'
down_revision: Union[str, None] = 'f3a1c47b9e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_inventory_numbers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('phone_number', sa.String(length=64), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('carrier', sa.String(length=64), nullable=False),
        sa.Column('number_type', sa.String(length=32), server_default='shared_trial', nullable=False),
        sa.Column('country_code', sa.String(length=8), server_default='US', nullable=False),
        sa.Column('monthly_cost', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('status', sa.String(length=32), server_default='available', nullable=False),
        sa.Column('provider_config', sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column('assigned_organization_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['assigned_organization_id'], ['organizations.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('phone_number'),
    )
    op.create_index('ix_platform_numbers_status', 'platform_inventory_numbers', ['status'], unique=False)
    op.create_index('ix_platform_numbers_type', 'platform_inventory_numbers', ['number_type'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_platform_numbers_type', table_name='platform_inventory_numbers')
    op.drop_index('ix_platform_numbers_status', table_name='platform_inventory_numbers')
    op.drop_table('platform_inventory_numbers')
