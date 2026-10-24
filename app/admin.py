"""Registered-administrator console for the persistent LISS community.

Every mutation is CSRF-protected, previewed, signed, bound to one browser and
checked against the current record again before applying. Public audit records
contain identifiers and moderation state, never passwords, email or post bodies.
"""
from datetime import timedelta
import hashlib
import hmac
import json
import re
import secrets
from urllib.parse import urlsplit

import click
from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, session, url_for)
from flask_login import current_user
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import or_

from . import db
from .models import Comment, Permission, Post, Role, User, utcnow


admin = Blueprint('admin', __name__, url_prefix='/admin')
ROLE_CHOICES = [('normal', '普通用户'), ('moderator', '版主'), ('admin', '管理员')]
SETTINGS_KEYS = ('site_name', 'tagline', 'welcome', 'registration_open',
                 'guest_posting_open', 'guest_post_limit')
DEFAULT_SETTINGS = dict(site_name='Liss Community',
    tagline='记录日常，分享发现，和有趣的人聊一聊。',
    welcome='LISS 欢迎认真记录，也欢迎随手分享。一个问题、一段经历、一点新发现，都值得留下。',
    registration_open=True, guest_posting_open=True, guest_post_limit=10)


class SiteSettings(db.Model):
    __tablename__ = 'site_settings'
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(60), nullable=False, default=DEFAULT_SETTINGS['site_name'])
    tagline = db.Column(db.String(160), nullable=False, default=DEFAULT_SETTINGS['tagline'])
    welcome = db.Column(db.String(500), nullable=False, default=DEFAULT_SETTINGS['welcome'])
    registration_open = db.Column(db.Boolean, nullable=False, default=True)
    guest_posting_open = db.Column(db.Boolean, nullable=False, default=True)
    guest_post_limit = db.Column(db.Integer, nullable=False, default=10)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AdminAudit(db.Model):
    __tablename__ = 'admin_audit'
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    actor_name = db.Column(db.String(64), nullable=False)
    action = db.Column(db.String(40), nullable=False)
    target_type = db.Column(db.String(16), nullable=False)
    target_id = db.Column(db.Integer, nullable=True)
    target_label = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    @property
    def action_label(self):
        return {'post.hide': '隐藏文章', 'post.restore': '恢复文章',
                'comment.hide': '隐藏评论', 'comment.restore': '恢复评论',
                'user.update': '更新用户权限', 'settings.update': '更新站点设置',
                'user.grant_admin': '授予管理员权限', 'user.merge_site_owner': '合并站点作者身份'}.get(self.action, self.action)


class AdminConfirmation(db.Model):
    """Short-lived server receipts prevent replay of a copied signed cookie."""
    __tablename__ = 'admin_confirmations'
    nonce_hash = db.Column(db.String(64), primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    consumed = db.Column(db.Boolean, nullable=False, default=False)


def initialize_settings():
    """Called after create_all under the site's existing exclusive lock."""
    if db.session.get(SiteSettings, 1) is None:
        db.session.add(SiteSettings(id=1,
            guest_post_limit=current_app.config.get('PUBLIC_DEMO_MAX_POSTS', 10)))
        db.session.commit()


def settings_values():
    defaults = dict(DEFAULT_SETTINGS, guest_post_limit=current_app.config.get('PUBLIC_DEMO_MAX_POSTS', 10))
    if (not current_app.config.get('PUBLIC_DEMO')
            or not current_app.extensions['public_demo'].initialized):
        return defaults
    row = db.session.get(SiteSettings, 1)
    return {key: getattr(row, key) for key in SETTINGS_KEYS} if row else defaults


def is_console_admin():
    return (current_app.config.get('PUBLIC_DEMO')
            and current_user.is_authenticated and current_user.is_registered
            and not current_user.disabled and current_user.is_administrator()
            and current_app.extensions['public_demo'].participant())


@admin.before_request
def protect_console():
    if not is_console_admin():
        abort(403, description='此页面仅对站点管理员开放。')


def role_for(key):
    if key == 'normal':
        return Role.query.filter_by(default=True).first()
    permissions = 0xff if key == 'admin' else (Permission.FOLLOW | Permission.COMMENT |
        Permission.WRITE_ARTICLES | Permission.MODERATE_COMMENTS)
    return Role.query.filter_by(permissions=permissions).first()


def active_admin_count():
    return User.query.join(Role).filter(User.account_kind == 'account', User.disabled.is_(False),
        Role.permissions.op('&')(Permission.ADMINISTER) == Permission.ADMINISTER).count()


def record_audit(action, kind, target, before, after, cli=False):
    label = ('站点设置' if kind == 'settings' else
             f'{"文章" if kind == "post" else "评论"} #{target.id}' if kind in {'post', 'comment'}
             else f'@{target.username}')
    entry = AdminAudit(actor_id=None if cli else current_user.id,
        actor_name='服务器 CLI' if cli else current_user.display_name[:64],
        action=action, target_type=kind, target_id=None if kind == 'settings' else target.id,
        target_label=label[:100], details=json.dumps({'before': before, 'after': after}, ensure_ascii=False))
    db.session.add(entry)


def page_query(query):
    return query.paginate(page=max(1, min(1_000_000, request.args.get('page', 1, type=int))), per_page=20, error_out=False)


def search_term():
    return request.args.get('q', '').strip()[:100]


def search_like(value):
    return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


@admin.get('/', strict_slashes=False)
def index():
    stats = dict(posts=Post.query.count(), hidden_posts=Post.query.filter_by(hidden=True).count(),
        comments=Comment.query.count(), hidden_comments=Comment.query.filter_by(disabled=True).count(),
        users=User.query.filter(User.account_kind != 'system').count(),
        registered_users=User.query.filter_by(account_kind='account').count(),
        guests=User.query.filter_by(account_kind='guest').count(),
        disabled_users=User.query.filter_by(disabled=True, merged_into_id=None).count(), admins=active_admin_count())
    return render_template('admin/index.html', admin_section='index', stats=stats,
        recent_audit=AdminAudit.query.order_by(AdminAudit.id.desc()).limit(8).all())


@admin.get('/posts', strict_slashes=False)
def posts():
    q, status = search_term(), request.args.get('status', 'all')
    query = Post.query.join(User)
    if q:
        if re.fullmatch(r'#[0-9]{1,18}', q):
            query = query.filter(Post.id == int(q[1:]))
        else:
            pattern = search_like(q)
            query = query.filter(or_(Post.body.ilike(pattern, escape='\\'),
                User.username.ilike(pattern, escape='\\'), User.name.ilike(pattern, escape='\\')))
    if status in {'visible', 'hidden'}:
        query = query.filter(Post.hidden.is_(status == 'hidden'))
    return render_template('admin/posts.html', admin_section='posts', q=q, status=status,
        pagination=page_query(query.order_by(Post.timestamp.desc(), Post.id.desc())))


@admin.get('/comments', strict_slashes=False)
def comments():
    q, status = search_term(), request.args.get('status', 'all')
    query = Comment.query.join(User)
    if q:
        if re.fullmatch(r'#[0-9]{1,18}', q):
            query = query.filter(Comment.post_id == int(q[1:]))
        else:
            pattern = search_like(q)
            query = query.filter(or_(Comment.body.ilike(pattern, escape='\\'),
                User.username.ilike(pattern, escape='\\'), User.name.ilike(pattern, escape='\\')))
    if status in {'visible', 'hidden'}:
        query = query.filter(Comment.disabled.is_(status == 'hidden'))
    return render_template('admin/comments.html', admin_section='comments', q=q, status=status,
        pagination=page_query(query.order_by(Comment.timestamp.desc(), Comment.id.desc())))


@admin.get('/users', strict_slashes=False)
def users():
    q, status = search_term(), request.args.get('status', 'all')
    kind, role = request.args.get('kind', 'all'), request.args.get('role', 'all')
    query = User.query.filter(User.merged_into_id.is_(None))
    if q:
        if re.fullmatch(r'#[0-9]{1,18}', q):
            query = query.filter(User.id == int(q[1:]))
        else:
            pattern = search_like(q)
            query = query.filter(or_(User.username.ilike(pattern, escape='\\'), User.name.ilike(pattern, escape='\\')))
    if status in {'active', 'disabled'}:
        query = query.filter(User.disabled.is_(status == 'disabled'))
    if kind in {'account', 'guest', 'system'}:
        query = query.filter_by(account_kind=kind)
    elif kind == 'owner':
        query = query.filter_by(is_site_owner=True)
    if role in dict(ROLE_CHOICES):
        selected = role_for(role)
        query = query.filter_by(role_id=selected.id)
    return render_template('admin/users.html', admin_section='users', q=q, status=status,
        kind=kind, role=role, roles=ROLE_CHOICES,
        pagination=page_query(query.order_by(User.member_since.desc(), User.id.desc())))


@admin.get('/settings', strict_slashes=False)
def settings():
    return render_template('admin/settings.html', admin_section='settings', values=settings_values(), errors={})


@admin.get('/audit', strict_slashes=False)
def audit():
    return render_template('admin/audit.html', admin_section='audit',
        pagination=page_query(AdminAudit.query.order_by(AdminAudit.id.desc())))


def fail(message, status=400):
    abort(status, description=message)


def clean_return(value):
    allowed = {url_for('admin.' + endpoint) for endpoint in
               ('index', 'posts', 'comments', 'users', 'settings', 'audit')}
    if not isinstance(value, str) or len(value) > 2048 or '\\' in value or any(ord(c) < 32 for c in value):
        return url_for('admin.index')
    try:
        parsed = urlsplit(value)
    except ValueError:
        return url_for('admin.index')
    if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path.rstrip('/') not in {path.rstrip('/') for path in allowed}:
        return url_for('admin.index')
    return parsed.path + ('?' + parsed.query if parsed.query else '')


def state_of(kind, target):
    if kind in {'post', 'comment'}:
        return {'hidden': bool(target.hidden if kind == 'post' else target.disabled),
                'body_hash': hashlib.sha256((target.body or '').encode()).hexdigest()}
    if kind == 'user':
        return {'role_id': target.role_id, 'disabled': bool(target.disabled),
                'session_version': target.session_version}
    return settings_values()


def check_user_change(target, changes):
    if target.is_system:
        fail('系统作者账号由站点维护，不能在此修改。')
    if target.is_site_owner and (changes['role'] != 'admin' or changes['disabled']):
        fail('站点作者必须保持管理员身份和正常状态。个人资料可在账户菜单中修改。')
    if not target.is_registered and changes['role'] != 'normal':
        fail('访客身份不能被授予管理角色；请先自行注册为账号。')
    if target.id == current_user.id and changes['disabled']:
        fail('不能停用当前登录的管理员账号。')
    if (target.is_registered and not target.disabled and target.can(Permission.ADMINISTER)
            and (changes['disabled'] or changes['role'] != 'admin') and active_admin_count() <= 1):
        fail('站点必须保留至少一名有效管理员。')


def parse_settings(form):
    errors, values = {}, {}
    for key, label, limit in [('site_name', '站点名称', 60), ('tagline', '站点简介', 160), ('welcome', '欢迎语', 500)]:
        value = form.get(key, '').strip()
        values[key] = value
        if (key == 'site_name' and not value) or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
            errors[key] = f'{label}请控制在 {limit} 字以内。' if value else '请填写站点名称。'
    for key in ('registration_open', 'guest_posting_open'):
        value = form.get(key)
        if value not in {'0', '1'}:
            errors[key] = '请选择开启或关闭。'
        values[key] = value == '1'
    try:
        values['guest_post_limit'] = int(form.get('guest_post_limit', ''))
        if not 1 <= values['guest_post_limit'] <= 100:
            raise ValueError()
    except (ValueError, TypeError):
        errors['guest_post_limit'] = '访客文章上限须为 1 至 100 的整数。'
        values['guest_post_limit'] = form.get('guest_post_limit', '')
    return values, errors


def pretty_value(key, value):
    if key in {'hidden', 'disabled'}:
        return ('隐藏' if value else '公开') if key == 'hidden' else ('已停用' if value else '正常')
    if key in {'registration_open', 'guest_posting_open'}:
        return '开启' if value else '关闭'
    if key == 'role':
        return dict(ROLE_CHOICES).get(value, value)
    return str(value)


def operation_context(kind, target, changes):
    title = ('更新站点设置' if kind == 'settings' else '更新用户权限' if kind == 'user' else
             ('隐藏' if changes['hidden'] else '恢复') + ('文章' if kind == 'post' else '评论'))
    label = '站点设置' if kind == 'settings' else ('@' + target.username if kind == 'user' else
            ('文章' if kind == 'post' else '评论') + f' #{target.id}')
    before = (settings_values() if kind == 'settings' else
              {'role': target.admin_role_key, 'disabled': bool(target.disabled)} if kind == 'user' else
              {'hidden': bool(target.hidden if kind == 'post' else target.disabled)})
    labels = dict(site_name='站点名称', tagline='站点简介', welcome='欢迎语', registration_open='新账号注册',
        guest_posting_open='访客发文', guest_post_limit='访客文章上限', role='账号角色', disabled='账号状态', hidden='显示状态')
    rows = [{'label': labels[key], 'before': pretty_value(key, before[key]),
             'after': pretty_value(key, value)} for key, value in changes.items() if before[key] != value]
    return dict(title=title, target_label=label, changes=rows,
        description=('隐藏后，前台列表、链接与评论入口都将停止展示；原始内容仍保留，可以恢复。'
                     if kind == 'post' and changes['hidden'] else
                     '停用会立即撤销已有登录会话，账号和历史内容仍会保留。' if kind == 'user' and changes['disabled'] else
                     '请核对以下变更，确认后才会保存。'))


@admin.post('/preview')
def preview():
    kind, action = request.form.get('kind'), request.form.get('action')
    if kind not in {'post', 'comment', 'user', 'settings'}:
        fail('未知的管理操作。')
    if kind == 'settings':
        if action != 'update':
            fail('未知的设置操作。')
        target = db.session.get(SiteSettings, 1)
        changes, errors = parse_settings(request.form)
        if errors:
            return render_template('admin/settings.html', admin_section='settings', values=changes, errors=errors), 400
    else:
        try:
            target_id = int(request.form.get('target_id', ''))
            if not 0 < target_id <= 9_223_372_036_854_775_807:
                raise ValueError()
        except (TypeError, ValueError):
            fail('请选择有效的管理对象。')
        target = db.get_or_404({'post': Post, 'comment': Comment, 'user': User}[kind], target_id)
        if kind == 'user':
            role, disabled = request.form.get('role'), request.form.get('disabled')
            if action != 'update' or role not in dict(ROLE_CHOICES) or disabled not in {'0', '1'}:
                fail('请选择有效的用户角色和状态。')
            changes = dict(role=role, disabled=disabled == '1')
            check_user_change(target, changes)
        else:
            if action not in {'hide', 'restore'}:
                fail('请选择隐藏或恢复。')
            changes = dict(hidden=action == 'hide')
    operation = operation_context(kind, target, changes)
    if not operation['changes']:
        flash('没有需要保存的更改。')
        return redirect(clean_return(request.form.get('return_to')))
    nonce = secrets.token_urlsafe(24)
    session['admin_pending'] = nonce
    AdminConfirmation.query.filter(AdminConfirmation.created_at < utcnow() - timedelta(minutes=10)).delete()
    db.session.add(AdminConfirmation(nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
                                    actor_id=current_user.id))
    db.session.commit()
    payload = dict(uid=current_user.id, nonce=nonce, kind=kind,
                   target_id=None if kind == 'settings' else target.id,
                   changes=changes, before=state_of(kind, target),
                   return_to=clean_return(request.form.get('return_to')))
    token = URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='liss-admin-change').dumps(payload)
    return render_template('admin/confirm.html', admin_section='settings' if kind == 'settings' else kind + 's',
        operation=operation, token=token, return_to=payload['return_to'])


@admin.post('/apply')
def apply():
    try:
        payload = URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='liss-admin-change').loads(
            request.form.get('token', ''), max_age=600)
    except (BadData, ValueError, TypeError):
        fail('确认已过期或无效，请重新预览。')
    nonce = session.get('admin_pending')
    if (not isinstance(payload, dict) or payload.get('uid') != current_user.id
            or not isinstance(nonce, str) or not isinstance(payload.get('nonce'), str)
            or not hmac.compare_digest(nonce, payload['nonce'])):
        fail('确认已失效，请重新预览。')
    session.pop('admin_pending', None)
    receipt = db.session.get(AdminConfirmation, hashlib.sha256(nonce.encode()).hexdigest())
    if (receipt is None or receipt.consumed or receipt.actor_id != current_user.id
            or receipt.created_at < utcnow() - timedelta(minutes=10)):
        fail('确认已失效，请重新预览。')
    kind, changes = payload['kind'], payload['changes']
    target = (db.session.get(SiteSettings, 1) if kind == 'settings' else
              db.get_or_404({'post': Post, 'comment': Comment, 'user': User}[kind], payload['target_id']))
    if state_of(kind, target) != payload['before']:
        fail('内容已发生变化，请重新查看并确认。', 409)
    if kind == 'user':
        check_user_change(target, changes)
        before = dict(role=target.admin_role_key, disabled=bool(target.disabled))
        target.role = role_for(changes['role'])
        if bool(target.disabled) != changes['disabled']:
            target.session_version += 1
        target.disabled = changes['disabled']
        after, action = changes, 'update'
    elif kind == 'settings':
        before = settings_values()
        for key, value in changes.items():
            setattr(target, key, value)
        after, action = changes, 'update'
    else:
        attr = 'hidden' if kind == 'post' else 'disabled'
        before = dict(hidden=bool(getattr(target, attr)))
        setattr(target, attr, changes['hidden'])
        after, action = changes, 'hide' if changes['hidden'] else 'restore'
    record_audit(kind + '.' + action, kind, target, before, after)
    receipt.consumed = True
    db.session.commit()
    flash('更改已保存。')
    return redirect(clean_return(payload.get('return_to')))


def install(app):
    app.register_blueprint(admin)

    @app.context_processor
    def public_settings():
        return dict(site_settings=settings_values())

    @app.cli.command('grant-admin')
    @click.option('--username', type=str, help='Exact existing account username (case-sensitive).')
    @click.option('--user-id', type=int, help='Exact existing account ID.')
    def grant_admin(username, user_id):
        """Grant an existing registered account admin; never changes its password."""
        if (username is None) == (user_id is None):
            raise click.ClickException('Specify exactly one of --username or --user-id.')
        sandbox = app.extensions.get('public_demo')
        if sandbox is None:
            raise click.ClickException('Requires FLASK_CONFIG=public-demo.')
        with sandbox.exclusive():
            sandbox.prepare()
            target = (db.session.get(User, user_id) if user_id is not None else
                      User.query.filter(User.username == username).first())
            if target is None or not target.is_registered or target.disabled:
                raise click.ClickException('Account must exist, be registered, and be active. Nothing changed.')
            before = dict(role=target.admin_role_key, disabled=False)
            if target.is_administrator():
                click.echo(f'Account #{target.id} @{target.username} is already an administrator.')
                return
            target.role = role_for('admin')
            record_audit('user.grant_admin', 'user', target, before, dict(role='admin', disabled=False), cli=True)
            db.session.commit()
            click.echo(f'Administrator granted to account #{target.id} @{target.username}. Password unchanged.')
