"""delayed stock deduction

Revision ID: 6e1c24fa18df
Revises: 201ca72d4868
Create Date: 2024-12-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6e1c24fa18df'
down_revision = '201ca72d4868'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('delivered_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('stock_deducted', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_column('stock_deducted')
        batch_op.drop_column('delivered_at')

