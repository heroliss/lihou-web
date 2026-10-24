"""Explicit, repeatable publication of the site's own writing guide.

This module is not part of request-time preparation or welcome seeding. Existing
posts are never updated, including a guide that its author later edits or removes.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from . import db
from .models import Post, User, utcnow
from .public_demo_storage import backup_database


GUIDE_KEY = 'writing-guide-v1'
GUIDE_TITLE = '在 Liss Community 写下第一篇文章'
GUIDE_SOURCE = Path(__file__).resolve().parents[1] / 'docs' / 'writing-guide.md'


class WritingGuidePublication(db.Model):
    __tablename__ = 'site_guide_publications'
    guide_key = db.Column(db.String(64), primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False, unique=True)
    published_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class WritingGuideError(RuntimeError):
    pass


def get_published_writing_guide():
    """Read the explicit publication record without preparing or mutating storage."""
    if (db.engine.url.get_backend_name() == 'sqlite'
            and db.engine.url.database != ':memory:'
            and not Path(db.engine.url.database).is_file()):
        return None
    if not inspect(db.engine).has_table(WritingGuidePublication.__tablename__):
        return None
    with db.session.no_autoflush:
        record = db.session.get(WritingGuidePublication, GUIDE_KEY)
        post = db.session.get(Post, record.post_id) if record is not None else None
        if (post is None or post.hidden or post.author is None or post.author.username != 'demo'
                or not post.author.is_system
                or post.author.email not in {'demo@example.com', 'demo@example.invalid'}):
            return None
    return post


def publish_writing_guide(*, dry_run=False):
    """Create one guide as the existing fixed site author; preserve every old row."""
    sandbox = current_app.extensions.get('public_demo')
    if sandbox is None:
        raise WritingGuideError('Requires the prepared public-demo site database.')
    sandbox._assert_database()
    if not Path(db.engine.url.database).is_file():
        raise WritingGuideError('Prepare site storage explicitly before publishing the guide.')
    if db.session.new or db.session.dirty or db.session.deleted:
        raise WritingGuideError('Use a clean database session; unrelated pending changes will not be published.')
    with sandbox.exclusive():
        tables = set(inspect(db.engine).get_table_names())
        if not {'users', 'posts', 'comments', 'follows', 'roles'} <= tables:
            raise WritingGuideError('Prepare site storage explicitly before publishing the guide.')
        author = User.query.filter_by(username='demo').first()
        if (author is None or not author.is_system
                or author.email not in {'demo@example.com', 'demo@example.invalid'}
                or author.posts.count() == 0):
            raise WritingGuideError('The existing fixed Liss system author could not be verified; nothing was changed.')
        published = (db.session.get(WritingGuidePublication, GUIDE_KEY)
                     if WritingGuidePublication.__tablename__ in tables else None)
        if published is not None:
            existing = db.session.get(Post, published.post_id)
            if existing is None or existing.author_id != author.id:
                raise WritingGuideError('The published guide was removed or its author changed; refusing to replace content.')
            return {'status': 'already-published', 'post_id': existing.id,
                    'author_id': author.id, 'author_username': author.username,
                    'title': GUIDE_TITLE}
        body = GUIDE_SOURCE.read_text(encoding='utf-8').strip()
        if not body.startswith('# ' + GUIDE_TITLE + '\n'):
            raise WritingGuideError('The guide source is missing its expected title.')
        result = {'status': 'would-create', 'post_id': None, 'author_id': author.id,
                  'author_username': author.username, 'title': GUIDE_TITLE}
        if dry_run:
            return result
        sandbox.check_storage()
        database = Path(db.engine.url.database)
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        backup = backup_database(database, database.parent /
                                 f'before-writing-guide-{timestamp}-{secrets.token_hex(3)}.sqlite')
        try:
            WritingGuidePublication.__table__.create(bind=db.engine, checkfirst=True)
            post = Post(body=body, author_id=author.id)
            db.session.add(post)
            db.session.flush()
            db.session.add(WritingGuidePublication(guide_key=GUIDE_KEY, post_id=post.id))
            db.session.commit()
        except BaseException:
            db.session.rollback()
            raise
        result.update(status='created', post_id=post.id, backup=str(backup))
        return result


@click.command('publish-writing-guide')
@click.option('--dry-run', is_flag=True, help='Check the existing author and publication record without changing storage.')
@with_appcontext
def publish_writing_guide_command(dry_run):
    """Explicitly publish one writing guide as the existing Liss site author."""
    try:
        result = publish_writing_guide(dry_run=dry_run)
    except (WritingGuideError, OSError, SQLAlchemyError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, ensure_ascii=False))
