"""Allow current Werkzeug password hash formats."""
from alembic import op
import sqlalchemy as sa

revision = '20261023_hash'
down_revision = '51f5ccfba190'


def upgrade():
    with op.batch_alter_table('users') as batch:
        batch.alter_column('password_hash', existing_type=sa.String(128), type_=sa.String(256))


def downgrade():
    users = sa.table('users', sa.column('password_hash', sa.String()))
    count = op.get_bind().execute(sa.select(sa.func.count()).select_from(users).where(
        sa.func.length(users.c.password_hash) > 128)).scalar_one()
    if count:
        raise RuntimeError('Cannot shorten password hashes without losing current credentials.')
    with op.batch_alter_table('users') as batch:
        batch.alter_column('password_hash', existing_type=sa.String(256), type_=sa.String(128))
