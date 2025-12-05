"""add icon to categories

Revision ID: cdf5a2649b24
Revises: b7e2c1d4f8aa
Create Date: 2025-02-06 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'cdf5a2649b24'
down_revision = 'b7e2c1d4f8aa'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("categories")}
    if "icon" not in cols:
        with op.batch_alter_table('categories', schema=None) as batch_op:
            batch_op.add_column(sa.Column('icon', sa.String(length=80), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("categories")}
    if "icon" in cols:
        with op.batch_alter_table('categories', schema=None) as batch_op:
            batch_op.drop_column('icon')
