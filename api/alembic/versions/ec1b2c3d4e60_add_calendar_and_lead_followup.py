"""add native calendar and lead followup tables

Revision ID: ec1b2c3d4e60
Revises: eb1b2c3d4e59
Create Date: 2026-10-01 10:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ec1b2c3d4e60"
down_revision: Union[str, None] = "eb1b2c3d4e59"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. organization_calendar_settings
    op.create_table(
        "organization_calendar_settings",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
            index=True,
        ),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default=sa.text("'Asia/Kolkata'")),
        sa.Column(
            "weekly_schedule",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(
                '\'{"mon":["10:00-19:00"],"tue":["10:00-19:00"],"wed":["10:00-19:00"],"thu":["10:00-19:00"],"fri":["10:00-19:00"],"sat":["10:00-17:00"],"sun":[]}\'::json'
            ),
        ),
        sa.Column("slot_duration_mins", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column("buffer_mins", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("max_advance_days", sa.Integer(), nullable=False, server_default=sa.text("14")),
        sa.Column("meeting_title_template", sa.String(length=255), nullable=False, server_default=sa.text("'Consultation with {lead_name}'")),
        sa.Column("location_type", sa.String(length=64), nullable=False, server_default=sa.text("'phone_call'")),
        sa.Column("static_meeting_url", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )

    # 2. scheduled_appointments
    op.create_table(
        "scheduled_appointments",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("contact_id", sa.Integer(), nullable=True, index=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("customer_name", sa.String(length=255), nullable=False),
        sa.Column("customer_phone", sa.String(length=64), nullable=False, index=True),
        sa.Column("customer_email", sa.String(length=255), nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'confirmed'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("meeting_link", sa.String(length=512), nullable=True),
        sa.Column("ics_uid", sa.String(length=128), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )

    # 3. campaign_followup_configs
    op.create_table(
        "campaign_followup_configs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
            index=True,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("is_auto_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "trigger_intents",
            sa.JSON(),
            nullable=False,
            server_default=sa.text('\'["interested", "appointment"]\'::json'),
        ),
        sa.Column("min_lead_score", sa.Integer(), nullable=False, server_default=sa.text("60")),
        sa.Column(
            "channels",
            sa.JSON(),
            nullable=False,
            server_default=sa.text('\'{"whatsapp": true, "sms": true, "email": false}\'::json'),
        ),
        sa.Column("whatsapp_template", sa.Text(), nullable=True),
        sa.Column("sms_template", sa.Text(), nullable=True),
        sa.Column("email_subject", sa.String(length=255), nullable=True),
        sa.Column("email_body_template", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )

    # 4. lead_notifications_log
    op.create_table(
        "lead_notifications_log",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("contact_id", sa.Integer(), nullable=True, index=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("channel", sa.String(length=32), nullable=False, index=True),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("content_sent", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'sent'")),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), index=True),
    )


def downgrade() -> None:
    op.drop_table("lead_notifications_log")
    op.drop_table("campaign_followup_configs")
    op.drop_table("scheduled_appointments")
    op.drop_table("organization_calendar_settings")
