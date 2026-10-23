"""Link articles to projects and persist short-lived transactional-mail limits."""
from alembic import op
import sqlalchemy as sa

revision = '20261024_projects'
down_revision = '20261024_account_kind'
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if 'project_entries' not in tables:
        op.create_table('project_entries',
                        sa.Column('post_id', sa.Integer(), sa.ForeignKey('posts.id'), primary_key=True),
                        sa.Column('project_slug', sa.String(80), nullable=False))
        op.create_index('ix_project_entries_project_slug', 'project_entries', ['project_slug'])
    if 'public_mail_rate' not in tables:
        op.create_table('public_mail_rate',
                        sa.Column('bucket', sa.Text(), primary_key=True),
                        sa.Column('started_at', sa.Float(), nullable=False),
                        sa.Column('hits', sa.Integer(), nullable=False))


def downgrade():
    op.drop_table('project_entries')
    op.drop_table('public_mail_rate')
