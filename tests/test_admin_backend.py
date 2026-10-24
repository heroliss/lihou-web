"""Successful admin workflows, CLI safety and additive migration acceptance."""
from contextlib import closing
import html
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import create_app, db
from app.admin import AdminAudit, SiteSettings
from app.models import Comment, Post, Role, User
from app.public_schema import migrate_admin_schema
from config import TestingConfig, config
from flask_migrate import Migrate, stamp, upgrade


class AdminBackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict('os.environ', {
            'SECRET_KEY': 'temporary-admin-workflow-test-' * 3,
            'LISS_DATA_DIR': self.directory.name, 'LIHOU_PROXY_HOPS': '0',
            'LISS_EMAIL_ACCOUNTS': '0'})
        self.environment.start()
        self.app = create_app('public-demo')
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False,
                               PUBLIC_DEMO_AUTH_LIMIT=200, PUBLIC_DEMO_WRITE_LIMIT=200)
        self.client = self.app.test_client()
        self.client.get('/')
        with self.app.app_context():
            ordinary = Role.query.filter_by(default=True).one()
            user = User(username='workflow_owner', name='Test Owner',
                password='temporary-test-password-24', role=ordinary, account_kind='account',
                email='admin-test@example.invalid', confirmed=True)
            db.session.add(user)
            db.session.commit()
            self.uid, self.password_hash = user.id, user.password_hash
        result = self.app.test_cli_runner().invoke(args=['grant-admin', '--username', 'workflow_owner'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.post('/auth/login', {'username': 'workflow_owner', 'password': 'temporary-test-password-24'}, '/auth/login')

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.environment.stop()
        self.directory.cleanup()

    def csrf(self, path='/admin/'):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return html.unescape(re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True)).group(1))

    def post(self, path, data, token_path='/admin/'):
        return self.client.post(path, data={**data, 'csrf_token': self.csrf(token_path)})

    def change(self, data):
        response = self.post('/admin/preview', data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        token = html.unescape(re.search(r'name="token"[^>]*value="([^"]+)"', response.get_data(as_text=True)).group(1))
        result = self.post('/admin/apply', {'token': token})
        self.assertEqual(result.status_code, 302)
        return result

    def test_cli_exact_identity_preserves_credentials_and_is_idempotent(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=['grant-admin', '--user-id', str(self.uid)])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('already an administrator', result.output)
        self.assertNotIn('admin-test@example.invalid', result.output)
        self.assertNotIn(self.password_hash, result.output)
        with self.app.app_context():
            user = db.session.get(User, self.uid)
            self.assertEqual(user.password_hash, self.password_hash)
            self.assertEqual(user.email, 'admin-test@example.invalid')
            self.assertEqual(AdminAudit.query.filter_by(action='user.grant_admin').count(), 1)
        for args in [[], ['--username', 'WORKFLOW_OWNER'], ['--username', 'demo'],
                     ['--username', 'workflow_owner', '--user-id', str(self.uid)]]:
            self.assertNotEqual(runner.invoke(args=['grant-admin', *args]).exit_code, 0)

    def test_hide_restore_and_audit_keep_content_and_private_data_out_of_log(self):
        with self.app.app_context():
            post = Post(author_id=self.uid, body='# Private audit body sentinel\n\nNot an audit payload.')
            db.session.add(post)
            db.session.flush()
            comment = Comment(author_id=self.uid, post=post, body='Comment audit body sentinel')
            db.session.add(comment)
            db.session.commit()
            post_id, comment_id = post.id, comment.id
        for kind, identifier in [('post', post_id), ('comment', comment_id)]:
            for action in ['hide', 'restore']:
                response = self.change(dict(kind=kind, action=action, target_id=str(identifier),
                                            return_to='/admin/posts?q=sentinel&status=all'))
                self.assertEqual(response.headers['Location'], '/admin/posts?q=sentinel&status=all')
        with self.app.app_context():
            self.assertEqual(db.session.get(Post, post_id).body, '# Private audit body sentinel\n\nNot an audit payload.')
            self.assertFalse(db.session.get(Post, post_id).hidden)
            self.assertEqual(db.session.get(Comment, comment_id).body, 'Comment audit body sentinel')
            self.assertFalse(db.session.get(Comment, comment_id).disabled)
            logs = '\n'.join(a.details for a in AdminAudit.query.all())
            for forbidden in ['Private audit body sentinel', 'Comment audit body sentinel',
                              'admin-test@example.invalid', self.password_hash]:
                self.assertNotIn(forbidden, logs)
            self.assertEqual(AdminAudit.query.count(), 5)
            self.assertEqual(json.loads(AdminAudit.query.order_by(AdminAudit.id.desc()).first().details)['after'], {'hidden': False})

    def test_paginated_search_filters_and_comment_article_lookup(self):
        with self.app.app_context():
            posts = [Post(author_id=self.uid, body=f'# Search_needle {number}') for number in range(23)]
            db.session.add_all(posts)
            db.session.flush()
            last_id = posts[-1].id
            db.session.add(Comment(author_id=self.uid, post_id=last_id, body='Specific comment result'))
            db.session.commit()
        page = self.client.get('/admin/posts?q=Search_needle').get_data(as_text=True)
        self.assertIn('23', page)
        self.assertIn('page=2', page)
        self.assertIn('Search_needle', self.client.get('/admin/posts?q=Search_needle&page=2').get_data(as_text=True))
        self.assertIn('Specific comment result', self.client.get(f'/admin/comments?q=%23{last_id}').get_data(as_text=True))
        self.assertNotIn('Specific comment result', self.client.get('/admin/comments?q=%23999999').get_data(as_text=True))

    def test_settings_are_effective_public_content_not_arbitrary_configuration(self):
        self.change(dict(kind='settings', action='update', site_name='Workflow Community',
            tagline='A public description', welcome='A thoughtful welcome.', registration_open='1',
            guest_posting_open='1', guest_post_limit='12', SECRET_KEY='must-be-ignored',
            LISS_MAIL_API_KEY='must-be-ignored'))
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('Workflow Community', page)
        self.assertIn('A public description', page)
        self.assertIn('A thoughtful welcome.', page)
        self.assertNotIn('must-be-ignored', page)
        self.assertNotEqual(self.app.config['SECRET_KEY'], 'must-be-ignored')
        with self.app.app_context():
            row = db.session.get(SiteSettings, 1)
            self.assertEqual(row.guest_post_limit, 12)
            self.app.extensions['public_demo'].initialized = False
            with self.app.extensions['public_demo'].exclusive():
                self.app.extensions['public_demo'].prepare()
            self.assertEqual(db.session.get(SiteSettings, 1).site_name, 'Workflow Community')

    def test_account_mail_tokens_do_not_revive_after_suspension(self):
        with self.app.app_context():
            user = db.session.get(User, self.uid)
            token = user.generate_account_token('reset-password')
            self.assertIsNotNone(User.load_account_token(token, 'reset-password'))
            user.disabled, user.session_version = True, 1
            db.session.commit()
            self.assertIsNone(User.load_account_token(token, 'reset-password'))
            user.disabled, user.session_version = False, 2
            db.session.commit()
            self.assertIsNone(User.load_account_token(token, 'reset-password'))

    def test_invalid_identifier_and_pagination_boundaries_do_not_crash(self):
        for q in ['#' + '9' * 99, '#²']:
            for endpoint in ('posts', 'comments', 'users'):
                self.assertEqual(self.client.get('/admin/' + endpoint, query_string={'q': q}).status_code, 200)
        self.assertEqual(self.client.get('/admin/posts?page=' + '9' * 99).status_code, 200)
        for identifier in ['0', '-1', '9223372036854775808', '9' * 100]:
            self.assertEqual(self.post('/admin/preview', dict(kind='post', action='hide', target_id=identifier)).status_code, 400)


class AdminMigrationTests(unittest.TestCase):
    def test_additive_schema_is_backed_up_idempotent_and_preserves_old_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'legacy.sqlite'
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript('''
                    CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT, account_kind TEXT, password_hash TEXT);
                    CREATE TABLE posts (id INTEGER PRIMARY KEY, body TEXT);
                    CREATE TABLE comments (id INTEGER PRIMARY KEY, disabled BOOLEAN);
                    CREATE TABLE roles (id INTEGER PRIMARY KEY);
                    CREATE TABLE follows (id INTEGER PRIMARY KEY);
                    INSERT INTO users VALUES (7, 'existing_writer', NULL, 'account', 'test-placeholder-hash');
                    INSERT INTO posts VALUES (8, 'Retain the original body');
                    INSERT INTO comments VALUES (9, NULL);
                ''')
                connection.commit()
            migrate_admin_schema(database)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute('SELECT id,username,password_hash,disabled,session_version FROM users').fetchall(),
                                 [(7, 'existing_writer', 'test-placeholder-hash', 0, 0)])
                self.assertEqual(connection.execute('SELECT id,body,hidden FROM posts').fetchall(),
                                 [(8, 'Retain the original body', 0)])
                self.assertEqual(connection.execute('SELECT id,disabled FROM comments').fetchall(), [(9, 0)])
                self.assertEqual(connection.execute('PRAGMA quick_check').fetchall(), [('ok',)])
            backups = list(Path(directory).glob('before-admin-*.sqlite'))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as original:
                self.assertEqual(original.execute('SELECT id,body FROM posts').fetchall(), [(8, 'Retain the original body')])
                self.assertIsNone(original.execute('SELECT disabled FROM comments').fetchone()[0])
            migrate_admin_schema(database)
            self.assertEqual(list(Path(directory).glob('before-admin-*.sqlite')), backups)

    def test_normal_cli_alembic_upgrade_preserves_a_pre_console_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'normal.sqlite'
            mode = type('AdminMigrationConfig', (TestingConfig,),
                        {'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + database.as_posix()})
            with patch.dict(config, {'admin-migration-test': mode}):
                app = create_app('admin-migration-test')
            Migrate(app, db)
            migrations = str(Path(__file__).resolve().parents[1] / 'migrations')
            with app.app_context():
                try:
                    upgrade(directory=migrations, revision='20261024_projects')
                    with db.engine.begin() as connection:
                        connection.execute(db.text("INSERT INTO roles (id,name,permissions,\"default\") VALUES (1,'Retained role',255,0)"))
                        connection.execute(db.text("INSERT INTO users (id,username,email,password_hash,role_id,confirmed,account_kind) VALUES (7,'retained_account',NULL,'retained-test-hash',1,1,'account')"))
                        connection.execute(db.text("INSERT INTO posts (id,body,author_id) VALUES (8,'Original normal-mode article',7)"))
                        connection.execute(db.text('INSERT INTO comments (id,disabled,author_id,post_id) VALUES (9,NULL,7,8)'))
                    upgrade(directory=migrations)
                    user = db.session.get(User, 7)
                    self.assertFalse(user.disabled)
                    self.assertEqual(user.session_version, 0)
                    self.assertEqual(user.password_hash, 'retained-test-hash')
                    self.assertFalse(db.session.get(Post, 8).hidden)
                    self.assertEqual(db.session.get(Post, 8).body, 'Original normal-mode article')
                    self.assertFalse(db.session.get(Comment, 9).disabled)
                    db.session.remove()
                    upgrade(directory=migrations)  # Normal CLI is repeatable.
                    self.assertEqual(db.session.get(Post, 8).body, 'Original normal-mode article')
                    tables = set(db.inspect(db.engine).get_table_names())
                    self.assertTrue({'site_settings', 'admin_audit', 'admin_confirmations'} <= tables)
                finally:
                    db.session.remove()
                    db.engine.dispose()

    def test_alembic_accepts_metadata_already_added_by_public_prepare(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict('os.environ', {'SECRET_KEY': 'public-prepared-migration-test-' * 3,
                                          'LISS_DATA_DIR': directory, 'LIHOU_PROXY_HOPS': '0'}):
                app = create_app('public-demo')
            app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
            app.test_client().get('/')
            Migrate(app, db)
            migrations = str(Path(__file__).resolve().parents[1] / 'migrations')
            with app.app_context():
                try:
                    before = [(p.id, p.body, p.author_id) for p in Post.query.order_by(Post.id).all()]
                    db.session.get(SiteSettings, 1).guest_post_limit = 17
                    db.session.commit()
                    db.session.remove()
                    stamp(directory=migrations, revision='20261024_projects')
                    upgrade(directory=migrations)
                    self.assertEqual([(p.id, p.body, p.author_id) for p in Post.query.order_by(Post.id).all()], before)
                    self.assertEqual(db.session.get(SiteSettings, 1).guest_post_limit, 17)
                finally:
                    db.session.remove()
                    db.engine.dispose()


if __name__ == '__main__':
    unittest.main()
