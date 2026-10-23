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
        response = client.post('/demo', data={'csrf_token': self.token(client, '/demo')})
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

    def test_fresh_home_seeds_samples_and_has_no_register_link(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('进入演示', page)
        # An old commented-out block can contain the historical URL; only visible HTML matters.
        visible = re.sub(r'<!--.*?-->', '', page, flags=re.S)
        self.assertNotIn('/auth/register', visible)
        self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, nofollow')
        with self.app.app_context():
            self.assertEqual(Post.query.count(), 3)
            self.assertFalse(User.query.filter_by(username='demo').first().is_administrator())

    def test_entry_requires_csrf_and_creates_distinct_normal_users(self):
        self.client.get('/demo')
        self.assertEqual(self.client.post('/demo').status_code, 400)
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
        blocked = ['/auth/register', '/auth/change-password', '/auth/change-email', '/auth/reset',
                   '/auth/reset/anything', '/auth/confirm', '/edit-profile/1', '/moderate',
                   '/moderate/disable/1', '/moderate/enable/1', '/api/v1.0/posts/']
        for path in blocked:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
                self.assertEqual(self.client.post(path, data={'body': 'blocked'}).status_code, 403)
        self.assertEqual(self.client.put('/api/v1.0/posts/1', json={'body': 'blocked'}).status_code, 403)
        self.assertEqual(self.client.get('/auth/login').location, '/demo')
        self.assertEqual(self.client.post('/auth/login', data={'email': 'demo@example.com',
                                                             'password': 'local-demo-only'}).status_code, 403)

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
        self.app.config['PUBLIC_DEMO_MAX_POSTS'] = 1
        self.app.config['PUBLIC_DEMO_MAX_COMMENTS'] = 1
        self.assertEqual(self.submit('/', {'body': 'One article'}).status_code, 302)
        self.assertEqual(self.submit('/', {'body': 'Another article'}).status_code, 429)
        self.assertEqual(self.submit('/post/1', {'body': 'One comment'}).status_code, 302)
        self.assertEqual(self.submit('/post/1', {'body': 'Another comment'}).status_code, 429)
        self.assertEqual(self.submit('/', {'body': 'x' * 4001}).status_code, 413)
        self.assertEqual(self.submit('/post/1', {'body': 'x' * 1001}).status_code, 413)
        self.assertEqual(self.submit('/edit-profile', {'about_me': 'x' * 501}).status_code, 413)

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
        response = other.post('/demo', data={'csrf_token': self.token(other, '/demo')})
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
        response = self.client.post('/', data={'body': 'x' * (20 * 1024)})
        self.assertEqual(response.status_code, 413)


if __name__ == '__main__':
    unittest.main()
