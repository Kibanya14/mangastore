"""add weekly_bonus_paid_count to deliverers

Revision ID: 5f0c2c6d8f3a
Revises: 3c1c7d4c5a2b
Create Date: 2025-12-04 11:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5f0c2c6d8f3a'
down_revision = '3c1c7d4c5a2b'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('deliverers', sa.Column('weekly_bonus_paid_count', sa.Integer(), nullable=True))
    op.execute("UPDATE deliverers SET weekly_bonus_paid_count = 0 WHERE weekly_bonus_paid_count IS NULL")


def downgrade():
    op.drop_column('deliverers', 'weekly_bonus_paid_count')

