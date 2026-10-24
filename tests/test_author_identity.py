"""Distinguish the content author from registered accounts using isolated data."""
import html
import re
import tempfile
import unittest
from unittest.mock import patch

from app import create_app, db
from app.models import Post, Role, User


class AuthorIdentityTests(unittest.TestCase):
    notice = '用于发布站点介绍与指南的内容身份，由管理员维护。'
    password = 'Author-identity-test-only-2026'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict('os.environ', {
            'SECRET_KEY': 'author-identity-tests-only-' * 3,
            'LISS_DATA_DIR': self.directory.name,
            'LIHOU_PROXY_HOPS': '0',
            'LISS_EMAIL_ACCOUNTS': '0',
        })
        self.environment.start()
        self.app = create_app('public-demo')
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = self.app.test_client()
        self.assertEqual(self.client.get('/').status_code, 200)
        with self.app.app_context():
            admin_role = Role.query.filter_by(permissions=0xff).one()
            reader_role = Role.query.filter_by(default=True).one()
            admin = User(username='identity_admin', password=self.password,
                         account_kind='account', confirmed=True,
                         role=admin_role)
            reader = User(username='identity_reader', password=self.password,
                          account_kind='account', confirmed=True,
                          role=reader_role)
            db.session.add_all([admin, reader])
            db.session.commit()
            self.author_post = Post.query.join(User).filter(User.account_kind == 'system').first().id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.environment.stop()
        self.directory.cleanup()

    def login(self, username):
        page = self.client.get('/auth/login').get_data(as_text=True)
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
        return self.client.post('/auth/login', data={
            'username': username, 'password': self.password,
            'csrf_token': html.unescape(token),
        })

    def test_anonymous_author_page_explains_content_identity(self):
        response = self.client.get('/user/demo')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn(self.notice, page)
        self.assertNotIn('管理站点文章', page)

    def test_admin_author_page_has_management_link_and_existing_edit_access(self):
        self.assertEqual(self.login('identity_admin').status_code, 302)
        page = self.client.get('/user/demo').get_data(as_text=True)
        self.assertIn(self.notice, page)
        self.assertIn('href="/admin/posts">管理站点文章</a>', page)
        self.assertIn('编辑（管理）', page)
        self.assertEqual(self.client.get(f'/edit/{self.author_post}').status_code, 200)

    def test_registered_profile_does_not_claim_to_be_content_identity(self):
        self.assertEqual(self.login('identity_admin').status_code, 302)
        page = self.client.get('/user/identity_reader').get_data(as_text=True)
        self.assertNotIn(self.notice, page)
        self.assertNotIn('管理站点文章', page)

    def test_system_author_cannot_log_in_even_with_a_known_test_password(self):
        with self.app.app_context():
            author = User.query.filter_by(account_kind='system').one()
            author.password = self.password
            db.session.commit()
        self.assertEqual(self.login('demo').status_code, 200)
        with self.client.session_transaction() as session:
            self.assertNotIn('public_demo_uid', session)
        self.assertEqual(self.client.get('/admin/').status_code, 403)
