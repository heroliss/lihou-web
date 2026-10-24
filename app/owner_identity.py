"""Explicit, backed-up consolidation of the old site author into a real account."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets

import click
from flask import current_app, has_request_context
from flask.cli import with_appcontext
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.exc import SQLAlchemyError

from . import db
from .models import Comment, Follow, Post, User, UsernameAlias
from .public_demo_storage import backup_database


class OwnerMergeError(RuntimeError):
    pass


def legacy_site_author(user):
    return bool(user and user.username == 'demo' and user.is_system
                and user.email in {'demo@example.com', 'demo@example.invalid'})


def get_site_owner(*, legacy_fallback=True):
    """The explicit owner is independent of the administrator permission bit."""
    owner = User.query.filter_by(is_site_owner=True, merged_into_id=None).first()
    if owner is not None:
        return owner
    if legacy_fallback:
        candidate = User.query.filter_by(username='demo').first()
        if legacy_site_author(candidate) and candidate.merged_into_id is None:
            return candidate
    return None


def user_for_username(username, *, login=False):
    """Resolve reserved aliases before the disabled historical identity row."""
    if not isinstance(username, str) or not username or len(username) > 64:
        return None
    name = username.lower()
    alias = db.session.get(UsernameAlias, name)
    if alias is not None:
        if login and not alias.login_allowed:
            return None
        return db.session.get(User, alias.user_id)
    return User.query.filter(func.lower(User.username) == name).first()


def _owner_plan(username, new_username):
    canonical_key = new_username.lower() if isinstance(new_username, str) else ''
    if (not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{2,31}', new_username or '')
            or canonical_key.startswith('guest_') or canonical_key in {'demo', 'system', 'admin', 'administrator'}):
        raise OwnerMergeError('New username must be an account name of 3 to 32 characters.')
    target = User.query.filter_by(username=username).first()
    if target is None:
        alias = db.session.get(UsernameAlias, username.lower()) if username else None
        target = db.session.get(User, alias.user_id) if alias and alias.login_allowed else None
    if (target is None or not target.is_registered or target.disabled or target.merged_into_id
            or not target.password_hash):
        raise OwnerMergeError('Source login must identify an existing active registered account; nothing changed.')
    owners = User.query.filter_by(is_site_owner=True).all()
    if any(owner.id != target.id for owner in owners):
        raise OwnerMergeError('A different site owner already exists; refusing to merge unrelated accounts.')
    source = User.query.filter_by(username='demo').first()
    if not legacy_site_author(source) or source.id == target.id:
        raise OwnerMergeError('The original system site-author identity could not be verified; nothing changed.')
    if source.merged_into_id is not None and source.merged_into_id != target.id:
        raise OwnerMergeError('The old author is already linked to a different account; nothing changed.')
    # Case-insensitive collisions are rejected even on SQLite's case-sensitive
    # unique column. An alias may coexist only with its disabled source row.
    collision = User.query.filter(func.lower(User.username) == canonical_key,
                                  User.id != target.id).first()
    if collision is not None:
        raise OwnerMergeError('The requested username belongs to another identity; nothing changed.')
    for name, login_allowed in ((target.username.lower(), True), ('demo', False), (canonical_key, True)):
        alias = db.session.get(UsernameAlias, name)
        if alias is not None and (alias.user_id != target.id or bool(alias.login_allowed) != login_allowed):
            raise OwnerMergeError('A historical name has a conflicting reservation; nothing changed.')
        conflicting = User.query.filter(func.lower(User.username) == name,
            User.id.notin_([target.id, source.id])).first()
        if conflicting:
            raise OwnerMergeError('A historical name collides with another identity; nothing changed.')
    affected = Follow.query.filter(or_(Follow.follower_id == source.id, Follow.followed_id == source.id)).all()
    mapped = {(target.id if f.follower_id == source.id else f.follower_id,
               target.id if f.followed_id == source.id else f.followed_id) for f in affected}
    existing = {(f.follower_id, f.followed_id) for f in Follow.query.filter(
        Follow.follower_id != source.id, Follow.followed_id != source.id).all()}
    counts = dict(posts=Post.query.filter_by(author_id=source.id).count(),
        comments=Comment.query.filter_by(author_id=source.id).count(),
        follows=len(affected), follows_after_merge=len(mapped - existing),
        follows_deduplicated=len(affected) - len(mapped - existing))
    complete = (target.is_site_owner and target.username == new_username
                and target.name == 'Liss' and target.is_administrator()
                and source.disabled and source.merged_into_id == target.id
                and not counts['posts'] and not counts['comments'] and not affected
                and all(db.session.get(UsernameAlias, name) is not None
                        for name in {'demo', username.lower()} - {canonical_key}))
    report = dict(status='already-merged' if complete else 'would-merge',
        account_id=target.id, old_username=target.username, new_username=new_username,
        historical_author_id=source.id, content=counts,
        credentials_preserved=True, current_account_sessions_preserved=True)
    return target, source, report


def merge_site_owner(*, username, new_username='Liss', apply=False):
    """Read-only by default; keep the login ID and credentials, merge content atomically."""
    sandbox = current_app.extensions.get('public_demo')
    if sandbox is None:
        raise OwnerMergeError('Requires the prepared public-demo site database.')
    sandbox._assert_database()
    database = Path(db.engine.url.database)
    if not database.is_file():
        raise OwnerMergeError('Prepare the existing site storage before planning an owner merge.')
    if db.session.new or db.session.dirty or db.session.deleted:
        raise OwnerMergeError('Use a clean database session; unrelated pending changes will not be committed.')
    with sandbox.exclusive():
        required = {'users', 'posts', 'comments', 'follows', 'username_aliases', 'admin_audit', 'admin_confirmations'}
        if not required <= set(inspect(db.engine).get_table_names()):
            raise OwnerMergeError('Run prepare-public-demo before planning this merge; storage is not ready.')
        target, source, result = _owner_plan(username, new_username)
        if not apply or result['status'] == 'already-merged':
            return result
        # All request writes use this same file lock. A consistent snapshot is
        # taken before any account, content, follow or confirmation change.
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        backup = backup_database(database, database.parent /
            f'before-owner-merge-{timestamp}-{secrets.token_hex(3)}.sqlite')
        from .admin import AdminConfirmation, record_audit, role_for
        before = dict(username=target.username, role=target.admin_role_key,
                      site_owner=bool(target.is_site_owner), historical_author_id=source.id)
        try:
            # Keep existing post IDs, timestamps, hidden state, bodies, project
            # links and writing-guide publication records exactly as stored.
            Post.query.filter_by(author_id=source.id).update({'author_id': target.id}, synchronize_session=False)
            Comment.query.filter_by(author_id=source.id).update({'author_id': target.id}, synchronize_session=False)
            follows = Follow.query.filter(or_(Follow.follower_id == source.id, Follow.followed_id == source.id)).all()
            mapped = {}
            for follow in follows:
                key = (target.id if follow.follower_id == source.id else follow.follower_id,
                       target.id if follow.followed_id == source.id else follow.followed_id)
                previous = mapped.get(key)
                if previous is None or (follow.timestamp and follow.timestamp < previous):
                    mapped[key] = follow.timestamp
                db.session.delete(follow)
            db.session.flush()
            for (follower_id, followed_id), timestamp in mapped.items():
                existing = db.session.get(Follow, (follower_id, followed_id))
                if existing is None:
                    db.session.add(Follow(follower_id=follower_id, followed_id=followed_id, timestamp=timestamp))
                elif timestamp and (not existing.timestamp or timestamp < existing.timestamp):
                    existing.timestamp = timestamp
            for name, login_allowed in ((target.username.lower(), True), ('demo', False)):
                if name != new_username.lower() and db.session.get(UsernameAlias, name) is None:
                    db.session.add(UsernameAlias(username=name, user_id=target.id, login_allowed=login_allowed))
            target.username, target.name = new_username, 'Liss'
            target.is_site_owner = True
            target.role = role_for('admin')
            if target.role is None:
                raise OwnerMergeError('The administrator role is missing; nothing changed.')
            # A retained tombstone protects historical audit FK references and
            # reserves the old source name. It can never authenticate or return.
            source.disabled = True
            source.session_version += 1
            source.merged_into_id = target.id
            source.is_site_owner = False
            # Invalidate signed previews without rotating the real user's
            # credential stamp or forcing an unnecessary logout.
            AdminConfirmation.query.filter_by(consumed=False).update({'consumed': True}, synchronize_session=False)
            after = dict(username=new_username, role='admin', site_owner=True, merged_from=source.id,
                         transferred=result['content'])
            record_audit('user.merge_site_owner', 'user', target, before, after, cli=True)
            db.session.flush()
            if db.session.execute(text('PRAGMA foreign_key_check')).first() is not None:
                raise OwnerMergeError('Foreign-key validation failed; the whole merge was rolled back.')
            db.session.commit()
        except BaseException:
            db.session.rollback()
            raise
        result.update(status='merged', backup=str(backup))
        return result


@click.command('merge-site-owner')
@click.option('--username', required=True, help='Exact existing registered username or its reserved old login name.')
@click.option('--new-username', default='Liss', show_default=True)
@click.option('--apply', is_flag=True, help='Apply the planned merge after making a consistent safety backup.')
@click.option('--dry-run', is_flag=True, help='Explicit read-only plan; this is also the default.')
@with_appcontext
def merge_site_owner_command(username, new_username, apply, dry_run):
    """Consolidate the original system author into one real, login-capable owner."""
    if apply and dry_run:
        raise click.ClickException('Choose --apply or --dry-run, not both.')
    try:
        result = merge_site_owner(username=username, new_username=new_username, apply=apply)
    except OwnerMergeError as error:
        raise click.ClickException(str(error)) from error
    except (OSError, SQLAlchemyError):
        # SQL exceptions can contain parameter values. Never echo credentials,
        # private email or the random historical-account hash in CLI output.
        raise click.ClickException('Owner merge failed; no partial merge was committed. Check private server diagnostics.') from None
    click.echo(json.dumps(result, ensure_ascii=False))


def install(app):
    app.cli.add_command(merge_site_owner_command)

    @app.context_processor
    def owner_context():
        # Transactional mail and offline template previews can render inside a
        # bare request context without running the site's preparation lifecycle.
        # Rendering an email must never create or require the community tables.
        sandbox = app.extensions['public_demo']
        return dict(site_owner=get_site_owner() if has_request_context() and sandbox.initialized else None)
