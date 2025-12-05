"""add activity log table and enforce unique emails

Revision ID: b7e2c1d4f8aa
Revises: 5f0c2c6d8f3a
Create Date: 2025-02-06 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b7e2c1d4f8aa'
down_revision = '5f0c2c6d8f3a'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    # --- Activity Log table ---
    tables = inspector.get_table_names()
    if 'activity_logs' not in tables:
        op.create_table(
            'activity_logs',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('action', sa.String(length=255), nullable=False),
            sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('actor_email', sa.String(length=120), nullable=True),
            sa.Column('actor_name', sa.String(length=120), nullable=True),
            sa.Column('actor_phone', sa.String(length=30), nullable=True),
            sa.Column('extra', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_activity_logs_created_at', 'activity_logs', ['created_at'], unique=False)

    # --- Unicité des emails users / deliverers ---
    # S'assurer que is_active est true pour éviter des problèmes de NOT NULL éventuels
    op.execute(sa.text("UPDATE users SET is_active = TRUE WHERE is_active IS NULL"))

    # Recréer les contraintes uniques si absentes (SQLite: via index unique)
    user_uniques = {c['name'] for c in inspector.get_unique_constraints('users')}
    if 'users_email_key' not in user_uniques:
        op.create_unique_constraint('users_email_key', 'users', ['email'])

    deliverer_uniques = {c['name'] for c in inspector.get_unique_constraints('deliverers')}
    if 'deliverers_email_key' not in deliverer_uniques:
        op.create_unique_constraint('deliverers_email_key', 'deliverers', ['email'])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Drop uniques (only if they exist)
    user_uniques = {c['name'] for c in inspector.get_unique_constraints('users')}
    if 'users_email_key' in user_uniques:
        op.drop_constraint('users_email_key', 'users', type_='unique')

    deliverer_uniques = {c['name'] for c in inspector.get_unique_constraints('deliverers')}
    if 'deliverers_email_key' in deliverer_uniques:
        op.drop_constraint('deliverers_email_key', 'deliverers', type_='unique')

    # Drop activity_logs table/index if present
    tables = inspector.get_table_names()
    if 'activity_logs' in tables:
        indexes = {idx['name'] for idx in inspector.get_indexes('activity_logs')}
        if 'ix_activity_logs_created_at' in indexes:
            op.drop_index('ix_activity_logs_created_at', table_name='activity_logs')
        op.drop_table('activity_logs')
