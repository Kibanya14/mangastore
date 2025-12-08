"""add last_forum_seen_at to users and deliverers

Revision ID: 0d4eb9d6c1c3
Revises: a4d9d0b5c7e2
Create Date: 2025-02-10 00:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0d4eb9d6c1c3'
down_revision = 'a4d9d0b5c7e2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, col in [('users', 'last_forum_seen_at'), ('deliverers', 'last_forum_seen_at')]:
        cols = {c["name"] for c in inspector.get_columns(table)}
        if col not in cols:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.add_column(sa.Column(col, sa.DateTime(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, col in [('users', 'last_forum_seen_at'), ('deliverers', 'last_forum_seen_at')]:
        cols = {c["name"] for c in inspector.get_columns(table)}
        if col in cols:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_column(col)
