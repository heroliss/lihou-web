"""Public author identity and ordinary writing controls with synthetic data only."""
import html
import re
import tempfile
import unittest
from unittest.mock import patch

from app import create_app, db
from app.models import Comment, Post, Role, User


class OwnerIdentityUITests(unittest.TestCase):
    password = 'Owner-UI-tests-only-2026'
    legacy_notice = '用于发布站点介绍与指南的内容身份，由管理员维护。'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict('os.environ', {
            'SECRET_KEY': 'owner-ui-tests-only-' * 3,
            'LISS_DATA_DIR': self.directory.name,
            'LIHOU_PROXY_HOPS': '0', 'LISS_EMAIL_ACCOUNTS': '0',
        })
        self.environment.start()
        self.app = create_app('public-demo')
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False,
                               MAIL_SUPPRESS_SEND=True)
        self.client = self.app.test_client()
        self.assertEqual(self.client.get('/').status_code, 200)
        with self.app.app_context():
            admin_role = Role.query.filter_by(permissions=0xff).one()
            reader_role = Role.query.filter_by(default=True).one()
            # A distinct username catches hard-coded links to demo/liss/lissbliss.
            owner = User(username='writing_owner', name='Synthetic Author',
                         password=self.password, account_kind='account',
                         is_site_owner=True, confirmed=True, role=admin_role)
            admin = User(username='writing_admin', name='Synthetic Admin',
                         password=self.password, account_kind='account',
                         confirmed=True, role=admin_role)
            reader = User(username='writing_reader', name='Synthetic Reader',
                          password=self.password, account_kind='account',
                          confirmed=True, role=reader_role)
            db.session.add_all([owner, admin, reader])
            owner_post = Post(author=owner, body='# Owner article\n\nA synthetic article.')
            reader_post = Post(author=reader, body='# Reader article\n\nAnother synthetic article.')
            db.session.add_all([owner_post, reader_post])
            comment = Comment(author=owner, post=reader_post,
                              body='Synthetic author reply')
            db.session.add(comment)
            db.session.commit()
            self.owner_post_id = owner_post.id
            self.reader_post_id = reader_post.id
            self.comment_id = comment.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.environment.stop()
        self.directory.cleanup()

    def login(self, username='writing_owner'):
        page = self.client.get('/auth/login').get_data(as_text=True)
        token = html.unescape(re.search(
            r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1))
        response = self.client.post('/auth/login', data={
            'username': username, 'password': self.password, 'csrf_token': token,
        })
        self.assertEqual(response.status_code, 302)

    def test_owner_profile_has_author_badge_without_separate_identity_notice(self):
        response = self.client.get('/user/writing_owner')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('identity-badge identity-author">站点作者</span>', page)
        self.assertIn('@writing_owner', page)
        self.assertNotIn(self.legacy_notice, page)
        self.assertNotIn('不可登录', page)
        self.assertNotIn('管理站点文章', page)

    def test_other_admin_and_reader_do_not_claim_site_ownership(self):
        for username in ('writing_admin', 'writing_reader'):
            with self.subTest(username=username):
                response = self.client.get('/user/' + username)
                self.assertEqual(response.status_code, 200)
                page = response.get_data(as_text=True)
                self.assertNotIn('identity-author', page)
                self.assertIn('identity-badge identity-account">账号</span>', page)

    def test_home_author_link_uses_configured_owner_account(self):
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('href="/user/writing_owner">认识站点作者', page)
        self.assertNotIn('href="/user/demo">认识站点作者', page)
        self.assertNotIn('href="/user/lissbliss">认识站点作者', page)

    def test_canonical_owner_links_preserve_the_chosen_username_case(self):
        with self.app.app_context():
            owner = User.query.filter_by(username='writing_owner').one()
            owner.username = 'Liss'
            db.session.commit()
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('href="/user/Liss">认识站点作者', page)
        self.assertEqual(self.client.get('/user/Liss').status_code, 200)
        response = self.client.get('/user/liss')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.location, '/user/Liss')

    def test_author_badge_is_shared_by_article_and_comment(self):
        article = self.client.get(f'/post/{self.owner_post_id}').get_data(as_text=True)
        self.assertIn('identity-badge identity-author">站点作者</span>', article)
        page = self.client.get(f'/post/{self.reader_post_id}').get_data(as_text=True)
        comment = re.search(
            rf'<li class="comment" id="comment-{self.comment_id}">(.*?)</li>',
            page, re.S).group(1)
        self.assertIn('identity-badge identity-author">站点作者</span>', comment)
        self.assertIn('Synthetic author reply', comment)

    def test_owner_uses_normal_profile_and_article_edit_controls(self):
        self.login()
        response = self.client.get('/user/writing_owner')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('href="/edit-profile"', page)
        self.assertIn(f'href="/edit/{self.owner_post_id}"', page)
        self.assertNotIn('编辑（管理）', page)
        self.assertIn('href="/admin/"', page)
        self.assertIn('href="/user/writing_owner"', page)
        self.assertEqual(self.client.get('/edit-profile').status_code, 200)
        self.assertEqual(self.client.get(f'/edit/{self.owner_post_id}').status_code, 200)

    def test_admin_user_card_keeps_owner_and_admin_roles_explicit(self):
        self.login('writing_admin')
        page = self.client.get('/admin/users?q=writing_owner').get_data(as_text=True)
        self.assertIn('站点作者 · 管理员', page)
        self.assertIn('站点作者保留管理员权限', page)
        self.assertNotIn('站点作者身份用于维护站点内容，不可修改', page)
        self.assertNotIn('class="admin-user-form"', page)

    def test_admin_can_filter_site_owner_without_including_other_admins(self):
        self.login('writing_admin')
        page = self.client.get('/admin/users?kind=owner').get_data(as_text=True)
        self.assertIn('<option value="owner" selected>站点作者</option>', page)
        cards = re.findall(r'<article class="admin-content-row">(.*?)</article>', page, re.S)
        self.assertEqual(len(cards), 1)
        self.assertIn('@writing_owner', cards[0])
        self.assertNotIn('@writing_admin', cards[0])


if __name__ == '__main__':
    unittest.main()
