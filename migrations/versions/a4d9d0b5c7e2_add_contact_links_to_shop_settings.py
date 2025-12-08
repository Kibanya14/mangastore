"""add contact links to shop settings

Revision ID: a4d9d0b5c7e2
Revises: cdf5a2649b24
Create Date: 2025-02-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4d9d0b5c7e2'
down_revision = 'cdf5a2649b24'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("shop_settings")}
    to_add = []
    if "facebook_url" not in cols:
        to_add.append(sa.Column('facebook_url', sa.String(length=255), nullable=True))
    if "whatsapp_number" not in cols:
        to_add.append(sa.Column('whatsapp_number', sa.String(length=30), nullable=True))
    if "whatsapp_group_url" not in cols:
        to_add.append(sa.Column('whatsapp_group_url', sa.String(length=255), nullable=True))

    if to_add:
        with op.batch_alter_table('shop_settings', schema=None) as batch_op:
            for col in to_add:
                batch_op.add_column(col)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("shop_settings")}
    to_drop = []
    if "facebook_url" in cols:
        to_drop.append('facebook_url')
    if "whatsapp_number" in cols:
        to_drop.append('whatsapp_number')
    if "whatsapp_group_url" in cols:
        to_drop.append('whatsapp_group_url')

    if to_drop:
        with op.batch_alter_table('shop_settings', schema=None) as batch_op:
            for name in to_drop:
                batch_op.drop_column(name)
