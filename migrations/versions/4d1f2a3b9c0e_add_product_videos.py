"""add product videos

Revision ID: 4d1f2a3b9c0e
Revises: 2b9f4a7c8d12
Create Date: 2025-02-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4d1f2a3b9c0e'
down_revision = '2b9f4a7c8d12'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("products")}

    if "videos" not in cols:
        with op.batch_alter_table('products', schema=None) as batch_op:
            batch_op.add_column(sa.Column('videos', sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("products")}

    if "videos" in cols:
        with op.batch_alter_table('products', schema=None) as batch_op:
            batch_op.drop_column('videos')
