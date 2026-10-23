"""Acceptance of the actual public demo routes, including CSRF and reset epochs."""
import html
import json
from pathlib import Path
import re
import runpy
import tempfile
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

    def test_expired_reset_revokes_old_sessions_even_if_ids_are_reused(self):
        old_uid = self.start()
        self.assertEqual(self.submit('/', {'body': 'Before reset'}).status_code, 302)
        sandbox = self.app.extensions['public_demo']
        saved = json.loads(sandbox.marker.read_text(encoding='utf-8'))
        saved['started'] -= 86401
        sandbox.marker.write_text(json.dumps(saved), encoding='utf-8')
        newer = self.app.test_client()
        new_uid = self.start(newer)
        self.assertEqual(old_uid, new_uid)  # SQLite may reuse the primary key.
        page = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('Before reset', page)
        with self.client.session_transaction() as saved:
            self.assertNotIn('_user_id', saved)
        self.assertEqual(self.submit('/', {'body': 'Old session write'}, token_path='/demo').status_code, 403)

    def test_restart_preserves_epoch_until_expiration(self):
        uid = self.start()
        cookie = self.client.get_cookie('lihou_demo')
        another_app = self.create_app()
        client = another_app.test_client()
        client.set_cookie('lihou_demo', cookie.value)
        self.assertEqual(client.get('/').status_code, 200)
        with client.session_transaction() as saved:
            self.assertEqual(saved.get('_user_id'), str(uid))
        with another_app.app_context():
            db.session.remove()
            db.engine.dispose()

    def test_cli_reset_is_explicit_and_invalidates_the_running_apps_sessions(self):
        self.start()
        self.assertEqual(self.submit('/', {'body': 'Before CLI reset'}).status_code, 302)
        with patch.dict('os.environ', {'FLASK_CONFIG': 'public-demo'}):
            namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'manage.py'),
                                      run_name='public_demo_cli_probe')
        command_app = namespace['app']
        try:
            runner = command_app.test_cli_runner()
            self.assertNotEqual(runner.invoke(args=['reset-public-demo']).exit_code, 0)
            with self.app.app_context():
                self.assertIsNotNone(Post.query.filter_by(body='Before CLI reset').first())
            self.assertEqual(runner.invoke(args=['reset-public-demo', '--yes']).exit_code, 0)
            self.assertEqual(self.client.get('/').status_code, 200)
            with self.client.session_transaction() as saved:
                self.assertNotIn('_user_id', saved)
            with self.app.app_context():
                self.assertEqual(Post.query.count(), 3)
        finally:
            with command_app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_reset_refuses_another_database_and_preserves_other_files(self):
        self.client.get('/')
        sentinel = Path(self.directory.name) / 'production.sqlite'
        sentinel.write_bytes(b'not-the-demo-database')
        sandbox = self.app.extensions['public_demo']
        with self.app.app_context(), patch.object(sandbox, 'app', create_app('testing')):
            with self.assertRaisesRegex(RuntimeError, 'dedicated'):
                sandbox.reset()
        self.assertEqual(sentinel.read_bytes(), b'not-the-demo-database')

    def test_payload_size_and_health(self):
        self.assertEqual(self.client.get('/healthz').get_json(), {'status': 'ok'})
        self.start()
        response = self.client.post('/', data={'body': 'x' * (20 * 1024)})
        self.assertEqual(response.status_code, 413)


if __name__ == '__main__':
    unittest.main()
