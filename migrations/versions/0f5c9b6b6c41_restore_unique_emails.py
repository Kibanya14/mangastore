"""restore unique emails

Revision ID: 0f5c9b6b6c41
Revises: 88e3808ebb11
Create Date: 2025-12-03 17:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0f5c9b6b6c41'
down_revision = '88e3808ebb11'
branch_labels = None
depends_on = None


def _dedupe_users(conn):
    """Keep the oldest user per email and repoint FK references before deleting extras."""
    duplicates = conn.execute(sa.text("""
        SELECT email, MIN(id) AS keep_id, array_agg(id ORDER BY id) AS ids
        FROM users
        GROUP BY email
        HAVING COUNT(*) > 1
    """)).mappings().all()

    fk_updates = [
        ("carts", "user_id"),
        ("orders", "user_id"),
        ("access_requests", "admin_id"),
        ("access_requests", "processed_by"),
        ("forum_messages", "user_id"),
    ]

    for dup in duplicates:
        keep_id = dup["keep_id"]
        for user_id in dup["ids"]:
            if user_id == keep_id:
                continue
            for table, column in fk_updates:
                conn.execute(sa.text(f"UPDATE {table} SET {column} = :keep WHERE {column} = :remove"),
                             {"keep": keep_id, "remove": user_id})
            conn.execute(sa.text("DELETE FROM users WHERE id = :remove"), {"remove": user_id})


def _dedupe_deliverers(conn):
    """Keep the oldest deliverer per email and repoint FK references before deleting extras."""
    duplicates = conn.execute(sa.text("""
        SELECT email, MIN(id) AS keep_id, array_agg(id ORDER BY id) AS ids
        FROM deliverers
        GROUP BY email
        HAVING COUNT(*) > 1
    """)).mappings().all()

    fk_updates = [
        ("delivery_assignments", "deliverer_id"),
        ("forum_messages", "deliverer_id"),
    ]

    for dup in duplicates:
        keep_id = dup["keep_id"]
        for deliverer_id in dup["ids"]:
            if deliverer_id == keep_id:
                continue
            for table, column in fk_updates:
                conn.execute(sa.text(f"UPDATE {table} SET {column} = :keep WHERE {column} = :remove"),
                             {"keep": keep_id, "remove": deliverer_id})
            conn.execute(sa.text("DELETE FROM deliverers WHERE id = :remove"), {"remove": deliverer_id})


def upgrade():
    conn = op.get_bind()
    # Assurer is_active non NULL/defaut TRUE
    conn.execute(sa.text("UPDATE users SET is_active = TRUE WHERE is_active IS NULL"))
    op.alter_column('users', 'is_active', existing_type=sa.Boolean(), server_default=sa.true(), nullable=False)

    # Nettoyer les doublons d'emails avant de restaurer l'unicité
    _dedupe_users(conn)
    _dedupe_deliverers(conn)

    # Restaurer l'unicité des emails
    op.create_unique_constraint('users_email_key', 'users', ['email'], postgresql_nulls_not_distinct=False)
    op.create_unique_constraint('deliverers_email_key', 'deliverers', ['email'], postgresql_nulls_not_distinct=False)


def downgrade():
    op.drop_constraint('users_email_key', 'users', type_='unique')
    op.drop_constraint('deliverers_email_key', 'deliverers', type_='unique')
    # On laisse is_active en place
