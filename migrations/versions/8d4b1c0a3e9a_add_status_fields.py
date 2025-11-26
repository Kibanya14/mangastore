"""add status tracking for orders and deliverers

Revision ID: 8d4b1c0a3e9a
Revises: 6e1c24fa18df
Create Date: 2024-12-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func


# revision identifiers, used by Alembic.
revision = '8d4b1c0a3e9a'
down_revision = '6e1c24fa18df'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('deliverers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=True, server_default='available'))

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status_changed_at', sa.DateTime(), server_default=func.now(), nullable=True))


def downgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_column('status_changed_at')

    with op.batch_alter_table('deliverers', schema=None) as batch_op:
        batch_op.drop_column('status')

