"""Owner consolidation: real credentials, attribution, atomicity and compatibility."""
import html
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from flask import render_template

from app import create_app, db
from app.admin import AdminAudit, AdminConfirmation
from app.community import ProjectEntry
from app.models import Comment, Follow, Post, Role, User, UsernameAlias
from app.owner_identity import get_site_owner, merge_site_owner, OwnerMergeError
from app.public_schema import migrate_owner_schema
from app.writing_guide import get_published_writing_guide, publish_writing_guide


class OwnerMergeBackendTests(unittest.TestCase):
    password = 'Only-for-owner-merge-tests-2026'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict('os.environ', {
            'SECRET_KEY': 'isolated-owner-merge-test-' * 3,
            'LISS_DATA_DIR': self.directory.name, 'LIHOU_PROXY_HOPS': '0',
            'LISS_EMAIL_ACCOUNTS': '0'})
        self.environment.start()
        self.app = create_app('public-demo')
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False,
            PUBLIC_DEMO_AUTH_LIMIT=200, PUBLIC_DEMO_WRITE_LIMIT=200)
        self.client = self.app.test_client()
        self.assertEqual(self.client.get('/').status_code, 200)
        with self.app.app_context():
            source = User.query.filter_by(username='demo').one()
            source.password = self.password
            admin_role = Role.query.filter_by(permissions=0xff).one()
            ordinary_role = Role.query.filter_by(default=True).one()
            owner = User(username='lissbliss', name='Original nickname', password=self.password,
                confirmed=True, email='private-owner@example.invalid', role=admin_role)
            other = User(username='reader', password=self.password,
                confirmed=True, role=ordinary_role)
            db.session.add_all([owner, other])
            db.session.flush()
            self.uid, self.source_id, self.other_id = owner.id, source.id, other.id
            self.original = {name: getattr(owner, name) for name in ('password_hash', 'email',
                'confirmed', 'session_version', 'member_since', 'location', 'about_me', 'avatar_hash')}
            self.original['credential'] = owner.account_credential_fingerprint
            # Exercise two-way duplication and self-follow collapse.
            owner.follow(source)
            source.follow(owner)
            other.follow(source)
            other.follow(owner)
            source.follow(other)
            owner.follow(other)
            post = Post(body='# Owner private article\n\nRetain content.', author=owner, hidden=True)
            db.session.add(post)
            db.session.flush()
            db.session.add(ProjectEntry(post_id=post.id, project_slug='liss-community'))
            db.session.add(Comment(body='Owner private comment', post=post, author=owner, disabled=True))
            db.session.add(AdminAudit(actor_id=source.id, actor_name='Historical actor',
                action='old.action', target_type='user', target_id=source.id,
                target_label='@demo', details='{}'))
            db.session.add(AdminConfirmation(nonce_hash='a'*64, actor_id=owner.id))
            db.session.commit()
            self.pid = post.id
            self.guide_id = publish_writing_guide()['post_id']
            self.before_posts = [(p.id, p.body, p.body_html, p.timestamp, p.hidden) for p in Post.query.all()]
            self.before_comments = [(c.id, c.body, c.body_html, c.timestamp, c.disabled, c.post_id) for c in Comment.query.all()]
        self.runner = self.app.test_cli_runner()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.environment.stop()
        self.directory.cleanup()

    def merge(self, *args):
        result = self.runner.invoke(args=['merge-site-owner', '--username', 'lissbliss', *args])
        self.assertEqual(result.exit_code, 0, result.output)
        return json.loads(result.output)

    def csrf(self, path='/auth/login'):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return html.unescape(re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True)).group(1))

    def login(self, username):
        return self.client.post('/auth/login', data={'username': username, 'password': self.password,
                                                     'csrf_token': self.csrf()})

    def test_default_plan_is_read_only_and_does_not_expose_credentials(self):
        database = Path(self.directory.name) / 'public-demo.sqlite'
        before = database.read_bytes()
        result = self.merge()
        self.assertEqual(result['status'], 'would-merge')
        self.assertEqual(before, database.read_bytes())
        self.assertEqual(list(Path(self.directory.name).glob('before-owner-merge-*')), [])
        for secret in (self.password, self.original['password_hash'], self.original['email']):
            self.assertNotIn(secret, json.dumps(result))

    def test_apply_preserves_real_identity_every_content_field_and_existing_audit(self):
        result = self.merge('--apply')
        self.assertEqual(result['status'], 'merged')
        self.assertTrue(Path(result['backup']).is_file())
        with closing(sqlite3.connect(result['backup'])) as backup:
            self.assertEqual(backup.execute('SELECT username FROM users WHERE id=?', (self.uid,)).fetchone()[0], 'lissbliss')
        with self.app.app_context():
            owner = db.session.get(User, self.uid)
            self.assertEqual((owner.username, owner.name, owner.account_kind), ('Liss', 'Liss', 'account'))
            self.assertTrue(owner.is_site_owner and owner.is_administrator())
            for name, value in self.original.items():
                self.assertEqual(owner.account_credential_fingerprint if name == 'credential' else getattr(owner, name), value)
            self.assertEqual(self.before_posts, [(p.id, p.body, p.body_html, p.timestamp, p.hidden) for p in Post.query.all()])
            self.assertEqual(self.before_comments, [(c.id, c.body, c.body_html, c.timestamp, c.disabled, c.post_id) for c in Comment.query.all()])
            self.assertEqual(Post.query.filter_by(author_id=self.source_id).count(), 0)
            self.assertEqual(Comment.query.filter_by(author_id=self.source_id).count(), 0)
            self.assertEqual(db.session.get(ProjectEntry, self.pid).post_id, self.pid)
            audit = AdminAudit.query.filter_by(action='old.action').one()
            self.assertEqual((audit.actor_id, audit.actor_name, audit.target_id, audit.target_label),
                             (self.source_id, 'Historical actor', self.source_id, '@demo'))
            self.assertEqual(AdminAudit.query.filter_by(action='user.merge_site_owner').count(), 1)
            self.assertEqual(db.session.execute(db.text('PRAGMA foreign_key_check')).all(), [])

    def test_follows_are_moved_in_both_directions_and_deduplicated(self):
        self.merge('--apply')
        with self.app.app_context():
            actual = {(f.follower_id, f.followed_id) for f in Follow.query.all()}
            self.assertEqual(actual, {(self.uid, self.uid), (self.uid, self.other_id),
                                     (self.other_id, self.uid), (self.other_id, self.other_id)})

    def test_real_session_survives_and_both_account_names_can_login(self):
        self.assertEqual(self.login('lissbliss').status_code, 302)
        with self.client.session_transaction() as session:
            original_stamp = session['public_account_credential']
        self.merge('--apply')
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session['public_account_credential'], original_stamp)
            session.clear()
        for username in ['Liss', 'liss', 'LISS', 'lIsS', 'lissbliss', 'LISSBLISS']:
            self.assertEqual(self.login(username).status_code, 302)
            self.assertEqual(self.client.get('/admin/').status_code, 200)
            with self.client.session_transaction() as session:
                self.assertEqual(session['_user_id'], str(self.uid))
                session.clear()
        self.assertEqual(self.login('demo').status_code, 200)
        self.assertEqual(self.client.get('/admin/').status_code, 403)

    def test_old_profiles_and_follow_lists_permanently_redirect(self):
        self.merge('--apply')
        for prefix in ['user', 'followers', 'followed-by']:
            for old in ['demo', 'lissbliss', 'liss', 'LISS']:
                response = self.client.get(f'/{prefix}/{old}?page=2&q=a&q=b')
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers['Location'], f'/{prefix}/Liss?page=2&q=a&q=b')

    def test_old_username_is_reserved_against_registration(self):
        self.merge('--apply')
        response = self.client.post('/auth/register', data=dict(username='LissBliss',
            password=self.password, password2=self.password, csrf_token=self.csrf('/auth/register')))
        self.assertEqual(response.status_code, 200)
        self.assertIn('此用户名已被使用', response.get_data(as_text=True))

    def test_system_tombstone_and_old_confirmation_receipts_cannot_return(self):
        self.merge('--apply')
        with self.app.app_context():
            source = db.session.get(User, self.source_id)
            self.assertTrue(source.disabled)
            self.assertTrue(source.verify_password(self.password))
            self.assertEqual(source.merged_into_id, self.uid)
            self.assertEqual(source.session_version, 1)
            self.assertTrue(AdminConfirmation.query.one().consumed)
            self.assertFalse(db.session.get(UsernameAlias, 'demo').login_allowed)
            self.assertTrue(db.session.get(UsernameAlias, 'lissbliss').login_allowed)

    def test_repeat_apply_is_idempotent_and_prepare_never_reseeds_or_rewrites(self):
        self.merge('--apply')
        second = self.merge('--apply')
        self.assertEqual(second['status'], 'already-merged')
        canonical_repeat = self.runner.invoke(args=['merge-site-owner', '--username', 'Liss', '--apply'])
        self.assertEqual(canonical_repeat.exit_code, 0, canonical_repeat.output)
        self.assertEqual(json.loads(canonical_repeat.output)['status'], 'already-merged')
        self.assertEqual(len(list(Path(self.directory.name).glob('before-owner-merge-*'))), 1)
        with self.app.app_context():
            post = db.session.get(Post, self.guide_id)
            post.body = '# Owner edited the guide'
            db.session.commit()
            sandbox = self.app.extensions['public_demo']
            sandbox.initialized = False
            with sandbox.exclusive():
                sandbox.prepare()
            self.assertEqual(db.session.get(Post, self.guide_id).body, '# Owner edited the guide')
            self.assertEqual(User.query.count(), 3)
            self.assertEqual(get_site_owner().id, self.uid)
            self.assertIsNone(db.session.get(UsernameAlias, 'liss'))
            self.assertEqual(get_published_writing_guide().id, self.guide_id)
            self.assertEqual(publish_writing_guide()['status'], 'already-published')

    def test_unexpected_failure_rolls_back_all_changes_with_safety_backup(self):
        with self.app.app_context(), patch('app.admin.record_audit', side_effect=RuntimeError('injected failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                merge_site_owner(username='lissbliss', apply=True)
            owner, source = db.session.get(User, self.uid), db.session.get(User, self.source_id)
            self.assertEqual(owner.username, 'lissbliss')
            self.assertFalse(owner.is_site_owner)
            self.assertFalse(source.disabled)
            self.assertIsNone(source.merged_into_id)
            self.assertEqual(UsernameAlias.query.count(), 0)
            self.assertEqual(Post.query.filter_by(author_id=source.id).count(), 4)
            self.assertFalse(AdminConfirmation.query.one().consumed)

    def test_conflicting_target_name_refuses_without_backup_or_mutation(self):
        result = self.runner.invoke(args=['merge-site-owner', '--username', 'lissbliss', '--new-username', 'reader', '--apply'])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('another identity', result.output)
        self.assertEqual(list(Path(self.directory.name).glob('before-owner-merge-*')), [])

    def test_owner_stays_admin_and_is_unique_in_management_directory(self):
        self.merge('--apply')
        self.login('liss')
        page = self.client.get('/admin/users').get_data(as_text=True)
        self.assertNotIn('@demo', page)
        self.assertIn('@Liss', page)
        for data in [dict(role='normal', disabled='0'), dict(role='admin', disabled='1')]:
            response = self.client.post('/admin/preview', data=dict(kind='user', target_id=str(self.uid),
                csrf_token=self.csrf('/admin/'), **data))
            self.assertEqual(response.status_code, 400)

    def test_owner_context_does_not_query_unprepared_storage_for_mail_or_offline_render(self):
        offline_dir = Path(self.directory.name) / 'unprepared-mail-app'
        with patch.dict('os.environ', {'LISS_DATA_DIR': str(offline_dir)}):
            offline = create_app('public-demo')
        recipient = type('Recipient', (), {'display_name': 'Reader'})()
        payload = dict(user=recipient, heading='Test', action='Continue',
                       link='https://example.invalid/action', explanation='Test mail')
        with patch('app.owner_identity.get_site_owner', side_effect=AssertionError('Unexpected owner database query')):
            with offline.app_context():
                self.assertIn('Reader', render_template('auth/email/account_action.html', **payload))
            with offline.test_request_context('/'):
                self.assertIn('Reader', render_template('auth/email/account_action.html', **payload))
        with offline.app_context():
            db.session.remove()
            db.engine.dispose()
        self.assertFalse((offline_dir / 'public-demo.sqlite').exists())

    def test_schema_upgrade_is_additive_backed_up_and_idempotent(self):
        database = Path(self.directory.name) / 'old-schema.sqlite'
        with closing(sqlite3.connect(database)) as connection:
            for sql in ['CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT)',
                        'CREATE TABLE roles(id INTEGER PRIMARY KEY)', 'CREATE TABLE posts(id INTEGER PRIMARY KEY)',
                        'CREATE TABLE comments(id INTEGER PRIMARY KEY)', 'CREATE TABLE follows(id INTEGER PRIMARY KEY)']:
                connection.execute(sql)
            connection.execute("INSERT INTO users VALUES(7, 'old-user')")
            connection.commit()
        migrate_owner_schema(database)
        migrate_owner_schema(database)
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute('SELECT id, username, is_site_owner, merged_into_id FROM users').fetchall(),
                             [(7, 'old-user', 0, None)])
            self.assertEqual(connection.execute('PRAGMA foreign_key_list(users)').fetchone()[2], 'users')
        self.assertEqual(len(list(Path(self.directory.name).glob('before-owner-schema-*'))), 1)
