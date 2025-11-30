"""message

Revision ID: 201ca72d4868
Revises: 
Create Date: 2025-11-25 11:19:22.785312

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '201ca72d4868'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("shop_settings")]
    if "deliverer_logo" not in columns:
        with op.batch_alter_table('shop_settings', schema=None) as batch_op:
            batch_op.add_column(sa.Column('deliverer_logo', sa.String(length=255), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("shop_settings")]
    if "deliverer_logo" in columns:
        with op.batch_alter_table('shop_settings', schema=None) as batch_op:
            batch_op.drop_column('deliverer_logo')
