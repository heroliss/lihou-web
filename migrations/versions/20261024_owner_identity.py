"""Add an explicit, login-capable site owner and reserved historical usernames."""
from alembic import op
import sqlalchemy as sa

revision = '20261024_owner_identity'
down_revision = '20261024_admin_console'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {field['name'] for field in sa.inspect(bind).get_columns('users')}
    if 'is_site_owner' not in columns:
        op.add_column('users', sa.Column('is_site_owner', sa.Boolean(), nullable=False, server_default=sa.false()))
    if 'merged_into_id' not in columns:
        # SQLite supports a nullable referenced column without rebuilding users.
        if bind.dialect.name == 'sqlite':
            op.execute(sa.text('ALTER TABLE users ADD COLUMN merged_into_id INTEGER REFERENCES users(id)'))
        else:
            op.add_column('users', sa.Column('merged_into_id', sa.Integer(), sa.ForeignKey('users.id')))
    if 'username_aliases' not in set(sa.inspect(bind).get_table_names()):
        op.create_table('username_aliases',
            sa.Column('username', sa.String(64), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('login_allowed', sa.Boolean(), nullable=False, server_default=sa.false()))
        op.create_index('ix_username_aliases_user_id', 'username_aliases', ['user_id'])


def downgrade():
    raise RuntimeError('Owner identity and reserved names preserve attribution; restore a verified backup to roll back.')
