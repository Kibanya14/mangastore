"""add telegram fields to shop settings

Revision ID: e7c2f9a1b4d8
Revises: a4d9d0b5c7e2
Create Date: 2026-01-31 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7c2f9a1b4d8'
down_revision = 'a4d9d0b5c7e2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("shop_settings")}
    to_add = []
    if "telegram_username" not in cols:
        to_add.append(sa.Column('telegram_username', sa.String(length=60), nullable=True))
    if "telegram_url" not in cols:
        to_add.append(sa.Column('telegram_url', sa.String(length=255), nullable=True))

    if to_add:
        with op.batch_alter_table('shop_settings', schema=None) as batch_op:
            for col in to_add:
                batch_op.add_column(col)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("shop_settings")}
    to_drop = []
    if "telegram_username" in cols:
        to_drop.append('telegram_username')
    if "telegram_url" in cols:
        to_drop.append('telegram_url')

    if to_drop:
        with op.batch_alter_table('shop_settings', schema=None) as batch_op:
            for name in to_drop:
                batch_op.drop_column(name)
