"""add is_featured to products

Revision ID: 2b9f4a7c8d12
Revises: 0d4eb9d6c1c3
Create Date: 2025-02-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2b9f4a7c8d12'
down_revision = '0d4eb9d6c1c3'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("products")}

    if "is_featured" not in cols:
        with op.batch_alter_table('products', schema=None) as batch_op:
            batch_op.add_column(sa.Column('is_featured', sa.Boolean(), server_default=sa.false(), nullable=False))
        op.execute("UPDATE products SET is_featured = FALSE WHERE is_featured IS NULL")


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("products")}

    if "is_featured" in cols:
        with op.batch_alter_table('products', schema=None) as batch_op:
            batch_op.drop_column('is_featured')
