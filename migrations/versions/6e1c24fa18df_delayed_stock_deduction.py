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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("orders")}
    to_add = []
    if "delivered_at" not in cols:
        to_add.append(sa.Column('delivered_at', sa.DateTime(), nullable=True))
    if "stock_deducted" not in cols:
        to_add.append(sa.Column('stock_deducted', sa.Boolean(), nullable=False, server_default=sa.true()))
    if to_add:
        with op.batch_alter_table('orders', schema=None) as batch_op:
            for col in to_add:
                batch_op.add_column(col)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("orders")}
    to_drop = []
    if "stock_deducted" in cols:
        to_drop.append('stock_deducted')
    if "delivered_at" in cols:
        to_drop.append('delivered_at')
    if to_drop:
        with op.batch_alter_table('orders', schema=None) as batch_op:
            for name in to_drop:
                batch_op.drop_column(name)
