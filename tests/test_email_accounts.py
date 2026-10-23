"""Email-account acceptance uses a test-only outbox, never real recipients."""
import html
import json
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.parse import urlsplit

from app import create_app, db
from app.models import Post, User
from app.transactional_mail import DeliveryResult, deliver_account_email, email_accounts_enabled


class EmailAccountsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict('os.environ', {
            'SECRET_KEY': 'test-email-account-secret-' * 3,
            'LISS_DATA_DIR': self.directory.name,
            'LIHOU_DEMO_DIR': self.directory.name,
            'LIHOU_PROXY_HOPS': '0',
        })
        self.environment.start()
        self.app = create_app('public-demo')
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False,
            LISS_EMAIL_ACCOUNTS=True, LISS_MAIL_PROVIDER='outbox',
            LISS_PUBLIC_BASE_URL='https://liss.example.com',
            PUBLIC_DEMO_AUTH_LIMIT=100, PUBLIC_DEMO_START_LIMIT=100,
            PUBLIC_MAIL_COOLDOWN_SECONDS=0, PUBLIC_MAIL_IP_LIMIT=1000)
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.environment.stop()
        self.directory.cleanup()

    def token(self, path, client=None):
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, 200, path)
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match, path)
        return html.unescape(match.group(1))

    def submit(self, path, data=None, client=None, token_path=None):
        client = client or self.client
        return client.post(path, data={**(data or {}),
            'csrf_token': self.token(token_path or path, client)})

    def start(self, client=None):
        client = client or self.client
        self.assertEqual(self.submit('/welcome', client=client).status_code, 302)
        with client.session_transaction() as saved:
            return int(saved['public_demo_uid'])

    def register(self, email='writer@example.com', client=None, nickname='Writer'):
        return self.submit('/auth/register', {'email': email, 'nickname': nickname,
            'password': 'Good-password-123', 'password2': 'Good-password-123'}, client=client)

    def latest_link(self):
        message = self.app.extensions['liss_mail_outbox'][-1]
        link = re.search(r'https://[^\s<>]+', message['text']).group(0)
        return urlsplit(link).path

    def confirm_registered(self):
        self.assertEqual(self.register().status_code, 302)
        self.assertEqual(self.app.test_client().get(self.latest_link()).status_code, 302)

    def test_email_registration_is_normalized_private_and_unverified(self):
        page = self.client.get('/auth/register').get_data(as_text=True)
        self.assertIn('name="email"', page)
        self.assertNotIn('name="username"', page)
        self.assertEqual(self.register(' WRITER@EXAMPLE.COM ').status_code, 302)
        with self.app.app_context():
            user = User.query.filter_by(email='writer@example.com').one()
            self.assertFalse(user.confirmed)
            self.assertTrue(user.username.startswith('member_'))
            self.assertEqual(user.name, 'Writer')
            self.assertFalse(user.is_administrator())
        message = self.app.extensions['liss_mail_outbox'][0]
        self.assertEqual(message['to'], 'writer@example.com')
        self.assertIn('https://liss.example.com/auth/confirm/', message['text'])
        self.assertNotIn('localhost', message['text'])
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_guest_upgrade_preserves_identity_and_all_content(self):
        uid = self.start()
        self.submit('/', {'body': 'Keep the guest article'})
        self.submit('/post/1', {'body': 'Keep the guest reply'}, token_path='/')
        self.submit('/follow/demo', token_path='/')
        self.submit('/edit-profile', {'name': 'Existing name', 'location': 'Here', 'about_me': 'Keep me'})
        with self.app.app_context():
            before = {table: db.session.execute(db.text(f'SELECT count(*) FROM {table}')).scalar()
                      for table in ['users', 'posts', 'comments', 'follows']}
            handle = db.session.get(User, uid).username
        self.assertEqual(self.register(nickname='').status_code, 302)
        with self.client.session_transaction() as saved:
            self.assertEqual(saved['public_demo_uid'], str(uid))
        with self.app.app_context():
            user = db.session.get(User, uid)
            self.assertEqual(user.username, handle)
            self.assertEqual(user.name, 'Existing name')
            self.assertFalse(user.confirmed)
            self.assertEqual(user.posts.first().body, 'Keep the guest article')
            self.assertEqual(user.comments.first().body, 'Keep the guest reply')
            self.assertTrue(user.is_following(User.query.filter_by(username='demo').one()))
            after = {table: db.session.execute(db.text(f'SELECT count(*) FROM {table}')).scalar()
                     for table in before}
            self.assertEqual(before, after)
        self.assertEqual(self.submit('/', {'body': 'Still allowed before confirmation'}, token_path='/').status_code, 302)

    def test_confirmation_is_anonymous_and_does_not_switch_another_visitor(self):
        self.register()
        link = self.latest_link()
        other = self.app.test_client()
        uid = self.start(other)
        self.assertEqual(other.get(link).status_code, 302)
        with other.session_transaction() as saved:
            self.assertEqual(saved['public_demo_uid'], str(uid))
        with self.app.app_context():
            self.assertTrue(User.query.filter_by(email='writer@example.com').one().confirmed)
        anonymous = self.app.test_client()
        anonymous.get(link)
        with anonymous.session_transaction() as saved:
            self.assertNotIn('_user_id', saved)

    def test_email_login_and_case_insensitive_duplicate(self):
        self.register()
        other = self.app.test_client()
        self.assertEqual(self.register('WRITER@EXAMPLE.COM', client=other).status_code, 200)
        response = self.submit('/auth/login', {'username': ' WRITER@EXAMPLE.COM ',
            'password': 'Good-password-123'}, client=other)
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            uid = User.query.filter_by(email='writer@example.com').one().id
        with other.session_transaction() as saved:
            self.assertEqual(saved['_user_id'], str(uid))

    def test_tokens_reject_wrong_purpose_expiry_tampering_and_changed_credentials(self):
        self.register()
        with self.app.app_context():
            user = User.query.filter_by(email='writer@example.com').one()
            valid = user.generate_account_token('confirm-email')
            expired = user.generate_account_token('confirm-email', expiration=-1)
            self.assertIsNone(User.load_account_token(expired, 'confirm-email'))
            self.assertIsNone(User.load_account_token(valid, 'reset-password'))
            self.assertIsNone(User.load_account_token(valid + 'corrupt', 'confirm-email'))
            user.password = 'Changed-password-456'
            db.session.commit()
            self.assertIsNone(User.load_account_token(valid, 'confirm-email'))
            another = user.generate_account_token('confirm-email')
            user.email = 'changed@example.com'
            db.session.commit()
            self.assertIsNone(User.load_account_token(another, 'confirm-email'))

    def test_reset_is_one_use_and_preserves_another_browser_identity(self):
        self.confirm_registered()
        visitor = self.app.test_client()
        visitor_uid = self.start(visitor)
        self.assertEqual(self.submit('/auth/reset', {'email': 'writer@example.com'}, client=visitor).status_code, 302)
        link = self.latest_link()
        response = self.submit(link, {'password': 'New-password-456',
            'password2': 'New-password-456'}, client=visitor)
        self.assertEqual(response.status_code, 302)
        with visitor.session_transaction() as saved:
            self.assertEqual(saved['public_demo_uid'], str(visitor_uid))
        self.assertEqual(visitor.get(link).headers['Location'], '/auth/reset')
        with self.app.app_context():
            user = User.query.filter_by(email='writer@example.com').one()
            self.assertTrue(user.verify_password('New-password-456'))
            self.assertFalse(user.verify_password('Good-password-123'))

    def test_password_reset_revokes_old_logged_in_browsers_and_new_login_restores_access(self):
        self.confirm_registered()
        stale = self.app.test_client()
        self.assertEqual(self.submit('/auth/login', {'username': 'writer@example.com',
            'password': 'Good-password-123'}, client=stale).status_code, 302)
        old_csrf = self.token('/', stale)
        recovery = self.app.test_client()
        self.submit('/auth/reset', {'email': 'writer@example.com'}, client=recovery)
        link = self.latest_link()
        self.assertEqual(self.submit(link, {'password': 'Recovered-password-456',
            'password2': 'Recovered-password-456'}, client=recovery).status_code, 302)
        self.assertEqual(stale.post('/', data={'body': 'Stale session must not publish',
            'csrf_token': old_csrf}).status_code, 403)
        with stale.session_transaction() as saved:
            self.assertNotIn('_user_id', saved)
            self.assertNotIn('public_account_credential', saved)
            self.assertIn('public_demo_epoch', saved)
        with self.app.app_context():
            self.assertIsNone(Post.query.filter_by(body='Stale session must not publish').first())
        self.assertEqual(self.submit('/auth/login', {'username': 'writer@example.com',
            'password': 'Good-password-123'}, client=stale).status_code, 200)
        self.assertEqual(self.submit('/auth/login', {'username': 'writer@example.com',
            'password': 'Recovered-password-456'}, client=stale).status_code, 302)
        self.assertEqual(self.submit('/', {'body': 'New login can publish'}, client=stale).status_code, 302)
        # The original registration browser is also stale, not just the second client.
        self.client.get('/')
        with self.client.session_transaction() as saved:
            self.assertNotIn('_user_id', saved)

    def test_password_change_keeps_current_browser_and_revokes_other_browsers(self):
        self.confirm_registered()
        other = self.app.test_client()
        self.submit('/auth/login', {'username': 'writer@example.com',
            'password': 'Good-password-123'}, client=other)
        self.assertEqual(self.submit('/auth/change-password', {'old_password': 'Good-password-123',
            'password': 'Changed-password-456', 'password2': 'Changed-password-456'}).status_code, 302)
        self.assertEqual(self.submit('/', {'body': 'Password changer keeps writing'}).status_code, 302)
        with self.client.session_transaction() as saved:
            self.assertIn('_user_id', saved)
            self.assertIn('public_account_credential', saved)
        other.get('/')
        with other.session_transaction() as saved:
            self.assertNotIn('_user_id', saved)

    def test_legacy_guest_cookie_is_preserved_but_unstamped_account_logs_in_once(self):
        guest = self.app.test_client()
        uid = self.start(guest)
        with guest.session_transaction() as saved:
            saved.pop('public_account_credential', None)
        self.assertEqual(self.submit('/', {'body': 'Historical guest cookie still writes'}, client=guest).status_code, 302)
        with guest.session_transaction() as saved:
            self.assertEqual(saved['_user_id'], str(uid))
        self.register()
        with self.client.session_transaction() as saved:
            saved.pop('public_account_credential', None)
        self.client.get('/')
        with self.client.session_transaction() as saved:
            self.assertNotIn('_user_id', saved)
        self.assertEqual(self.submit('/auth/login', {'username': 'writer@example.com',
            'password': 'Good-password-123'}).status_code, 302)
        with self.client.session_transaction() as saved:
            self.assertIn('public_account_credential', saved)

    def test_reset_response_does_not_enumerate_unknown_or_unverified_addresses(self):
        self.register()
        outbox_count = len(self.app.extensions['liss_mail_outbox'])
        unknown = self.app.test_client()
        pending = self.app.test_client()
        a = self.submit('/auth/reset', {'email': 'missing@example.com'}, client=unknown)
        b = self.submit('/auth/reset', {'email': 'writer@example.com'}, client=pending)
        self.assertEqual(a.status_code, b.status_code)
        self.assertEqual(a.headers['Location'], b.headers['Location'])
        with unknown.session_transaction() as first, pending.session_transaction() as second:
            self.assertEqual(first['_flashes'], second['_flashes'])
        self.assertEqual(len(self.app.extensions['liss_mail_outbox']), outbox_count)

    def test_legacy_username_account_can_bind_email_without_losing_id(self):
        self.app.config['LISS_EMAIL_ACCOUNTS'] = False
        self.assertEqual(self.submit('/auth/register', {'username': 'legacy_writer',
            'password': 'Good-password-123', 'password2': 'Good-password-123'}).status_code, 302)
        with self.app.app_context():
            uid = User.query.filter_by(username='legacy_writer').one().id
        self.app.config['LISS_EMAIL_ACCOUNTS'] = True
        self.assertEqual(self.submit('/auth/change-email', {'email': 'legacy@example.com',
            'password': 'Good-password-123'}).status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(User, uid).email)
        self.app.test_client().get(self.latest_link())
        with self.app.app_context():
            user = db.session.get(User, uid)
            self.assertEqual(user.email, 'legacy@example.com')
            self.assertTrue(user.confirmed)
        other = self.app.test_client()
        self.assertEqual(self.submit('/auth/login', {'username': 'LEGACY_WRITER',
            'password': 'Good-password-123'}, client=other).status_code, 302)
        with other.session_transaction() as saved:
            self.assertEqual(saved['_user_id'], str(uid))

    def test_email_change_requires_password_and_keeps_old_address_until_confirmation(self):
        self.confirm_registered()
        response = self.submit('/auth/change-email', {'email': 'new@example.com',
            'password': 'Wrong-password-123'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('当前密码不正确', response.get_data(as_text=True))
        self.assertEqual(self.submit('/auth/change-email', {'email': 'new@example.com',
            'password': 'Good-password-123'}).status_code, 302)
        link = self.latest_link()
        with self.app.app_context():
            self.assertIsNotNone(User.query.filter_by(email='writer@example.com').first())
            self.assertIsNone(User.query.filter_by(email='new@example.com').first())
        self.app.test_client().get(link)
        with self.app.app_context():
            self.assertIsNone(User.query.filter_by(email='writer@example.com').first())
            self.assertTrue(User.query.filter_by(email='new@example.com').one().confirmed)

    def test_delivery_failure_keeps_pending_account_without_fake_success(self):
        with patch('app.public_accounts.deliver_account_email', return_value=DeliveryResult(False, 'unavailable')):
            self.assertEqual(self.register().status_code, 302)
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('暂未发送成功', page)
        self.assertNotIn('验证邮件已交给', page)
        with self.app.app_context():
            self.assertFalse(User.query.filter_by(email='writer@example.com').one().confirmed)

    def test_unconfigured_service_preserves_legacy_registration(self):
        self.app.config.update(LISS_MAIL_PROVIDER='brevo', LISS_MAIL_API_KEY='')
        page = self.client.get('/auth/register').get_data(as_text=True)
        self.assertIn('邮箱注册与邮件找回暂未开通', page)
        self.assertIn('name="username"', page)
        self.assertNotIn('name="email"', page)
        self.assertEqual(self.submit('/auth/register', {'username': 'still_works',
            'password': 'Good-password-123', 'password2': 'Good-password-123'}).status_code, 302)
        self.assertNotIn('liss_mail_outbox', self.app.extensions)

    def test_registration_resend_and_reset_require_csrf(self):
        self.client.get('/auth/register')
        self.assertEqual(self.client.post('/auth/register', data={'email': 'a@example.com'}).status_code, 400)
        self.register()
        self.assertEqual(self.client.post('/auth/confirm').status_code, 400)
        self.assertEqual(self.client.get('/auth/confirm').status_code, 405)
        self.assertEqual(self.client.post('/auth/reset', data={'email': 'writer@example.com'}).status_code, 400)

    def test_outbox_is_never_available_in_non_testing_mode(self):
        with self.app.app_context():
            self.app.testing = False
            try:
                self.assertFalse(email_accounts_enabled())
            finally:
                self.app.testing = True

    def test_confirmation_resend_is_rate_limited_and_cannot_be_triggered_by_get(self):
        self.app.config['PUBLIC_MAIL_COOLDOWN_SECONDS'] = 60
        self.register()
        self.assertEqual(self.client.get('/auth/confirm').status_code, 405)
        self.assertEqual(len(self.app.extensions['liss_mail_outbox']), 1)
        response = self.submit('/auth/confirm', token_path='/auth/unconfirmed')
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response.headers)
        self.assertEqual(len(self.app.extensions['liss_mail_outbox']), 1)

    def test_ip_mail_quota_and_unauthenticated_confirmation(self):
        self.app.config['PUBLIC_MAIL_IP_LIMIT'] = 1
        self.assertEqual(self.submit('/auth/reset', {'email': 'missing@example.com'}).status_code, 302)
        response = self.submit('/auth/reset', {'email': 'another@example.com'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.app.test_client().get('/auth/confirm/invalid').status_code, 302)

    def test_mail_transport_builds_brevo_and_resend_requests_and_handles_failure(self):
        self.app.config.update(LISS_MAIL_API_KEY='test-placeholder-key',
            LISS_MAIL_SENDER_EMAIL='liss@example.com')
        fake = MagicMock()
        fake.__enter__.return_value.status = 201
        with self.app.test_request_context('/'):
            for provider in ['brevo', 'resend']:
                self.app.config['LISS_MAIL_PROVIDER'] = provider
                with patch('app.transactional_mail.urlopen', return_value=fake) as transport:
                    result = deliver_account_email('reader@example.com', 'Test', 'auth/email/account_action',
                        user=type('Recipient', (), {'display_name': 'Reader'})(),
                        heading='Test', action='Test', link='https://liss.example.com/a', explanation='Test')
                    self.assertTrue(result.accepted)
                    request = transport.call_args.args[0]
                    self.assertTrue(request.full_url.startswith('https://api.'))
                    self.assertEqual(transport.call_args.kwargs['timeout'], 10)
                    payload = json.loads(request.data)
                    self.assertIn('subject', payload)
            with patch('app.transactional_mail.urlopen', side_effect=URLError('simulated private failure')):
                result = deliver_account_email('reader@example.com', 'Test', 'auth/email/account_action',
                    user=type('Recipient', (), {'display_name': 'Reader'})(), heading='Test', action='Test',
                    link='https://liss.example.com/a', explanation='Test')
                self.assertFalse(result.accepted)
                self.assertEqual(result.reason, 'unavailable')


if __name__ == '__main__':
    unittest.main()
