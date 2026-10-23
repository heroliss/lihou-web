"""Persist guest, account and system identity kinds.

Revision ID: 20261024_account_kind
Revises: 20261023_hash
"""
from alembic import op
import sqlalchemy as sa

revision = '20261024_account_kind'
down_revision = '20261023_hash'
branch_labels = None
depends_on = None


def upgrade():
    # Public SQLite storage migrates before its first ORM query; skip that
    # already-present column when an operator later runs the normal CLI upgrade.
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('users')}
    if 'account_kind' not in columns:
        op.add_column('users', sa.Column('account_kind', sa.String(16),
                                       nullable=False, server_default='account'))


def downgrade():
    op.drop_column('users', 'account_kind')
