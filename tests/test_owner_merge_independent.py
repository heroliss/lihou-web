"""Independent owner migration acceptance: fresh fixtures, public CLI and HTTP.

No production data, browser cookies, actual addresses or outbound mail are used.
"""
from contextlib import closing
import html
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import create_app, db
from app.admin import AdminAudit, AdminConfirmation
from app.community import ProjectEntry
from app.models import Comment, Follow, Post, Role, User
from app.writing_guide import get_published_writing_guide, publish_writing_guide


class OwnerMergeIndependentTests(unittest.TestCase):
    password = 'Independent-owner-only-2026'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict('os.environ', {
            'SECRET_KEY': 'owner-merge-independent-tests-only-' * 3,
            'LISS_DATA_DIR': self.directory.name,
            'LIHOU_PROXY_HOPS': '0',
            'LISS_EMAIL_ACCOUNTS': '0',
        })
        self.environment.start()
        self.app = create_app('public-demo')
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False,
                               PUBLIC_DEMO_AUTH_LIMIT=500,
                               PUBLIC_DEMO_WRITE_LIMIT=500,
                               MAIL_SUPPRESS_SEND=True)
        self.anonymous = self.app.test_client()
        self.assertEqual(self.anonymous.get('/').status_code, 200)
        with self.app.app_context():
            self.database = Path(db.engine.url.database)
            ordinary = Role.query.filter_by(default=True).one()
            admin = Role.query.filter_by(permissions=0xff).one()
            account = User(username='lissbliss', name='Liss',
                email='owner-fixture@example.test', password=self.password,
                account_kind='account', role=admin, confirmed=True,
                location='Fixture location', about_me='Owner fixture biography')
            reader = User(username='independent_reader', name='Reader',
                password=self.password, account_kind='account',
                role=ordinary, confirmed=True)
            outsider = User(username='independent_outsider', name='Other',
                password=self.password, account_kind='account',
                role=ordinary, confirmed=True)
            db.session.add_all([account, reader, outsider])
            db.session.flush()
            self.account_id, self.reader_id, self.outsider_id = account.id, reader.id, outsider.id
            legacy = User.query.filter_by(username='demo').one()
            self.legacy_id = legacy.id
            legacy.password = 'Known-legacy-password-2026'
            self.legacy_hash = legacy.password_hash
            self.account_credentials = (account.email, account.password_hash,
                                        account.confirmed, account.session_version)
            self.legacy_version = legacy.session_version
            account.follow(reader)
            legacy.follow(reader)
            reader.follow(account)
            reader.follow(legacy)
            outsider.follow(legacy)
            legacy.follow(outsider)
            legacy.follow(account)
            account.follow(legacy)
            post = Post(body='# Owner fixture article\n\nOriginal **body**.', author=account)
            hidden = Post(body='# Hidden legacy article', author=legacy, hidden=True)
            db.session.add_all([post, hidden])
            db.session.flush()
            self.account_post_id, self.hidden_post_id = post.id, hidden.id
            db.session.add(ProjectEntry(post=hidden, project_slug='outpost'))
            db.session.add(Comment(body='Legacy hidden comment', author=legacy,
                                   post=post, disabled=True))
            db.session.add(Comment(body='Account comment', author=account,
                                   post=hidden, disabled=False))
            db.session.add(AdminAudit(actor_id=legacy.id, actor_name='Original actor',
                action='fixture.action', target_type='user', target_id=legacy.id,
                target_label='Original label', details='{"fixture":true}'))
            db.session.commit()
            guide = publish_writing_guide()
            self.guide_id = guide['post_id']
            self.content_before = self.content_snapshot()
            self.old_account_token = account.generate_account_token('reset-password')
            self.old_legacy_token = legacy.generate_account_token('reset-password')
        self.account_client = self.app.test_client()
        self.reader_client = self.app.test_client()
        self.login(self.account_client, 'lissbliss')
        self.login(self.reader_client, 'independent_reader')

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.environment.stop()
        self.directory.cleanup()

    def content_snapshot(self):
        return {
            'posts': list(db.session.execute(text('SELECT id,body,body_html,timestamp,hidden FROM posts ORDER BY id'))),
            'comments': list(db.session.execute(text('SELECT id,body,body_html,timestamp,disabled,post_id FROM comments ORDER BY id'))),
            'projects': list(db.session.execute(text('SELECT * FROM project_entries ORDER BY post_id'))),
            'guide': list(db.session.execute(text('SELECT * FROM site_guide_publications ORDER BY guide_key'))),
        }

    def snapshot(self):
        tables = [row[0] for row in db.session.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"))]
        return {table: [tuple(row) for row in db.session.execute(text(
            'SELECT * FROM "' + table + '" ORDER BY rowid'))] for table in tables}

    def csrf(self, client, path='/'):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return html.unescape(match.group(1))

    def post(self, client, path, data=None, token_path='/'):
        return client.post(path, data={**(data or {}),
            'csrf_token': self.csrf(client, token_path)})

    def login(self, client, username, password=None):
        return self.post(client, '/auth/login',
            {'username': username, 'password': password or self.password}, '/auth/login')

    def merge(self, apply=True, source='lissbliss'):
        # Kept at the public command boundary so this suite does not reuse the
        # migration's own planning, validation or FK-rewriting helpers.
        args = ['merge-site-owner', '--username', source, '--new-username', 'Liss']
        if apply:
            args += ['--apply']
        return self.app.test_cli_runner().invoke(args=args)

    def successful_merge(self):
        result = self.merge()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn(self.password, result.output)
        self.assertNotIn('Known-legacy-password-2026', result.output)
        return result

    def test_dry_run_preserves_all_rows_and_creates_no_merge_backup(self):
        with self.app.app_context():
            before = self.snapshot()
        backups = set(self.database.parent.glob('*.sqlite'))
        result = self.merge(apply=False)
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(self.database.parent.glob('*.sqlite')), backups)

    def test_account_id_credentials_and_all_content_ids_are_preserved(self):
        self.successful_merge()
        with self.app.app_context():
            owner = db.session.get(User, self.account_id)
            self.assertEqual(owner.username, 'Liss')
            self.assertTrue(owner.is_registered and owner.is_administrator() and owner.is_site_owner)
            self.assertFalse(owner.disabled)
            self.assertEqual((owner.email, owner.password_hash, owner.confirmed,
                              owner.session_version), self.account_credentials)
            self.assertEqual(self.content_snapshot(), self.content_before)
            self.assertEqual(User.query.filter_by(is_site_owner=True).count(), 1)
            self.assertEqual(Post.query.filter(Post.author_id == self.legacy_id).count(), 0)
            self.assertEqual(Comment.query.filter(Comment.author_id == self.legacy_id).count(), 0)
            self.assertEqual(db.session.get(Post, self.hidden_post_id).author_id, owner.id)
            self.assertTrue(db.session.get(Post, self.hidden_post_id).hidden)
            self.assertEqual(ProjectEntry.query.filter_by(post_id=self.hidden_post_id).one().project_slug, 'outpost')

    def test_bidirectional_follows_deduplicate_and_keep_one_owner_self_follow(self):
        self.successful_merge()
        with self.app.app_context():
            pairs = {(f.follower_id, f.followed_id) for f in Follow.query.all()}
            self.assertEqual(pairs, {
                (self.account_id, self.account_id),
                (self.reader_id, self.reader_id),
                (self.outsider_id, self.outsider_id),
                (self.account_id, self.reader_id), (self.reader_id, self.account_id),
                (self.account_id, self.outsider_id), (self.outsider_id, self.account_id),
            })
            self.assertEqual(len(pairs), Follow.query.count())

    def test_writing_guide_marker_and_route_follow_real_owner(self):
        self.successful_merge()
        with self.app.app_context():
            guide = get_published_writing_guide()
            self.assertIsNotNone(guide)
            self.assertEqual((guide.id, guide.author_id), (self.guide_id, self.account_id))
            before = self.content_snapshot()
            again = publish_writing_guide()
            self.assertEqual(again['status'], 'already-published')
            self.assertEqual(again['post_id'], self.guide_id)
            self.assertEqual(self.content_snapshot(), before)
        response = self.anonymous.get('/writing-guide/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/post/' + str(self.guide_id), response.location)

    def test_prepare_and_new_app_do_not_reseed_legacy_author_or_content(self):
        self.successful_merge()
        with self.app.app_context():
            before = self.snapshot()
            sandbox = self.app.extensions['public_demo']
            sandbox.initialized = False
            with sandbox.exclusive():
                sandbox.prepare()
            self.assertEqual(self.snapshot(), before)
            db.session.remove()
        fresh = create_app('public-demo')
        fresh.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.assertEqual(fresh.test_client().get('/').status_code, 200)
        with fresh.app_context():
            self.assertEqual(User.query.count(), len(before['users']))
            self.assertEqual(self.content_snapshot(), self.content_before)
            db.session.remove()
            db.engine.dispose()

    def test_legitimate_session_and_account_token_stay_usable(self):
        self.successful_merge()
        self.assertEqual(self.account_client.get('/admin/').status_code, 200)
        self.assertEqual(self.reader_client.get('/edit-profile').status_code, 200)
        with self.account_client.session_transaction() as session:
            self.assertEqual(session['public_demo_uid'], str(self.account_id))
        with self.app.app_context():
            loaded = User.load_account_token(self.old_account_token, 'reset-password')
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded[0].id, self.account_id)
            self.assertIsNone(User.load_account_token(self.old_legacy_token, 'reset-password'))

    def test_canonical_old_alias_and_email_login_reach_same_account(self):
        self.successful_merge()
        for username in ('Liss', 'liss', 'LISS', 'lIsS', 'lissbliss', 'LISSBLISS', 'owner-fixture@example.test'):
            with self.subTest(username=username):
                client = self.app.test_client()
                self.assertEqual(self.login(client, username).status_code, 302)
                self.assertEqual(client.get('/admin/').status_code, 200)
                with client.session_transaction() as session:
                    self.assertEqual(session['public_demo_uid'], str(self.account_id))

    def test_legacy_system_password_and_demo_alias_never_authenticate(self):
        self.successful_merge()
        with self.app.app_context():
            old = db.session.get(User, self.legacy_id)
            self.assertTrue(old.disabled)
            self.assertEqual(old.merged_into_id, self.account_id)
            self.assertGreater(old.session_version, self.legacy_version)
            self.assertEqual(old.password_hash, self.legacy_hash)
            self.assertTrue(old.verify_password('Known-legacy-password-2026'))
        for password in ('Known-legacy-password-2026', self.password):
            client = self.app.test_client()
            self.assertEqual(self.login(client, 'demo', password).status_code, 200)
            self.assertEqual(client.get('/admin/').status_code, 403)
            with client.session_transaction() as session:
                self.assertNotIn('public_demo_uid', session)

    def test_alias_profile_lists_and_follow_mutations_resolve_to_owner(self):
        self.successful_merge()
        for old_name in ('demo', 'lissbliss'):
            for route in ('user', 'followers', 'followed-by'):
                with self.subTest(old_name=old_name, route=route):
                    response = self.anonymous.get(f'/{route}/{old_name}?page=1')
                    self.assertIn(response.status_code, {301, 302, 307, 308})
                    self.assertIn(f'/{route}/Liss', response.location)
                    self.assertEqual(self.anonymous.get(response.location).status_code, 200)
            response = self.post(self.reader_client, f'/unfollow/{old_name}')
            self.assertEqual(response.status_code, 302)
            with self.app.app_context():
                self.assertIsNone(db.session.get(Follow, (self.reader_id, self.account_id)))
            response = self.post(self.reader_client, f'/follow/{old_name}')
            self.assertEqual(response.status_code, 302)
            with self.app.app_context():
                self.assertIsNotNone(db.session.get(Follow, (self.reader_id, self.account_id)))

    def test_old_names_cannot_be_registered_or_claimed_by_guest_upgrade(self):
        self.successful_merge()
        for name in ('liss', 'LISS', 'lissbliss', 'LISSBLISS', 'demo', 'DEMO'):
            with self.subTest(name=name):
                client = self.app.test_client()
                response = self.post(client, '/auth/register', {
                    'username': name, 'password': self.password,
                    'password2': self.password, 'nickname': 'Impersonator',
                }, '/auth/register')
                self.assertEqual(response.status_code, 200)
                with client.session_transaction() as session:
                    self.assertNotIn('public_demo_uid', session)
        guest = self.app.test_client()
        self.assertEqual(self.post(guest, '/welcome', token_path='/welcome').status_code, 302)
        with guest.session_transaction() as session:
            guest_id = int(session['public_demo_uid'])
        self.assertEqual(self.post(guest, '/auth/register', {
            'username': 'LISSBLISS', 'password': self.password,
            'password2': self.password,
        }, '/auth/register').status_code, 200)
        with self.app.app_context():
            self.assertTrue(db.session.get(User, guest_id).is_visitor)

    def test_pending_admin_confirmation_is_revoked_without_logging_out_owner(self):
        preview = self.post(self.account_client, '/admin/preview', {
            'kind': 'post', 'action': 'hide', 'target_id': str(self.account_post_id),
        }, '/admin/')
        self.assertEqual(preview.status_code, 200)
        match = re.search(r'name="token"[^>]*value="([^"]+)"', preview.get_data(as_text=True))
        self.assertIsNotNone(match)
        token = html.unescape(match.group(1))
        self.successful_merge()
        self.assertEqual(self.account_client.get('/admin/').status_code, 200)
        response = self.post(self.account_client, '/admin/apply', {'token': token}, '/admin/')
        self.assertIn(response.status_code, {400, 403, 409})
        with self.app.app_context():
            self.assertFalse(db.session.get(Post, self.account_post_id).hidden)
            self.assertEqual(AdminConfirmation.query.filter_by(consumed=False).count(), 0)

    def test_second_apply_is_idempotent_and_audit_history_is_unchanged(self):
        self.successful_merge()
        with self.app.app_context():
            before = self.snapshot()
            old_audit = AdminAudit.query.filter_by(action='fixture.action').one()
            self.assertEqual((old_audit.actor_id, old_audit.actor_name, old_audit.target_id,
                              old_audit.target_label, old_audit.details),
                             (self.legacy_id, 'Original actor', self.legacy_id,
                              'Original label', '{"fixture":true}'))
        before_backups = set(self.database.parent.glob('*.sqlite'))
        result = self.merge()
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(self.database.parent.glob('*.sqlite')), before_backups)

    def test_canonical_username_conflict_is_rejected_without_partial_changes(self):
        with self.app.app_context():
            conflict = User(username='LISS', password=self.password,
                            account_kind='account', confirmed=True)
            db.session.add(conflict)
            db.session.commit()
            before = self.snapshot()
        result = self.merge()
        self.assertNotEqual(result.exit_code, 0)
        with self.app.app_context():
            self.assertEqual(self.snapshot(), before)

    def test_success_has_premerge_snapshot_and_valid_foreign_keys(self):
        before = set(self.database.parent.glob('*.sqlite'))
        self.successful_merge()
        created = set(self.database.parent.glob('*.sqlite')) - before
        self.assertTrue(created)
        found_original = False
        for path in created:
            with closing(sqlite3.connect(path)) as snapshot:
                row = snapshot.execute('SELECT username FROM users WHERE id=?',
                                       (self.account_id,)).fetchone()
                if row and row[0] == 'lissbliss':
                    found_original = True
                    self.assertGreater(snapshot.execute('SELECT COUNT(*) FROM posts WHERE author_id=?',
                                                        (self.legacy_id,)).fetchone()[0], 0)
        self.assertTrue(found_original)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute('PRAGMA quick_check').fetchone(), ('ok',))
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_alias_collision_refuses_to_capture_another_account(self):
        from app.models import UsernameAlias
        with self.app.app_context():
            db.session.add(UsernameAlias(username='liss', user_id=self.reader_id, login_allowed=True))
            db.session.commit()
            before = self.snapshot()
        result = self.merge()
        self.assertNotEqual(result.exit_code, 0)
        with self.app.app_context():
            self.assertEqual(self.snapshot(), before)

    def test_different_explicit_owner_is_never_silently_replaced(self):
        with self.app.app_context():
            reader = db.session.get(User, self.reader_id)
            reader.is_site_owner = True
            db.session.commit()
            before = self.snapshot()
        result = self.merge()
        self.assertNotEqual(result.exit_code, 0)
        with self.app.app_context():
            self.assertEqual(self.snapshot(), before)

    def test_failed_backup_prevents_any_merge_write(self):
        with self.app.app_context():
            before = self.snapshot()
        with patch('app.owner_identity.backup_database', side_effect=OSError('fixture backup failure')):
            result = self.merge()
        self.assertNotEqual(result.exit_code, 0)
        with self.app.app_context():
            self.assertEqual(self.snapshot(), before)

    def test_failure_after_fk_rewrites_rolls_back_every_change_and_hides_sql_details(self):
        with self.app.app_context():
            before = self.snapshot()
        with patch('app.admin.record_audit', side_effect=SQLAlchemyError('fixture-sensitive-diagnostics')):
            result = self.merge()
        self.assertNotEqual(result.exit_code, 0)
        self.assertNotIn('fixture-sensitive-diagnostics', result.output)
        with self.app.app_context():
            self.assertEqual(self.snapshot(), before)

    def test_existing_legacy_cookie_is_revoked_and_cannot_upgrade_into_owner(self):
        legacy_client = self.app.test_client()
        # An intentionally synthetic pre-migration system cookie proves that
        # even an old signed system identity is not promoted by the merge.
        self.assertEqual(legacy_client.get('/').status_code, 200)
        with self.account_client.session_transaction() as real_session:
            copied_test_context = dict(real_session)
        with legacy_client.session_transaction() as session:
            session.update(copied_test_context)
            session['_user_id'] = str(self.legacy_id)
            session['public_demo_uid'] = str(self.legacy_id)
            session.pop('public_account_credential', None)
            session['public_guest_version'] = self.legacy_version
        self.successful_merge()
        self.assertEqual(legacy_client.get('/admin/').status_code, 403)
        with legacy_client.session_transaction() as session:
            self.assertNotIn('public_demo_uid', session)
            self.assertEqual(session.get('disabled_identity'), self.legacy_id)
        self.assertEqual(self.post(legacy_client, '/auth/register', {
            'username': 'historical_identity_escape', 'password': self.password,
            'password2': self.password,
        }, '/auth/login').status_code, 403)

