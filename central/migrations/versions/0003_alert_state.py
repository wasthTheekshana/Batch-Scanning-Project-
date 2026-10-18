"""alert state

Which alerts the webhook has announced as open (so each change is announced once).

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('alert_state',
    sa.Column('alert_id', sa.String(length=200), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('pc_name', sa.String(length=200), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('opened_at', sa.DateTime(), nullable=False),
    sa.Column('last_sent_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('alert_id')
    )


def downgrade() -> None:
    op.drop_table('alert_state')
