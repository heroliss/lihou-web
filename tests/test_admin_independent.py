"""Independent admin acceptance using only new temporary public-site databases.

Exercise HTTP authorization and retained-content behavior, without private data,
production cookies, mail credentials, or direct use of implementation helpers.
"""
import html
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from app import create_app, db
from app.community import ProjectEntry
from app.models import Comment, Post, Role, User


class AdminIndependentTests(unittest.TestCase):
    password = 'Independent-test-password-24'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict('os.environ', {
            'SECRET_KEY': 'independent-admin-tests-only-' * 3,
            'LISS_DATA_DIR': self.directory.name,
            'LIHOU_PROXY_HOPS': '0',
            'LISS_EMAIL_ACCOUNTS': '0',
        })
        self.environment.start()
        self.app = create_app('public-demo')
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False,
                               PUBLIC_DEMO_AUTH_LIMIT=200,
                               PUBLIC_DEMO_WRITE_LIMIT=200)
        self.anonymous = self.app.test_client()
        self.assertEqual(self.anonymous.get('/').status_code, 200)
        with self.app.app_context():
            ordinary = Role.query.filter_by(default=True).one()
            admin = Role.query.filter_by(permissions=0xff).one()
            moderator = Role.query.filter_by(name='论坛版主').one()
            root = User(username='review_root', password=self.password,
                        account_kind='account', role=admin, confirmed=True)
            writer = User(username='review_writer', password=self.password,
                          account_kind='account', role=ordinary, confirmed=True)
            mod = User(username='review_mod', password=self.password,
                       account_kind='account', role=moderator, confirmed=True)
            db.session.add_all([root, writer, mod])
            db.session.commit()
            self.root_id, self.writer_id, self.mod_id = root.id, writer.id, mod.id
            self.system_id = User.query.filter_by(username='demo').one().id
        self.admin = self.app.test_client()
        self.writer = self.app.test_client()
        self.login(self.admin, 'review_root')
        self.login(self.writer, 'review_writer')

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.environment.stop()
        self.directory.cleanup()

    def csrf(self, client, path='/'):
        response = client.get(path)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                          response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return html.unescape(match.group(1))

    def post(self, client, path, data=None, token_path='/'):
        return client.post(path, data={**(data or {}),
                                      'csrf_token': self.csrf(client, token_path)})

    def login(self, client, username):
        response = self.post(client, '/auth/login',
                             {'username': username, 'password': self.password},
                             token_path='/auth/login')
        self.assertEqual(response.status_code, 302)

    def preview(self, kind, target_id=None, action='update', client=None, **values):
        data = {'kind': kind, 'action': action, **values}
        if target_id is not None:
            data['target_id'] = str(target_id)
        return self.post(client or self.admin, '/admin/preview', data,
                         token_path='/admin/')

    def token(self, response):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:1000])
        match = re.search(r'name="token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match, response.get_data(as_text=True)[:1000])
        return html.unescape(match.group(1))

    def apply(self, token, client=None):
        return self.post(client or self.admin, '/admin/apply', {'token': token},
                         token_path='/admin/')

    def create_post(self, hidden=False, project=True):
        with self.app.app_context():
            post = Post(body='# Independent visibility needle\n\nRetained text.',
                        author_id=self.writer_id, hidden=hidden)
            db.session.add(post)
            db.session.flush()
            if project:
                db.session.add(ProjectEntry(post=post, project_slug='outpost'))
            db.session.commit()
            return post.id

    def settings_values(self, **changes):
        return {'site_name': 'Review Community', 'tagline': 'A concise tagline',
                'welcome': 'Welcome to a temporary test.', 'registration_open': '1',
                'guest_posting_open': '1', 'guest_post_limit': '10', **changes}

    def test_admin_requires_account_role_and_matching_public_identity(self):
        paths = ['/admin/', '/admin/posts', '/admin/comments', '/admin/users',
                 '/admin/settings', '/admin/audit']
        for path in paths:
            with self.subTest(path=path):
                self.assertIn(self.anonymous.get(path).status_code, {302, 401, 403})
                self.assertEqual(self.writer.get(path).status_code, 403)
        moderator = self.app.test_client()
        self.login(moderator, 'review_mod')
        self.assertEqual(moderator.get('/admin/').status_code, 403)
        with self.admin.session_transaction() as session:
            session['public_demo_uid'] = str(self.writer_id)
        self.assertIn(self.admin.get('/admin/').status_code, {302, 401, 403})

    def test_guest_role_accident_cannot_grant_administration(self):
        guest = self.app.test_client()
        self.assertEqual(self.post(guest, '/welcome', token_path='/welcome').status_code, 302)
        with guest.session_transaction() as session:
            guest_id = int(session['public_demo_uid'])
        with self.app.app_context():
            user = db.session.get(User, guest_id)
            user.role = Role.query.filter_by(permissions=0xff).one()
            db.session.commit()
        self.assertEqual(guest.get('/admin/').status_code, 403)

    def test_csrf_and_get_cannot_mutate_an_article(self):
        post_id = self.create_post()
        self.assertEqual(self.admin.post('/admin/preview', data={
            'kind': 'post', 'target_id': post_id, 'action': 'hide'}).status_code, 400)
        token = self.token(self.preview('post', post_id, 'hide'))
        self.assertEqual(self.admin.post('/admin/apply', data={'token': token}).status_code, 400)
        self.assertEqual(self.admin.get('/admin/apply', query_string={'token': token}).status_code, 405)
        self.assertEqual(self.admin.get('/admin/preview', query_string={
            'kind': 'post', 'target_id': post_id, 'action': 'hide'}).status_code, 405)
        with self.app.app_context():
            self.assertFalse(db.session.get(Post, post_id).hidden)

    def test_confirmation_token_is_tamper_resistant_single_use_and_session_bound(self):
        post_id = self.create_post()
        token = self.token(self.preview('post', post_id, 'hide'))
        with self.app.app_context():
            self.assertFalse(db.session.get(Post, post_id).hidden)
        other = self.app.test_client()
        self.login(other, 'review_root')
        self.assertIn(self.apply(token, other).status_code, {400, 403, 409})
        tampered = ('a' if token[0] != 'a' else 'b') + token[1:]
        self.assertIn(self.apply(tampered).status_code, {400, 403, 409})
        self.assertEqual(self.apply(token).status_code, 302)
        self.assertIn(self.apply(token).status_code, {400, 403, 409})
        with self.app.app_context():
            self.assertTrue(db.session.get(Post, post_id).hidden)

    def test_preview_rejects_stale_content_instead_of_silently_overwriting(self):
        post_id = self.create_post()
        token = self.token(self.preview('post', post_id, 'hide'))
        with self.app.app_context():
            db.session.get(Post, post_id).body = '# A newer edit that must survive'
            db.session.commit()
        self.assertIn(self.apply(token).status_code, {400, 403, 409})
        with self.app.app_context():
            post = db.session.get(Post, post_id)
            self.assertEqual(post.body, '# A newer edit that must survive')
            self.assertFalse(post.hidden)

    def test_consumed_confirmation_cannot_replay_with_restored_cookie_and_article_state(self):
        post_id = self.create_post()
        token = self.token(self.preview('post', post_id, 'hide'))
        name = self.app.config['SESSION_COOKIE_NAME']
        cookie_with_pending_intent = self.admin.get_cookie(name).value
        self.assertEqual(self.apply(token).status_code, 302)
        # Restore through a separate legitimate confirmation. Both the record and
        # a copied signed browser cookie now match the old preview's old state.
        self.assertEqual(self.apply(self.token(self.preview('post', post_id, 'restore'))).status_code, 302)
        copied = self.app.test_client()
        copied.set_cookie(name, cookie_with_pending_intent)
        self.assertIn(self.apply(token, copied).status_code, {400, 403, 409})
        with self.app.app_context():
            self.assertFalse(db.session.get(Post, post_id).hidden)

    def test_expired_preview_cannot_apply_after_confirmation_window(self):
        post_id = self.create_post()
        token = self.token(self.preview('post', post_id, 'hide'))
        csrf = self.csrf(self.admin, '/admin/')
        with patch('itsdangerous.timed.time.time', return_value=time.time() + 601):
            response = self.admin.post('/admin/apply', data={'token': token, 'csrf_token': csrf})
        self.assertIn(response.status_code, {400, 403, 409})
        with self.app.app_context():
            self.assertFalse(db.session.get(Post, post_id).hidden)

    def test_hidden_post_cannot_leak_through_feed_profile_project_detail_or_edit(self):
        post_id = self.create_post()
        self.assertEqual(self.apply(self.token(self.preview('post', post_id, 'hide'))).status_code, 302)
        for client in (self.anonymous, self.writer):
            for path in ('/', '/user/review_writer', '/projects/outpost/talk/'):
                with self.subTest(path=path):
                    self.assertNotIn('Independent visibility needle', client.get(path).get_data(as_text=True))
            self.assertEqual(client.get(f'/post/{post_id}').status_code, 404)
            self.assertIn(client.get(f'/edit/{post_id}').status_code, {302, 404})
        self.assertEqual(self.post(self.writer, f'/post/{post_id}', {'body': 'no hidden replies'}).status_code, 404)
        self.assertEqual(self.post(self.writer, f'/edit/{post_id}', {'body': 'no hidden edits'}).status_code, 404)
        # A retained article can be restored with its identity and project link intact.
        self.assertEqual(self.apply(self.token(self.preview('post', post_id, 'restore'))).status_code, 302)
        self.assertEqual(self.anonymous.get(f'/post/{post_id}').status_code, 200)
        with self.app.app_context():
            self.assertEqual(db.session.get(Post, post_id).body.splitlines()[0], '# Independent visibility needle')
            self.assertEqual(db.session.get(ProjectEntry, post_id).project_slug, 'outpost')
            self.assertEqual(Comment.query.filter_by(post_id=post_id).count(), 0)

    def test_hidden_comments_are_absent_from_body_count_and_pagination(self):
        post_id = self.create_post()
        with self.app.app_context():
            db.session.add_all([
                Comment(post_id=post_id, author_id=self.writer_id, body='Visible comment needle', disabled=False),
                Comment(post_id=post_id, author_id=self.writer_id, body='Hidden comment needle', disabled=True)])
            db.session.commit()
        self.app.config['FLASKY_COMMENTS_PER_PAGE'] = 1
        detail = self.anonymous.get(f'/post/{post_id}').get_data(as_text=True)
        self.assertIn('Visible comment needle', detail)
        self.assertNotIn('Hidden comment needle', detail)
        self.assertIn('1 条评论', detail)
        self.assertNotIn(f'/post/{post_id}?page=2', detail)
        self.assertNotIn('Hidden comment needle', self.anonymous.get(
            f'/post/{post_id}?page=-1').get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Comment.query.filter_by(post_id=post_id).count(), 2)

    def test_real_new_comment_remains_readable_without_explicit_visibility_flag(self):
        post_id = self.create_post()
        response = self.post(self.writer, f'/post/{post_id}', {'body': 'A real new reply'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('A real new reply', self.anonymous.get(f'/post/{post_id}').get_data(as_text=True))
        with self.app.app_context():
            self.assertFalse(Comment.query.filter_by(post_id=post_id).one().disabled)

    def test_system_guest_and_invalid_role_requests_cannot_change_account_kinds(self):
        guest = self.app.test_client()
        self.post(guest, '/welcome', token_path='/welcome')
        with guest.session_transaction() as session:
            guest_id = int(session['public_demo_uid'])
        for uid, role in ((self.system_id, 'admin'), (guest_id, 'admin'),
                          (self.writer_id, 'made-up-role')):
            response = self.preview('user', uid, role=role, disabled='0')
            self.assertIn(response.status_code, {400, 403, 409, 422})
        with self.app.app_context():
            self.assertEqual(db.session.get(User, self.system_id).account_kind, 'system')
            self.assertEqual(db.session.get(User, guest_id).account_kind, 'guest')
            self.assertFalse(db.session.get(User, guest_id).is_administrator())
            self.assertFalse(db.session.get(User, self.writer_id).is_administrator())

    def test_last_admin_demotion_and_self_disabling_are_rejected(self):
        for role, disabled in (('normal', '0'), ('admin', '1')):
            response = self.preview('user', self.root_id, role=role, disabled=disabled)
            self.assertIn(response.status_code, {400, 403, 409, 422})
        with self.app.app_context():
            self.assertTrue(db.session.get(User, self.root_id).is_administrator())
            self.assertFalse(db.session.get(User, self.root_id).disabled)

    def test_disabled_accounts_cannot_relogin_and_reenable_does_not_restore_stolen_cookie(self):
        name = self.app.config['SESSION_COOKIE_NAME']
        old_cookie = self.writer.get_cookie(name).value
        with self.app.app_context():
            old_version = db.session.get(User, self.writer_id).session_version
        self.assertEqual(self.apply(self.token(self.preview(
            'user', self.writer_id, role='normal', disabled='1'))).status_code, 302)
        self.assertNotIn('/edit-profile', self.writer.get('/').get_data(as_text=True))
        fresh = self.app.test_client()
        rejected = self.post(fresh, '/auth/login', {'username': 'review_writer', 'password': self.password},
                             token_path='/auth/login')
        self.assertEqual(rejected.status_code, 200)
        with fresh.session_transaction() as session:
            self.assertNotIn('_user_id', session)
        self.assertEqual(self.apply(self.token(self.preview(
            'user', self.writer_id, role='normal', disabled='0'))).status_code, 302)
        stolen = self.app.test_client()
        stolen.set_cookie(name, old_cookie)
        self.assertNotIn('/edit-profile', stolen.get('/').get_data(as_text=True))
        with self.app.app_context():
            self.assertGreater(db.session.get(User, self.writer_id).session_version, old_version)
        self.login(fresh, 'review_writer')

    def test_disabling_and_restoring_moderator_preserves_its_saved_role(self):
        self.assertEqual(self.apply(self.token(self.preview(
            'user', self.mod_id, role='moderator', disabled='1'))).status_code, 302)
        with self.app.app_context():
            moderator = db.session.get(User, self.mod_id)
            self.assertEqual(moderator.admin_role_key, 'moderator')
            self.assertFalse(moderator.can(0x08))
        self.assertEqual(self.apply(self.token(self.preview(
            'user', self.mod_id, role='moderator', disabled='0'))).status_code, 302)
        with self.app.app_context():
            moderator = db.session.get(User, self.mod_id)
            self.assertEqual(moderator.admin_role_key, 'moderator')
            self.assertTrue(moderator.can(0x08))

    def test_disabled_guest_cannot_upgrade_or_rejoin_and_old_cookie_stays_revoked(self):
        guest = self.app.test_client()
        self.assertEqual(self.post(guest, '/welcome', token_path='/welcome').status_code, 302)
        self.assertEqual(self.post(guest, '/', {'body': '# Retained guest article'}).status_code, 302)
        name = self.app.config['SESSION_COOKIE_NAME']
        old_cookie = guest.get_cookie(name).value
        with guest.session_transaction() as session:
            guest_id = int(session['public_demo_uid'])
        self.assertEqual(self.apply(self.token(self.preview(
            'user', guest_id, role='normal', disabled='1'))).status_code, 302)
        self.assertNotIn('/edit-profile', guest.get('/').get_data(as_text=True))
        # The information page may remain visible; identity creation is blocked.
        self.assertIn(guest.get('/welcome').status_code, {200, 403})
        self.assertEqual(guest.get('/auth/register').status_code, 403)
        csrf = self.csrf(guest, '/auth/login')
        self.assertEqual(guest.post('/welcome', data={'csrf_token': csrf}).status_code, 403)
        self.assertEqual(guest.post('/auth/register', data={
            'csrf_token': csrf, 'username': 'disabled_guest_escape',
            'password': self.password, 'password2': self.password}).status_code, 403)
        # A different valid registered identity still has its ordinary login path.
        self.login(guest, 'review_writer')
        self.assertEqual(self.apply(self.token(self.preview(
            'user', guest_id, role='normal', disabled='0'))).status_code, 302)
        previous = self.app.test_client()
        previous.set_cookie(name, old_cookie)
        self.assertNotIn('/edit-profile', previous.get('/').get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(User.query.filter_by(username='disabled_guest_escape').first())
            self.assertEqual(Post.query.filter_by(author_id=guest_id).count(), 1)

    def test_legacy_management_routes_and_public_api_stay_closed(self):
        for client in (self.anonymous, self.writer, self.admin):
            for path in (f'/edit-profile/{self.writer_id}', '/moderate',
                         '/moderate/enable/1', '/moderate/disable/1', '/api/v1.0/posts/'):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 403)

    def test_settings_are_validated_and_survive_a_new_application_instance(self):
        invalid = self.settings_values()
        invalid['guest_post_limit'] = '0'
        self.assertIn(self.preview('settings', **invalid).status_code, {400, 422})
        values = self.settings_values()
        values.update(site_name='Review <safe> Community', guest_post_limit='2')
        self.assertEqual(self.apply(self.token(self.preview('settings', **values))).status_code, 302)
        self.assertIn('Review &lt;safe&gt; Community', self.anonymous.get('/').get_data(as_text=True))
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        restarted = create_app('public-demo')
        restarted.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        try:
            page = restarted.test_client().get('/').get_data(as_text=True)
            self.assertIn('Review &lt;safe&gt; Community', page)
            self.assertIn('A concise tagline', page)
        finally:
            with restarted.app_context():
                db.session.remove()
                db.engine.dispose()
        # All persisted files remain under this temporary directory.
        self.assertTrue((Path(self.directory.name) / 'public-demo.sqlite').is_file())

    def test_settings_affect_real_registration_guest_quota_and_article_permissions(self):
        values = self.settings_values(registration_open='0', guest_post_limit='2')
        self.assertEqual(self.apply(self.token(self.preview('settings', **values))).status_code, 302)
        guest = self.app.test_client()
        self.assertIn('访客最多 2 篇', guest.get('/welcome').get_data(as_text=True))
        self.assertEqual(self.post(guest, '/welcome', token_path='/welcome').status_code, 302)
        self.assertIn('访客 0 / 2 篇', guest.get('/').get_data(as_text=True))
        for number, path in enumerate(('/', '/projects/outpost/talk/')):
            self.assertEqual(self.post(guest, path, {'body': f'# Guest article {number}'}).status_code, 302)
        self.assertEqual(self.post(guest, '/', {'body': '# Beyond current quota'}).status_code, 429)
        register_response = guest.get('/auth/register')
        if register_response.status_code == 200:
            # A closed registration form may show a notice rather than a 403.
            token = self.csrf(guest, '/auth/register')
            registration = guest.post('/auth/register', data={
                'csrf_token': token, 'username': 'cannot_register_now',
                'password': self.password, 'password2': self.password})
            self.assertIn(registration.status_code, {200, 403})
        else:
            self.assertEqual(register_response.status_code, 403)
        with self.app.app_context():
            self.assertIsNone(User.query.filter_by(username='cannot_register_now').first())
        values['guest_posting_open'] = '0'
        self.assertEqual(self.apply(self.token(self.preview('settings', **values))).status_code, 302)
        fresh_guest = self.app.test_client()
        self.assertEqual(self.post(fresh_guest, '/welcome', token_path='/welcome').status_code, 302)
        closed_home = fresh_guest.get('/').get_data(as_text=True)
        self.assertIn('访客发文暂时关闭', closed_home)
        self.assertNotIn('<details class="composer"', closed_home)
        for path in ('/', '/projects/outpost/talk/'):
            self.assertEqual(self.post(fresh_guest, path, {'body': '# Guest posting closed'}).status_code, 403)
        # Existing identities retain replies, and registered users retain writing.
        post_id = self.create_post()
        self.assertEqual(self.post(guest, f'/post/{post_id}', {'body': 'Replies remain open'}).status_code, 302)
        self.assertEqual(self.post(self.writer, '/', {'body': '# Registered writing remains open'}).status_code, 302)


if __name__ == '__main__':
    unittest.main()
