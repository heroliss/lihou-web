"""Actual visitor routes, durable content, isolation and consistent backups."""
import html
from contextlib import closing
import json
from pathlib import Path
import re
import runpy
import tempfile
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from app import create_app, db
from app.models import Comment, Post, User
from app.admin import SiteSettings


class PublicDemoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict('os.environ', {
            'SECRET_KEY': 'test-only-secret-' * 4,
            'LIHOU_DEMO_DIR': self.directory.name,
            'LIHOU_PROXY_HOPS': '0',
            'LIHOU_DEMO_RESET_SECONDS': '86400',
        })
        self.environment.start()
        self.app = self.create_app()
        self.client = self.app.test_client()

    def create_app(self):
        app = create_app('public-demo')
        app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        return app

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.environment.stop()
        self.directory.cleanup()

    def token(self, client=None, path='/'):
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return html.unescape(match.group(1))

    def start(self, client=None):
        client = client or self.client
        response = client.post('/welcome', data={'csrf_token': self.token(client, '/welcome')})
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as saved:
            return int(saved['public_demo_uid'])

    def submit(self, path, data=None, client=None, token_path='/'):
        client = client or self.client
        return client.post(path, data={**(data or {}), 'csrf_token': self.token(client, token_path)})

    def test_no_secret_fallback_for_public_mode(self):
        with patch.dict('os.environ', {'SECRET_KEY': ''}):
            with self.assertRaisesRegex(RuntimeError, 'SECRET_KEY'):
                create_app('public-demo')

    def test_fresh_home_has_welcome_and_optional_account_entry(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('LISS', page)
        # An old commented-out block can contain the historical URL; only visible HTML matters.
        visible = re.sub(r'<!--.*?-->', '', page, flags=re.S)
        self.assertIn('/welcome', visible)
        self.assertIn('/auth/login', visible)
        self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, nofollow')
        with self.app.app_context():
            self.assertEqual(Post.query.count(), 3)
            self.assertFalse(User.query.filter_by(username='demo').first().is_administrator())

    def test_entry_requires_csrf_and_creates_distinct_normal_users(self):
        self.client.get('/welcome')
        self.assertEqual(self.client.post('/welcome').status_code, 400)
        first = self.start()
        second = self.start(self.app.test_client())
        self.assertNotEqual(first, second)
        with self.app.app_context():
            user = db.session.get(User, first)
            self.assertTrue(user.confirmed)
            self.assertFalse(user.is_administrator())
            self.assertTrue(user.username.startswith('guest_'))

    def test_real_post_comment_profile_and_markdown_persist(self):
        uid = self.start()
        self.assertEqual(self.submit('/', {'body': '# A public test\n\n**bold**'}).status_code, 302)
        self.assertEqual(self.submit('/post/1', {'body': 'Demo reply'}).status_code, 302)
        self.assertEqual(self.submit('/edit-profile', {'name': 'Visitor', 'location': 'Demo',
                                                      'about_me': 'Temporary profile'}).status_code, 302)
        with self.app.app_context():
            user = db.session.get(User, uid)
            self.assertEqual(user.name, 'Visitor')
            self.assertIn('<strong>bold</strong>', user.posts.first().body_html)
            self.assertEqual(user.comments.first().body, 'Demo reply')

    def test_fixed_and_other_visitors_articles_cannot_be_edited(self):
        uid = self.start()
        self.assertEqual(self.submit('/', {'body': 'My article'}).status_code, 302)
        with self.app.app_context():
            post_id = Post.query.filter_by(author_id=uid).first().id
        other = self.app.test_client()
        self.start(other)
        self.assertEqual(self.submit('/edit/1', {'body': 'Overwrite sample'}).status_code, 403)
        self.assertEqual(self.submit(f'/edit/{post_id}', {'body': 'Overwrite visitor'}, client=other).status_code, 403)
        self.assertEqual(self.submit(f'/edit/{post_id}', {'body': 'Updated own article'}).status_code, 302)

    def test_account_admin_and_api_routes_reject_real_requests(self):
        self.start()
        blocked = ['/auth/change-password', '/edit-profile/1', '/moderate',
                   '/moderate/disable/1', '/moderate/enable/1', '/api/v1.0/posts/']
        for path in blocked:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
                self.assertEqual(self.client.post(path, data={'body': 'blocked'}).status_code, 403)
        self.assertEqual(self.client.put('/api/v1.0/posts/1', json={'body': 'blocked'}).status_code, 403)
        self.assertEqual(self.client.get('/auth/login').status_code, 200)
        self.assertEqual(self.client.get('/auth/register').status_code, 200)
        self.assertEqual(self.client.get('/auth/reset').status_code, 200)
        self.assertEqual(self.client.get('/auth/change-email').status_code, 302)
        self.assertEqual(self.client.get('/auth/reset/anything').status_code, 302)
        self.assertEqual(self.client.get('/auth/confirm').status_code, 405)
        for path in ['/auth/change-email', '/auth/reset', '/auth/reset/anything', '/auth/confirm']:
            self.assertEqual(self.client.post(path, data={'email': 'reader@example.com'}).status_code, 400)
        self.assertEqual(self.client.post('/auth/login', data={'username': 'demo',
                                                             'password': 'local-demo-only'}).status_code, 400)

    def test_follow_is_post_and_csrf_protected(self):
        uid = self.start()
        self.assertEqual(self.client.get('/follow/demo').status_code, 405)
        self.assertEqual(self.client.post('/follow/demo').status_code, 400)
        self.assertEqual(self.submit('/follow/demo').status_code, 302)
        with self.app.app_context():
            self.assertTrue(db.session.get(User, uid).is_following(User.query.filter_by(username='demo').first()))
        self.assertEqual(self.submit('/unfollow/demo').status_code, 302)

    def test_article_and_comment_quotas_and_body_limits(self):
        self.start()
        with self.app.app_context():
            db.session.get(SiteSettings, 1).guest_post_limit = 1
            db.session.commit()
        self.app.config['PUBLIC_DEMO_MAX_COMMENTS'] = 1
        self.assertEqual(self.submit('/', {'body': 'One article'}).status_code, 302)
        self.assertEqual(self.submit('/', {'body': 'Another article'}).status_code, 429)
        self.assertEqual(self.submit('/post/1', {'body': 'One comment'}).status_code, 302)
        self.assertEqual(self.submit('/post/1', {'body': 'Another comment'}).status_code, 429)
        self.assertEqual(self.submit('/', {'body': 'x' * 8001}).status_code, 413)
        self.assertEqual(self.submit('/post/1', {'body': 'x' * 2001}).status_code, 413)
        self.assertEqual(self.submit('/edit-profile', {'about_me': 'x' * 501}).status_code, 413)

    def test_guest_default_article_limit_is_shared_and_upgrade_immediately_removes_it(self):
        uid = self.start()
        self.assertEqual(self.app.config['PUBLIC_DEMO_MAX_POSTS'], 10)
        target = '/projects/outpost/talk/'
        for index in range(10):
            path = '/' if index % 2 == 0 else target
            response = self.submit(path, {'body': f'Guest article {index + 1}'}, token_path=path)
            self.assertEqual(response.status_code, 302)
        for path in ('/', target):
            response = self.submit(path, {'body': 'Must wait for account'}, token_path=path)
            self.assertEqual(response.status_code, 429)
            self.assertIn('访客最多发布 10 篇', response.get_data(as_text=True))
            self.assertIn('创建账号后不限篇数', response.get_data(as_text=True))
        with self.app.app_context():
            user = db.session.get(User, uid)
            self.assertEqual(user.posts.count(), 10)
            post_id = user.posts.first().id
            original_ids = {post.id for post in user.posts}
        # The article ceiling applies only to creating articles, not existing work or replies.
        self.assertEqual(self.submit(f'/edit/{post_id}', {'body': 'Edited at the article limit'}).status_code, 302)
        self.assertEqual(self.submit('/post/1', {'body': 'A reply at the article limit'}).status_code, 302)
        self.assertEqual(self.client.get(target).status_code, 200)
        self.assertEqual(self.register().status_code, 302)
        with self.client.session_transaction() as saved:
            self.assertEqual(saved['public_demo_uid'], str(uid))
        for path in ('/', target):
            self.assertEqual(self.submit(path, {'body': 'Account can continue'}, token_path=path).status_code, 302)
        with self.app.app_context():
            user = db.session.get(User, uid)
            self.assertTrue(user.is_registered)
            self.assertEqual(user.posts.count(), 12)
            self.assertTrue(original_ids.issubset({post.id for post in user.posts}))

    def test_account_articles_are_unlimited_while_body_rate_and_storage_limits_remain(self):
        self.assertIsNone(self.app.config['PUBLIC_ACCOUNT_MAX_POSTS'])
        self.assertEqual(self.register().status_code, 302)
        with self.client.session_transaction() as saved:
            uid = int(saved['public_demo_uid'])
        with self.app.app_context():
            # Exceed the previous 1,000-article ceiling without making 1,001 web writes.
            db.session.add_all(Post(author_id=uid, body=f'Existing account article {index}')
                               for index in range(1001))
            db.session.commit()
        for path in ('/', '/projects/outpost/talk/'):
            self.assertEqual(self.submit(path, {'body': 'Beyond the old account ceiling'}, token_path=path).status_code, 302)
        self.assertEqual(self.submit('/', {'body': 'x' * 20001}).status_code, 413)
        self.app.config['PUBLIC_DEMO_WRITE_LIMIT'] = 3
        response = self.submit('/', {'body': 'Rate limit still applies'})
        self.assertEqual(response.status_code, 429)
        self.assertIn('操作有些频繁', response.get_data(as_text=True))
        self.app.config['PUBLIC_DEMO_WRITE_LIMIT'] = 60
        self.app.config['PUBLIC_DEMO_MAX_STORAGE_BYTES'] = 1
        self.assertEqual(self.submit('/', {'body': 'Storage limit still applies'}).status_code, 503)
        with self.app.app_context():
            self.assertEqual(db.session.get(User, uid).posts.count(), 1003)

    def test_write_rate_limit_and_retry_after(self):
        self.start()
        self.app.config['PUBLIC_DEMO_WRITE_LIMIT'] = 1
        self.assertEqual(self.submit('/', {'body': 'First'}).status_code, 302)
        response = self.submit('/', {'body': 'Too soon'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '60')
        with self.app.app_context():
            self.assertEqual(Post.query.count(), 4)

    def test_visitor_capacity_is_bounded(self):
        self.app.config['PUBLIC_DEMO_MAX_VISITORS'] = 1
        self.start()
        other = self.app.test_client()
        response = other.post('/welcome', data={'csrf_token': self.token(other, '/welcome')})
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('重置', response.get_data(as_text=True))
        self.assertNotIn('Retry-After', response.headers)
        self.app.config['PUBLIC_DEMO_MAX_VISITORS'] = 2
        self.start(other)  # Raising the configured limit does not clear users.
        with self.app.app_context():
            self.assertEqual(User.query.count(), 3)

    def test_old_ttl_and_missing_marker_never_remove_saved_content(self):
        uid = self.start()
        self.assertEqual(self.submit('/', {'body': 'Kept beyond old TTL'}).status_code, 302)
        self.assertEqual(self.submit('/post/1', {'body': 'Kept comment'}).status_code, 302)
        self.assertEqual(self.submit('/follow/demo').status_code, 302)
        sandbox = self.app.extensions['public_demo']
        sandbox.marker.write_text(json.dumps({'epoch': sandbox.epoch, 'started': 1}), encoding='utf-8')
        with self.app.app_context():
            db.session.execute(db.text('UPDATE public_demo_state SET created_at=1'))
            db.session.commit()
        self.assertIn('Kept beyond old TTL', self.client.get('/').get_data(as_text=True))
        sandbox.marker.unlink()
        self.assertEqual(self.client.get('/').status_code, 200)
        with self.client.session_transaction() as saved:
            self.assertEqual(saved['_user_id'], str(uid))
        with self.app.app_context():
            user = db.session.get(User, uid)
            self.assertEqual(user.posts.count(), 1)
            self.assertEqual(user.comments.first().body, 'Kept comment')
            self.assertTrue(user.is_following(User.query.filter_by(username='demo').first()))

    def test_restart_keeps_identity_posts_comments_profile_and_follows(self):
        uid = self.start()
        self.assertEqual(self.submit('/', {'body': 'Persistent article'}).status_code, 302)
        self.assertEqual(self.submit('/post/1', {'body': 'Persistent reply'}).status_code, 302)
        self.assertEqual(self.submit('/edit-profile', {'name': 'Persistent name'}).status_code, 302)
        self.assertEqual(self.submit('/follow/demo').status_code, 302)
        cookie = self.client.get_cookie('lihou_demo')
        another_app = self.create_app()
        client = another_app.test_client()
        client.set_cookie('lihou_demo', cookie.value)
        self.assertEqual(client.get('/').status_code, 200)
        with client.session_transaction() as saved:
            self.assertEqual(saved.get('_user_id'), str(uid))
        with another_app.app_context():
            user = db.session.get(User, uid)
            self.assertEqual(user.posts.first().body, 'Persistent article')
            self.assertEqual(user.comments.first().body, 'Persistent reply')
            self.assertEqual(user.name, 'Persistent name')
            self.assertTrue(user.is_following(User.query.filter_by(username='demo').first()))
            db.session.remove()
            db.engine.dispose()

    def test_cli_backup_restore_and_removed_reset_command(self):
        self.start()
        self.assertEqual(self.submit('/', {'body': 'Before snapshot'}).status_code, 302)
        self.assertEqual(self.submit('/post/1', {'body': 'Snapshot comment'}).status_code, 302)
        with patch.dict('os.environ', {'FLASK_CONFIG': 'public-demo'}):
            namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'manage.py'),
                                      run_name='public_demo_cli_probe')
        command_app = namespace['app']
        try:
            runner = command_app.test_cli_runner()
            backup = Path(self.directory.name) / 'snapshots' / 'content.sqlite'
            self.assertEqual(runner.invoke(args=['backup-public-demo', '--output', str(backup)]).exit_code, 0)
            self.assertNotEqual(runner.invoke(args=['backup-public-demo', '--output', str(backup)]).exit_code, 0)
            self.assertEqual(self.submit('/', {'body': 'After snapshot'}).status_code, 302)
            self.assertNotEqual(runner.invoke(args=['reset-public-demo', '--yes']).exit_code, 0)
            self.assertNotEqual(runner.invoke(args=['restore-public-demo', '--input', str(backup)]).exit_code, 0)
            with self.app.app_context():
                self.assertIsNotNone(Post.query.filter_by(body='After snapshot').first())
            self.assertEqual(runner.invoke(args=['restore-public-demo', '--input', str(backup), '--yes']).exit_code, 0)
            self.assertEqual(self.client.get('/').status_code, 200)
            with self.client.session_transaction() as saved:
                self.assertNotIn('_user_id', saved)
            with self.app.app_context():
                self.assertIsNotNone(Post.query.filter_by(body='Before snapshot').first())
                self.assertIsNone(Post.query.filter_by(body='After snapshot').first())
                self.assertIsNotNone(Comment.query.filter_by(body='Snapshot comment').first())
            safety = list(Path(self.directory.name).glob('before-restore-*.sqlite'))
            self.assertEqual(len(safety), 1)
            with closing(sqlite3.connect(safety[0])) as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM posts WHERE body=?',
                                                    ('After snapshot',)).fetchone()[0], 1)
        finally:
            with command_app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_storage_refuses_another_database_and_preserves_other_files(self):
        self.client.get('/')
        sentinel = Path(self.directory.name) / 'production.sqlite'
        sentinel.write_bytes(b'not-the-demo-database')
        sandbox = self.app.extensions['public_demo']
        with self.app.app_context(), patch.object(sandbox, 'app', create_app('testing')):
            with self.assertRaisesRegex(RuntimeError, 'dedicated'):
                sandbox.prepare()
        self.assertEqual(sentinel.read_bytes(), b'not-the-demo-database')

    def test_cookie_expiration_does_not_delete_the_visitor_or_content(self):
        uid = self.start()
        self.assertEqual(self.submit('/', {'body': 'Content outlives identity cookie'}).status_code, 302)
        self.assertEqual(self.app.permanent_session_lifetime.days, 30)
        self.client.delete_cookie('lihou_demo')
        self.assertIn('Content outlives identity cookie', self.client.get('/').get_data(as_text=True))
        new_uid = self.start()
        self.assertNotEqual(uid, new_uid)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(User, uid))
            self.assertEqual(Post.query.filter_by(author_id=uid).count(), 1)

    def test_storage_capacity_stops_new_writes_without_removing_saved_data(self):
        uid = self.start()
        self.assertEqual(self.submit('/', {'body': 'Capacity does not erase this'}).status_code, 302)
        self.app.config['PUBLIC_DEMO_MAX_STORAGE_BYTES'] = 1
        response = self.submit('/', {'body': 'No capacity'})
        self.assertEqual(response.status_code, 503)
        self.assertIn('Capacity does not erase this', self.client.get('/').get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(db.session.get(User, uid).posts.count(), 1)

    def test_upgrade_preserves_old_epoch_and_content_beyond_the_old_ttl(self):
        uid = self.start()
        self.assertEqual(self.submit('/', {'body': 'Legacy content must survive upgrade'}).status_code, 302)
        sandbox = self.app.extensions['public_demo']
        epoch = sandbox.epoch
        with self.app.app_context():
            db.session.execute(db.text('DROP TABLE public_demo_state'))
            db.session.commit()
        sandbox.initialized = False
        sandbox.marker.write_text(json.dumps({'epoch': epoch, 'started': 1}), encoding='utf-8')
        self.assertIn('Legacy content must survive upgrade', self.client.get('/').get_data(as_text=True))
        with self.client.session_transaction() as saved:
            self.assertEqual(saved['_user_id'], str(uid))
            self.assertEqual(saved['public_demo_epoch'], epoch)

    def test_sqlite_backup_includes_committed_wal_and_rejects_overwrite(self):
        self.client.get('/')
        sandbox = self.app.extensions['public_demo']
        snapshot = Path(self.directory.name) / 'wal-backup.sqlite'
        database = Path(self.directory.name) / 'public-demo.sqlite'
        with closing(sqlite3.connect(database)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('UPDATE posts SET body=? WHERE id=1', ('Committed WAL content',))
            writer.commit()
            self.assertTrue(Path(str(database) + '-wal').is_file())
            with self.app.app_context(), sandbox.exclusive():
                sandbox.backup(snapshot)
                with self.assertRaises(FileExistsError):
                    sandbox.backup(snapshot)
                with self.assertRaises(ValueError):
                    sandbox.backup(database)
            with closing(sqlite3.connect(snapshot)) as saved:
                self.assertEqual(saved.execute('SELECT body FROM posts WHERE id=1').fetchone()[0],
                                 'Committed WAL content')

    def test_invalid_restore_never_replaces_existing_data(self):
        self.start()
        self.assertEqual(self.submit('/', {'body': 'Keep if invalid restore'}).status_code, 302)
        invalid = Path(self.directory.name) / 'not-a-visitor-db.sqlite'
        with closing(sqlite3.connect(invalid)) as connection:
            connection.execute('CREATE TABLE unrelated (id INTEGER)')
            connection.commit()
        sandbox = self.app.extensions['public_demo']
        with self.app.app_context(), sandbox.exclusive():
            with self.assertRaises(ValueError):
                sandbox.restore(invalid)
        with self.app.app_context():
            self.assertIsNotNone(Post.query.filter_by(body='Keep if invalid restore').first())

    def test_platform_admin_helper_loads_fixed_private_key_and_correct_storage(self):
        self.start()
        self.assertEqual(self.submit('/', {'body': 'CLI helper must find this database'}).status_code, 302)
        private = Path(self.directory.name) / 'private-settings.json'
        private.write_text(json.dumps({'SECRET_KEY': 'test-only-secret-' * 4}), encoding='utf-8')
        output = Path(self.directory.name) / 'helper-backup.sqlite'
        completed = subprocess.run([sys.executable, 'deploy/manage_public.py', '--settings', str(private),
                                    '--data-dir', self.directory.name, 'backup-public-demo', '--output', str(output)],
                                   cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                   text=True, encoding='utf-8', timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn('test-only-secret-', completed.stdout + completed.stderr)
        with closing(sqlite3.connect(output)) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM posts WHERE body=?',
                                                ('CLI helper must find this database',)).fetchone()[0], 1)

    def test_payload_size_and_health(self):
        self.assertEqual(self.client.get('/healthz').get_json(), {'status': 'ok'})
        self.start()
        response = self.client.post('/', data={'body': 'x' * (257 * 1024)})
        self.assertEqual(response.status_code, 413)

    def register(self, username='writer_one', client=None, nickname=''):
        return self.submit('/auth/register', {'username': username, 'password': 'Good-password-123',
                            'password2': 'Good-password-123', 'nickname': nickname},
                           client=client, token_path='/auth/register')

    def login(self, client, username='writer_one', password='Good-password-123'):
        return self.submit('/auth/login', {'username': username, 'password': password},
                           client=client, token_path='/auth/login')

    def test_guest_upgrade_keeps_id_content_profile_comments_and_follow_relations(self):
        uid = self.start()
        self.submit('/', {'body': 'Written before account upgrade'})
        self.submit('/post/1', {'body': 'Reply before upgrade'})
        self.submit('/edit-profile', {'name': 'Existing nickname', 'location': 'Somewhere', 'about_me': 'Keep profile'})
        self.submit('/follow/demo')
        with self.app.app_context():
            before = {table: db.session.execute(db.text(f'SELECT count(*) FROM {table}')).scalar()
                      for table in ['users', 'posts', 'comments', 'follows']}
            post_id = Post.query.filter_by(author_id=uid).first().id
        self.app.config['PUBLIC_DEMO_MAX_VISITORS'] = 1  # Upgrade is not a new identity.
        with patch('app.auth.views.send_email', side_effect=AssertionError('SMTP must not be used')):
            self.assertEqual(self.register().status_code, 302)
        with self.client.session_transaction() as saved:
            self.assertEqual(saved['public_demo_uid'], str(uid))
        with self.app.app_context():
            user = db.session.get(User, uid)
            self.assertEqual(user.account_kind, 'account')
            self.assertTrue(user.is_registered)
            self.assertFalse(user.is_visitor)
            self.assertFalse(user.is_administrator())
            self.assertIsNone(user.email)
            self.assertTrue(user.confirmed)
            self.assertEqual(user.display_name, 'Existing nickname')
            self.assertEqual(user.location, 'Somewhere')
            self.assertEqual(user.about_me, 'Keep profile')
            self.assertEqual(user.posts.first().id, post_id)
            self.assertEqual(user.comments.first().body, 'Reply before upgrade')
            self.assertTrue(user.is_following(User.query.filter_by(username='demo').first()))
            after = {table: db.session.execute(db.text(f'SELECT count(*) FROM {table}')).scalar()
                     for table in before}
            self.assertEqual(before, after)
        another = self.app.test_client()
        self.assertEqual(self.login(another, 'WRITER_ONE').status_code, 302)
        with another.session_transaction() as saved:
            self.assertEqual(saved['public_demo_uid'], str(uid))
        self.assertEqual(self.submit(f'/edit/{post_id}', {'body': 'Edited on another browser'}, client=another).status_code, 302)
        self.assertEqual(self.submit('/edit/1', {'body': 'Cannot change guide'}, client=another).status_code, 403)

    def test_public_registration_login_csrf_and_reserved_names_without_email(self):
        self.assertEqual(self.client.post('/auth/register', data={'username': 'writer_one'}).status_code, 400)
        with patch('app.auth.views.send_email', side_effect=AssertionError('SMTP must not be used')):
            self.assertEqual(self.register(nickname='A writer').status_code, 302)
        with self.app.app_context():
            user = User.query.filter_by(username='writer_one').first()
            self.assertEqual(user.display_name, 'A writer')
            self.assertIsNone(user.email)
            self.assertFalse(user.is_administrator())
            uid = user.id
        other = self.app.test_client()
        for reserved in ['Writer_One', 'guest_123abc', 'demo', 'admin', 'liss']:
            self.assertEqual(self.register(reserved, client=other).status_code, 200)
        with self.app.app_context():
            self.assertEqual(User.query.count(), 2)
        self.assertEqual(other.post('/auth/login', data={'username': 'writer_one', 'password': 'Good-password-123'}).status_code, 400)
        self.assertEqual(self.login(other, password='Wrong-password').status_code, 200)
        with other.session_transaction() as saved:
            self.assertNotIn('_user_id', saved)
        self.assertEqual(self.login(other).status_code, 302)
        with other.session_transaction() as saved:
            self.assertEqual(saved['_user_id'], str(uid))

    def test_account_login_switch_does_not_reassign_visitor_content(self):
        self.register()
        visitor = self.app.test_client()
        uid = self.start(visitor)
        self.submit('/', {'body': 'Keep original visitor owner'}, client=visitor)
        self.assertEqual(self.login(visitor).status_code, 302)
        with self.app.app_context():
            post = Post.query.filter_by(body='Keep original visitor owner').first()
            self.assertEqual(post.author_id, uid)
            self.assertTrue(db.session.get(User, uid).is_visitor)
            post_id = post.id
        self.assertEqual(self.submit(f'/edit/{post_id}', {'body': 'Cannot take visitor content'}, client=visitor).status_code, 403)

    def test_account_password_change_works_and_guest_cannot_use_it(self):
        self.start()
        self.assertEqual(self.client.get('/auth/change-password').status_code, 403)
        self.register()
        self.assertEqual(self.client.post('/auth/change-password', data={'password': 'No-CSRF-password'}).status_code, 400)
        self.assertEqual(self.submit('/auth/change-password', {
            'old_password': 'Good-password-123', 'password': 'Changed-password-456',
            'password2': 'Changed-password-456'}, token_path='/auth/change-password').status_code, 302)
        other = self.app.test_client()
        self.assertEqual(self.login(other).status_code, 200)
        self.assertEqual(self.login(other, password='Changed-password-456').status_code, 302)

    def test_guest_and_account_have_ordinary_permissions_with_different_quotas(self):
        uid = self.start()
        with self.app.app_context():
            db.session.get(SiteSettings, 1).guest_post_limit = 1
            db.session.commit()
        self.submit('/', {'body': 'First visitor post'})
        self.assertEqual(self.submit('/', {'body': 'Visitor limit'}).status_code, 429)
        self.register()
        self.assertEqual(self.submit('/', {'body': 'Account can continue writing'}).status_code, 302)
        with self.app.app_context():
            user = db.session.get(User, uid)
            user.username = 'guest_account'  # Prefixes do not determine capabilities.
            db.session.commit()
        self.assertEqual(self.submit('/', {'body': 'Persisted kind controls access'}).status_code, 302)
        for path in ['/api/v1.0/posts/', '/moderate', '/edit-profile/1']:
            self.assertEqual(self.client.get(path).status_code, 403)
        for path in ['/auth/reset', '/auth/change-email']:
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertIn('尚未开通' if path == '/auth/reset' else '暂未开通', page.get_data(as_text=True))

    def test_legacy_schema_upgrade_preserves_old_cookie_ids_and_custom_content(self):
        uid = self.start()
        self.submit('/', {'body': 'Legacy visitor content'})
        self.submit('/post/1', {'body': 'Legacy visitor reply'})
        self.submit('/follow/demo')
        from app.demo_seed import LEGACY_POSTS, WELCOME_POSTS
        database = Path(self.directory.name) / 'public-demo.sqlite'
        with self.app.app_context():
            user = db.session.get(User, uid)
            user.email = 'abcdef0123456789@example.invalid'
            db.session.get(Post, 1).body = LEGACY_POSTS[0]
            db.session.get(Post, 2).body = 'Customized original seed body must survive'
            db.session.commit()
            before = {table: db.session.execute(db.text(f'SELECT count(*) FROM {table}')).scalar()
                      for table in ['users', 'posts', 'comments', 'follows']}
            db.session.remove()
            db.engine.dispose()
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('ALTER TABLE users DROP COLUMN account_kind')
            connection.commit()
        self.app.extensions['public_demo'].initialized = False
        self.assertEqual(self.client.get('/').status_code, 200)
        with self.client.session_transaction() as saved:
            self.assertEqual(saved['_user_id'], str(uid))
        self.assertEqual(self.submit('/', {'body': 'Old cookie can still write'}).status_code, 302)
        with self.app.app_context():
            user = db.session.get(User, uid)
            self.assertTrue(user.is_visitor)
            self.assertEqual(user.comments.first().body, 'Legacy visitor reply')
            self.assertTrue(user.is_following(User.query.filter_by(username='demo').first()))
            self.assertEqual(db.session.get(Post, 1).body, WELCOME_POSTS[0])
            self.assertEqual(db.session.get(Post, 2).body, 'Customized original seed body must survive')
            after = {table: db.session.execute(db.text(f'SELECT count(*) FROM {table}')).scalar()
                     for table in before}
            self.assertEqual(after, {**before, 'posts': before['posts'] + 1})
        snapshots = list(Path(self.directory.name).glob('before-accounts-*.sqlite'))
        self.assertEqual(len(snapshots), 1)
        with closing(sqlite3.connect(snapshots[0])) as connection:
            self.assertNotIn('account_kind', {row[1] for row in connection.execute('pragma table_info(users)')})
            self.assertEqual(connection.execute('SELECT body FROM posts WHERE id=1').fetchone()[0], LEGACY_POSTS[0])

    def test_registered_identity_and_password_survive_backup_restore_and_restart(self):
        self.start()
        self.submit('/', {'body': 'Account backup article'})
        self.register()
        sandbox = self.app.extensions['public_demo']
        snapshot = Path(self.directory.name) / 'account-backup.sqlite'
        with self.app.app_context(), sandbox.exclusive():
            sandbox.backup(snapshot)
        self.submit('/', {'body': 'Later article'})
        with self.app.app_context(), sandbox.exclusive():
            sandbox.restore(snapshot)
        another_app = self.create_app()
        try:
            other = another_app.test_client()
            self.assertEqual(self.login(other).status_code, 302)
            with another_app.app_context():
                self.assertTrue(User.query.filter_by(username='writer_one').first().is_registered)
                self.assertIsNotNone(Post.query.filter_by(body='Account backup article').first())
                self.assertIsNone(Post.query.filter_by(body='Later article').first())
        finally:
            with another_app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_legacy_entry_redirects_and_join_alias_uses_registration(self):
        self.assertEqual(self.client.get('/demo').location, '/welcome')
        token = self.token(path='/welcome')
        response = self.client.post('/demo', data={'csrf_token': token})
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.location, '/welcome')
        self.assertEqual(self.client.get('/join').status_code, 200)

    def test_auth_rate_limit_is_separate_and_does_not_delete_accounts(self):
        self.register()
        self.app.config['PUBLIC_DEMO_AUTH_LIMIT'] = 1
        other = self.app.test_client()
        # Registration already used this IP's auth bucket; no account mutation occurs.
        response = self.login(other)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '60')
        with self.app.app_context():
            self.assertTrue(User.query.filter_by(username='writer_one').first().is_registered)

    def test_account_login_rejects_external_next_destinations(self):
        self.register()
        for destination in ['//outside.example', '/\\outside.example', 'https://outside.example']:
            other = self.app.test_client()
            token = self.token(other, '/auth/login')
            response = other.post('/auth/login', query_string={'next': destination},
                                  data={'csrf_token': token, 'username': 'writer_one', 'password': 'Good-password-123'})
            self.assertEqual(response.location, '/')

    def test_public_logout_requires_post_and_csrf_without_erasing_content(self):
        uid = self.start()
        self.submit('/', {'body': 'Saved before explicit logout'})
        self.assertEqual(self.client.get('/auth/logout').status_code, 405)
        self.assertEqual(self.client.post('/auth/logout').status_code, 400)
        with self.client.session_transaction() as saved:
            self.assertEqual(saved['_user_id'], str(uid))
        self.assertEqual(self.submit('/auth/logout').status_code, 302)
        with self.client.session_transaction() as saved:
            self.assertNotIn('_user_id', saved)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(User, uid))
            self.assertEqual(Post.query.filter_by(author_id=uid).count(), 1)


if __name__ == '__main__':
    unittest.main()
