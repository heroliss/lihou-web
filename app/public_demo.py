"""Persistent guest-first LISS site. Ordinary modes remain unchanged.

No request, age threshold or missing marker ever clears saved content. One WSGI
worker and a file lock coordinate writes, online snapshots and explicit restores.
"""
from collections import OrderedDict, deque
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import threading
import time
from sqlalchemy import text

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from flask_wtf import CSRFProtect, FlaskForm
from wtforms import SubmitField

from . import db
from .demo_seed import seed_sample_content
from .models import Role, User

demo = Blueprint('public_demo', __name__)


class EntryForm(FlaskForm):
    submit = SubmitField('以访客身份加入')


class Sandbox:
    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        self.epoch = None
        self.initialized = False
        self.rates = OrderedDict()

    @property
    def marker(self):
        return Path(self.app.config['PUBLIC_DEMO_DIR']) / 'epoch.json'

    @contextmanager
    def exclusive(self):
        """Coordinate the WSGI worker and explicit backup / restore commands."""
        with self.lock:
            with (self.marker.parent / '.sandbox.lock').open('a+b') as handle:
                if os.name == 'nt':
                    import msvcrt
                    if handle.seek(0, 2) == 0:
                        handle.write(b'\0')
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if os.name == 'nt':
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _assert_database(self):
        if not self.app.config.get('PUBLIC_DEMO'):
            raise RuntimeError('Visitor storage only supports its dedicated public-demo.sqlite.')
        expected = (Path(self.app.config['PUBLIC_DEMO_DIR']) / 'public-demo.sqlite').resolve()
        if (db.engine.url.get_backend_name() != 'sqlite'
                or Path(db.engine.url.database).resolve() != expected):
            raise RuntimeError('Visitor storage only supports its dedicated public-demo.sqlite.')

    def prepare(self):
        self._assert_database()
        if not self.initialized:
            from .public_schema import migrate_account_kinds
            migrate_account_kinds(db.engine.url.database)
            # create_all / sample seeding only add missing tables and samples.
            # Invalid databases fail instead of being deleted or recreated.
            seed_sample_content(password=secrets.token_urlsafe(48), public=True)
            db.session.execute(text('CREATE TABLE IF NOT EXISTS public_demo_state '
                                    '(id INTEGER PRIMARY KEY CHECK (id=1), '
                                    'identity_epoch TEXT NOT NULL, created_at REAL NOT NULL)'))
            if db.session.execute(text('SELECT identity_epoch FROM public_demo_state WHERE id=1')).first() is None:
                epoch = secrets.token_urlsafe(24)
                try:
                    legacy = json.loads(self.marker.read_text(encoding='utf-8'))
                    if isinstance(legacy, dict) and isinstance(legacy.get('epoch'), str) and legacy['epoch']:
                        epoch = legacy['epoch']
                except (OSError, ValueError, TypeError):
                    pass
                db.session.execute(text('INSERT INTO public_demo_state (id, identity_epoch, created_at) '
                                        'VALUES (1, :epoch, :created)'),
                                   {'epoch': epoch, 'created': time.time()})
            db.session.commit()
            self.initialized = True
        row = db.session.execute(text('SELECT identity_epoch FROM public_demo_state WHERE id=1')).first()
        if row is None:
            raise RuntimeError('Visitor identity metadata is missing; restore a verified backup.')
        if row[0] != self.epoch:
            self.rates.clear()
        self.epoch = row[0]

    def backup(self, output):
        from .public_demo_storage import backup_database
        self.prepare()
        db.session.remove()
        return backup_database(db.engine.url.database, output)

    def restore(self, source):
        from .public_demo_storage import inspect_backup, restore_database
        self._assert_database()
        source = inspect_backup(source)
        if source == Path(db.engine.url.database).resolve():
            raise ValueError('Restore input must differ from the live database.')
        safety = self.marker.parent / ('before-restore-' + time.strftime('%Y%m%d-%H%M%S')
                                      + '-' + secrets.token_hex(3) + '.sqlite')
        self.backup(safety)
        db.session.remove()
        db.engine.dispose()
        restore_database(source, db.engine.url.database)
        self.initialized = False
        self.prepare()
        return safety

    def check_storage(self):
        database = Path(db.engine.url.database)
        total = sum(path.stat().st_size for path in
                    (database, Path(str(database) + '-wal'), Path(str(database) + '-journal'))
                    if path.is_file())
        if total >= self.app.config['PUBLIC_DEMO_MAX_STORAGE_BYTES']:
            abort(503, description='站点已达到写入容量限制；已有内容仍保留，请联系站点维护者。')

    def limit(self, action):
        now = time.monotonic()
        key = (request.remote_addr or 'unknown', action)
        window = self.app.config['PUBLIC_DEMO_RATE_WINDOW']
        history = self.rates.pop(key, deque())
        while history and history[0] <= now - window:
            history.popleft()
        maximum = self.app.config[{'start': 'PUBLIC_DEMO_START_LIMIT',
                                   'auth': 'PUBLIC_DEMO_AUTH_LIMIT'}.get(action, 'PUBLIC_DEMO_WRITE_LIMIT')]
        self.rates[key] = history
        while len(self.rates) > 2048:
            self.rates.popitem(last=False)
        if len(history) >= maximum:
            g.public_demo_retry_after = window
            abort(429, description='操作有些频繁，请稍等一分钟再试。')
        history.append(now)

    @staticmethod
    def participant():
        return (current_user.is_authenticated
                and str(current_user.id) == session.get('public_demo_uid')
                and current_user.account_kind in {'guest', 'account'})

    visitor = participant  # Existing integrations use the historical helper name.

    def check_identity_capacity(self):
        if User.query.filter(User.account_kind != 'system').count() >= self.app.config['PUBLIC_DEMO_MAX_VISITORS']:
            abort(429, description='站点的身份容量暂时已满；已有账号仍可登录，已有内容会继续保留。')

    def quota(self, suffix):
        prefix = 'PUBLIC_ACCOUNT_' if current_user.is_registered else 'PUBLIC_DEMO_'
        return self.app.config[prefix + suffix]

    def before_request(self):
        if request.endpoint in {'static', 'bootstrap.static', 'pagedown.static'}:
            return
        lease = self.exclusive()
        lease.__enter__()
        g.public_demo_lease = lease
        self.prepare()
        if session.get('public_demo_epoch') != self.epoch:
            session.clear()
            session['public_demo_epoch'] = self.epoch
        endpoint = request.endpoint or ''
        if endpoint.startswith('api.'):
            abort(403, description='此站点暂不开放公共 API。')
        if endpoint.startswith('auth.') and endpoint not in {
                'auth.login', 'auth.register', 'auth.logout', 'auth.change_password'}:
            abort(403, description='此站点暂不提供邮件验证、邮箱找回或外部头像设置。')
        if endpoint == 'auth.change_password' and not (self.participant() and current_user.is_registered):
            abort(403, description='访客可先创建账号，再设置或更改登录密码。')
        if endpoint == 'auth.logout' and request.method != 'POST':
            abort(405, description='请通过站内按钮退出当前身份。')
        if endpoint in {'main.edit_profile_admin', 'main.moderate', 'main.moderate_enable',
                        'main.moderate_disable', 'main.server_shutdown'}:
            abort(403, description='此站点不开放公共管理操作。')
        if endpoint in {'main.follow', 'main.unfollow'} and request.method != 'POST':
            abort(405, description='请通过页面按钮提交关注操作。')
        if request.method in {'GET', 'HEAD', 'OPTIONS'}:
            return
        if endpoint == 'public_demo.entry':
            self.limit('start')
            self.check_storage()
            return
        if endpoint in {'auth.login', 'auth.register', 'public_demo.join'}:
            self.limit('auth')
            if endpoint != 'auth.login':
                self.check_storage()
            return
        if endpoint == 'public_demo.legacy_entry':
            return
        if endpoint == 'auth.change_password':
            self.limit('auth')
            return
        if endpoint == 'auth.logout':
            return  # CSRFProtect validates the POST before the auth view runs.
        allowed = {'main.index', 'main.post', 'main.edit', 'main.edit_profile',
                   'main.follow', 'main.unfollow'}
        if endpoint not in allowed or not self.participant():
            abort(403, description='请先以访客身份加入，或登录已有账号。')
        self.limit('write')
        self.check_storage()
        if endpoint in {'main.index', 'main.edit', 'main.post'}:
            maximum = self.quota('MAX_COMMENT_BODY' if endpoint == 'main.post' else 'MAX_BODY')
            if len(request.form.get('body', '')) > maximum:
                abort(413, description=f'内容请控制在 {maximum} 字以内。')
        if endpoint == 'main.edit_profile' and len(request.form.get('about_me', '')) > self.quota('MAX_PROFILE_BODY'):
            abort(413, description=f'个人简介请控制在 {self.quota("MAX_PROFILE_BODY")} 字以内。')
        if endpoint == 'main.index' and current_user.posts.count() >= self.quota('MAX_POSTS'):
            abort(429, description=f'当前身份最多发布 {self.quota("MAX_POSTS")} 篇文章，可继续编辑已有文章。')
        if endpoint == 'main.post' and current_user.comments.count() >= self.quota('MAX_COMMENTS'):
            abort(429, description='当前身份的评论数量已达上限，已有评论会继续保留。')
        if endpoint == 'main.follow' and current_user.followed.count() >= self.quota('MAX_FOLLOWS') + 1:
            abort(429, description=f'当前身份最多关注 {self.quota("MAX_FOLLOWS")} 位作者。')

    def release(self, error=None):
        lease = g.pop('public_demo_lease', None)
        if lease is not None:
            try:
                db.session.remove()
            finally:
                lease.__exit__(None, None, None)


@demo.route('/welcome', methods=['GET', 'POST'])
def entry():
    sandbox = current_app.extensions['public_demo']
    if sandbox.visitor():
        return redirect(url_for('main.index'))
    form = EntryForm()
    if form.validate_on_submit():
        sandbox.check_identity_capacity()
        token = secrets.token_hex(8)
        while User.query.filter_by(username=f'guest_{token[:6]}').first() is not None:
            token = secrets.token_hex(8)
        user = User(email=None, username=f'guest_{token[:6]}', account_kind='guest',
                    password=secrets.token_urlsafe(48), confirmed=True,
                    name='访客', location='', about_me='',
                    role=Role.query.filter_by(default=True).first())
        db.session.add(user)
        db.session.commit()
        from .public_accounts import establish_identity
        establish_identity(user)
        return redirect(url_for('main.index'))
    return render_template('public_demo/entry.html', form=form)


@demo.route('/demo', methods=['GET', 'POST'])
def legacy_entry():
    return redirect(url_for('public_demo.entry'), code=307 if request.method == 'POST' else 302)


@demo.route('/join', methods=['GET', 'POST'])
def join():
    from .public_accounts import register
    return register()


@demo.get('/healthz')
def health():
    db.session.execute(db.select(Role.id).limit(1)).first()
    return jsonify(status='ok')


def install(app):
    sandbox = Sandbox(app)
    app.extensions['public_demo'] = sandbox
    app.before_request(sandbox.before_request)
    app.teardown_request(sandbox.release)
    CSRFProtect(app)
    app.register_blueprint(demo)

    @app.after_request
    def demo_headers(response):
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if response.mimetype == 'text/html':
            response.headers['Cache-Control'] = 'no-store'
        if getattr(g, 'public_demo_retry_after', None) is not None:
            response.headers['Retry-After'] = str(g.public_demo_retry_after)
        return response

    @app.errorhandler(413)
    @app.errorhandler(429)
    @app.errorhandler(503)
    def limited(error):
        return render_template('public_demo/message.html', message=error.description), error.code
