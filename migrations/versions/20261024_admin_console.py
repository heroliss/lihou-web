"""Add retained-content moderation and the registered-administrator console.

Public SQLite preparation may have added these fields before an explicit normal
CLI upgrade. Inspect first so both supported upgrade paths preserve existing rows.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261024_admin_console'
down_revision = '20261024_projects'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    additions = {
        'users': [sa.Column('disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
                  sa.Column('session_version', sa.Integer(), nullable=False, server_default='0')],
        'posts': [sa.Column('hidden', sa.Boolean(), nullable=False, server_default=sa.false())],
    }
    for table, fields in additions.items():
        columns = {field['name'] for field in sa.inspect(bind).get_columns(table)}
        for column in fields:
            if column.name not in columns:
                op.add_column(table, column)
    if 'ix_posts_hidden' not in {index['name'] for index in sa.inspect(bind).get_indexes('posts')}:
        op.create_index('ix_posts_hidden', 'posts', ['hidden'])
    # Historically a NULL disabled flag meant a visible comment. Keep that
    # meaning; no SQLite table rewrite or removal of existing comments is needed.
    op.execute(sa.text('UPDATE comments SET disabled=false WHERE disabled IS NULL'))
    tables = set(sa.inspect(bind).get_table_names())
    if 'site_settings' not in tables:
        op.create_table('site_settings',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('site_name', sa.String(60), nullable=False),
            sa.Column('tagline', sa.String(160), nullable=False),
            sa.Column('welcome', sa.String(500), nullable=False),
            sa.Column('registration_open', sa.Boolean(), nullable=False),
            sa.Column('guest_posting_open', sa.Boolean(), nullable=False),
            sa.Column('guest_post_limit', sa.Integer(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False))
    if 'admin_audit' not in tables:
        op.create_table('admin_audit',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('actor_name', sa.String(64), nullable=False),
            sa.Column('action', sa.String(40), nullable=False),
            sa.Column('target_type', sa.String(16), nullable=False),
            sa.Column('target_id', sa.Integer(), nullable=True),
            sa.Column('target_label', sa.String(100), nullable=False),
            sa.Column('details', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False))
        op.create_index('ix_admin_audit_actor_id', 'admin_audit', ['actor_id'])
        op.create_index('ix_admin_audit_created_at', 'admin_audit', ['created_at'])
    if 'admin_confirmations' not in tables:
        op.create_table('admin_confirmations',
            sa.Column('nonce_hash', sa.String(64), primary_key=True),
            sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('consumed', sa.Boolean(), nullable=False))
        op.create_index('ix_admin_confirmations_created_at', 'admin_confirmations', ['created_at'])


def downgrade():
    # Removing these columns or tables would erase moderation state and audit
    # history, potentially republishing hidden content. Require a verified data
    # backup and an explicit operator-controlled rollback instead.
    raise RuntimeError('This revision preserves moderation state; restore a verified pre-upgrade backup to roll back.')
