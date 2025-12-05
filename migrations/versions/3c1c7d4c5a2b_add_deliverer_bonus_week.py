"""add last_bonus_week_start to deliverers

Revision ID: 3c1c7d4c5a2b
Revises: 0f5c9b6b6c41
Create Date: 2025-12-04 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3c1c7d4c5a2b'
down_revision = '0f5c9b6b6c41'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("deliverers")}
    if "last_bonus_week_start" not in cols:
        op.add_column('deliverers', sa.Column('last_bonus_week_start', sa.Date(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("deliverers")}
    if "last_bonus_week_start" in cols:
        op.drop_column('deliverers', 'last_bonus_week_start')
