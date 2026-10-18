"""fleet reporting

PCs report their app version, the project versions they run, and daily counts with their heartbeat;
the admin dashboard's Fleet and Activity views read them.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('pc_applied',
    sa.Column('pc_id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=64), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('reported_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['pc_id'], ['pcs.id'], ),
    sa.PrimaryKeyConstraint('pc_id', 'project_id')
    )
    op.create_table('pc_daily_stats',
    sa.Column('pc_id', sa.String(length=36), nullable=False),
    sa.Column('day', sa.String(length=10), nullable=False),
    sa.Column('documents', sa.Integer(), nullable=False),
    sa.Column('pages', sa.Integer(), nullable=False),
    sa.Column('batches', sa.Integer(), nullable=False),
    sa.Column('errors', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['pc_id'], ['pcs.id'], ),
    sa.PrimaryKeyConstraint('pc_id', 'day')
    )
    # plain ADD COLUMN (not a table rebuild): other tables point at pcs, and a rebuild of a
    # referenced table fails on SQLite when foreign keys are enforced
    op.add_column('pcs', sa.Column('app_version', sa.String(length=32), nullable=True))
    op.add_column('pcs', sa.Column('reported_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('pcs', 'reported_at')
    op.drop_column('pcs', 'app_version')
    op.drop_table('pc_daily_stats')
    op.drop_table('pc_applied')
