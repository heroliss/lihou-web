"""Explicit guide publication preserves real identities, articles and replies."""
from hashlib import sha256
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from sqlalchemy import inspect, text

from app import create_app, db
from app.models import Comment, Post, User
from app.writing_guide import (GUIDE_KEY, GUIDE_SOURCE, GUIDE_TITLE,
    WritingGuideError, WritingGuidePublication, get_published_writing_guide,
    publish_writing_guide, publish_writing_guide_command)


class WritingGuideTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict('os.environ', {
            'SECRET_KEY': 'writing-guide-tests-only-' * 3,
            'LISS_DATA_DIR': self.directory.name, 'LIHOU_PROXY_HOPS': '0'})
        self.environment.start()
        self.app = create_app('public-demo')
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.app.cli.add_command(publish_writing_guide_command)
        self.context = self.app.app_context()
        self.context.push()
        self.sandbox = self.app.extensions['public_demo']
        with self.sandbox.exclusive():
            self.sandbox.prepare()
        self.database = Path(db.engine.url.database)

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.environment.stop()
        self.directory.cleanup()

    def snapshot(self):
        return {name: [tuple(row) for row in db.session.execute(
            text('SELECT * FROM "' + name + '" ORDER BY rowid'))]
            for name in ('users', 'roles', 'posts', 'comments', 'follows', 'public_demo_state')}

    def backups(self):
        return list(self.database.parent.glob('before-writing-guide-*.sqlite'))

    def test_dry_run_checks_author_but_changes_no_bytes_tables_posts_or_backups(self):
        WritingGuidePublication.__table__.drop(bind=db.engine, checkfirst=True)
        db.session.remove()
        before = sha256(self.database.read_bytes()).hexdigest()
        snapshot = self.snapshot()
        result = publish_writing_guide(dry_run=True)
        self.assertEqual(result['status'], 'would-create')
        self.assertEqual(result['author_username'], 'demo')
        self.assertEqual(self.snapshot(), snapshot)
        self.assertEqual(sha256(self.database.read_bytes()).hexdigest(), before)
        self.assertFalse(inspect(db.engine).has_table('site_guide_publications'))
        self.assertEqual(self.backups(), [])

    def test_publish_adds_one_real_system_article_and_preserves_every_existing_row(self):
        snapshot = self.snapshot()
        result = publish_writing_guide()
        self.assertEqual(result['status'], 'created')
        post = db.session.get(Post, result['post_id'])
        self.assertTrue(post.author.is_system)
        self.assertEqual(post.author.username, 'demo')
        self.assertEqual(post.body, GUIDE_SOURCE.read_text(encoding='utf-8').strip())
        for table, old_rows in snapshot.items():
            current = self.snapshot()[table]
            self.assertEqual(current[:-1] if table == 'posts' else current, old_rows)
        record = db.session.get(WritingGuidePublication, GUIDE_KEY)
        self.assertEqual(record.post_id, post.id)
        self.assertEqual(get_published_writing_guide().id, post.id)
        self.assertEqual(len(self.backups()), 1)
        with closing(sqlite3.connect(result['backup'])) as original:
            self.assertEqual(original.execute('SELECT COUNT(*) FROM posts').fetchone()[0], len(snapshot['posts']))
            self.assertEqual(original.execute('PRAGMA quick_check').fetchone()[0], 'ok')

    def test_repeated_publication_keeps_article_id_edits_timestamps_and_comments(self):
        result = publish_writing_guide()
        guide = db.session.get(Post, result['post_id'])
        guide.body += '\n\n作者以后手动补充的内容。'
        reader = User(username='guide_reader', email=None, password='test-password',
                      account_kind='account', confirmed=True)
        db.session.add(reader)
        db.session.flush()
        db.session.add(Comment(body='真实读者的回复', post=guide, author=reader))
        db.session.commit()
        before = self.snapshot()
        for dry_run in (False, True, False):
            again = publish_writing_guide(dry_run=dry_run)
            self.assertEqual(again['status'], 'already-published')
            self.assertEqual(again['post_id'], guide.id)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.backups()), 1)

    def test_readers_matching_title_and_body_are_never_reused_or_overwritten(self):
        reader = User(username='copied_guide', email=None, password='test-password',
                      account_kind='account', confirmed=True)
        copied = Post(body=GUIDE_SOURCE.read_text(encoding='utf-8').strip(), author=reader)
        db.session.add_all([reader, copied])
        db.session.commit()
        copied_id, copied_body = copied.id, copied.body
        result = publish_writing_guide()
        self.assertNotEqual(result['post_id'], copied_id)
        self.assertEqual(db.session.get(Post, copied_id).body, copied_body)
        self.assertEqual(db.session.get(Post, copied_id).author_id, reader.id)

    def test_non_system_or_unrecognized_site_author_is_rejected_without_writes(self):
        author = User.query.filter_by(username='demo').one()
        for field, value in (('account_kind', 'guest'), ('account_kind', 'account'),
                             ('email', 'reader@example.com'), ('username', 'renamed_author')):
            with self.subTest(field=field, value=value):
                original = getattr(author, field)
                setattr(author, field, value)
                db.session.commit()
                snapshot = self.snapshot()
                with self.assertRaises(WritingGuideError):
                    publish_writing_guide()
                self.assertEqual(self.snapshot(), snapshot)
                self.assertEqual(self.backups(), [])
                setattr(author, field, original)
                db.session.commit()

    def test_published_guide_changed_to_reader_author_is_never_replaced(self):
        result = publish_writing_guide()
        reader = User(username='different_author', email=None, password='test-password',
                      account_kind='account', confirmed=True)
        db.session.add(reader)
        db.session.flush()
        guide = db.session.get(Post, result['post_id'])
        guide.author_id = reader.id
        db.session.commit()
        snapshot = self.snapshot()
        with self.assertRaisesRegex(WritingGuideError, 'author changed'):
            publish_writing_guide()
        self.assertIsNone(get_published_writing_guide())
        self.assertEqual(self.snapshot(), snapshot)
        self.assertEqual(len(self.backups()), 1)

    def test_missing_published_article_is_not_recreated(self):
        result = publish_writing_guide()
        db.session.execute(text('DELETE FROM posts WHERE id=:id'), {'id': result['post_id']})
        db.session.commit()
        snapshot = self.snapshot()
        with self.assertRaisesRegex(WritingGuideError, 'removed'):
            publish_writing_guide()
        self.assertIsNone(get_published_writing_guide())
        self.assertEqual(self.snapshot(), snapshot)

    def test_fixed_route_is_read_only_before_publication_and_redirects_after(self):
        WritingGuidePublication.__table__.drop(bind=db.engine, checkfirst=True)
        self.assertIsNone(get_published_writing_guide())
        snapshot = self.snapshot()
        client = self.app.test_client()
        self.assertEqual(client.get('/writing-guide/').status_code, 404)
        self.assertEqual(self.snapshot(), snapshot)
        self.assertFalse(inspect(db.engine).has_table('site_guide_publications'))
        result = publish_writing_guide()
        response = client.get('/writing-guide/?next=https://example.test/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/post/' + str(result['post_id']))
        self.assertIn(GUIDE_TITLE, client.get(response.location).get_data(as_text=True))

    def test_cli_dry_run_and_idempotent_publish_are_explicit(self):
        runner = self.app.test_cli_runner()
        first = runner.invoke(args=['publish-writing-guide', '--dry-run'])
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(json.loads(first.output)['status'], 'would-create')
        self.assertEqual(Post.query.count(), 3)
        created = runner.invoke(args=['publish-writing-guide'])
        self.assertEqual(created.exit_code, 0, created.output)
        self.assertEqual(json.loads(created.output)['status'], 'created')
        repeated = runner.invoke(args=['publish-writing-guide'])
        self.assertEqual(repeated.exit_code, 0, repeated.output)
        self.assertEqual(json.loads(repeated.output)['status'], 'already-published')
        self.assertEqual(Post.query.count(), 4)

    def test_markdown_examples_and_illustration_match_real_rendering(self):
        result = publish_writing_guide()
        body = db.session.get(Post, result['post_id']).body_html
        for snippet in ('<h1>', '<h2>', '<strong>', '- 做了什么', '<pre><code',
                        'href="https://heroliss.github.io/liss-works/projects/"',
                        'src="https://liss.pythonanywhere.com/static/writing-guide.svg"'):
            self.assertIn(snippet, body)
        source = GUIDE_SOURCE.read_text(encoding='utf-8')
        self.assertEqual(body.count('<h2>'), 4)
        self.assertNotIn('&amp;quot;', body)
        self.assertIn('10 篇文章', source)
        self.assertIn('注册账号不限制篇数', source)
        self.assertIn('邮箱始终可选', source)
        self.assertNotIn('邮件已启用', source)
        image = Path(self.app.static_folder) / 'writing-guide.svg'
        svg = ElementTree.fromstring(image.read_text(encoding='utf-8'))
        self.assertEqual(svg.attrib['viewBox'], '0 0 1000 300')
        self.assertNotIn('script', image.read_text(encoding='utf-8').lower())

    def test_rejects_other_runtime_modes_without_mutation(self):
        with patch.dict(self.app.extensions, {}, clear=True):
            with self.assertRaisesRegex(WritingGuideError, 'prepared public-demo'):
                publish_writing_guide()
        self.assertEqual(Post.query.count(), 3)

    def test_backup_failure_never_publishes_or_rewrites_existing_data(self):
        snapshot = self.snapshot()
        with patch('app.writing_guide.backup_database', side_effect=OSError('No backup space')):
            with self.assertRaisesRegex(OSError, 'backup space'):
                publish_writing_guide()
        self.assertEqual(self.snapshot(), snapshot)
        self.assertEqual(WritingGuidePublication.query.count(), 0)

    def test_unrelated_pending_edits_are_not_accidentally_committed(self):
        author = User.query.filter_by(username='demo').one()
        original = author.name
        author.name = 'An unrelated pending change'
        with self.assertRaisesRegex(WritingGuideError, 'pending changes'):
            publish_writing_guide()
        db.session.rollback()
        self.assertEqual(author.name, original)
        self.assertEqual(Post.query.count(), 3)

    def test_unprepared_dry_run_and_lookup_never_create_a_database(self):
        unprepared = Path(self.directory.name) / 'unprepared'
        with patch.dict('os.environ', {'LISS_DATA_DIR': str(unprepared)}):
            app = create_app('public-demo')
        with app.app_context():
            database = Path(db.engine.url.database)
            self.assertFalse(database.exists())
            self.assertIsNone(get_published_writing_guide())
            with self.assertRaisesRegex(WritingGuideError, 'Prepare site storage'):
                publish_writing_guide(dry_run=True)
            self.assertFalse(database.exists())
            db.session.remove()
            db.engine.dispose()

    def test_management_wrapper_supports_explicit_guide_command_without_implicit_publish(self):
        root = Path(__file__).resolve().parents[1]
        settings = Path(self.directory.name) / 'cli-settings.json'
        settings.write_text(json.dumps({'SECRET_KEY': 'guide-cli-test-only-' * 4}), encoding='utf-8')
        cli_data = Path(self.directory.name) / 'cli-data'
        command = [sys.executable, str(root / 'deploy/manage_public.py'),
                   '--settings', str(settings), '--data-dir', str(cli_data)]
        environment = dict(os.environ, PYTHONIOENCODING='utf-8')
        def run(*arguments):
            completed = subprocess.run(command + list(arguments), cwd=root, env=environment,
                                       capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            return completed.stdout
        run('prepare-public-demo')
        self.assertEqual(json.loads(run('publish-writing-guide', '--dry-run'))['status'], 'would-create')
        run('prepare-public-demo')
        with closing(sqlite3.connect(cli_data / 'public-demo.sqlite')) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM posts').fetchone()[0], 3)
        created = json.loads(run('publish-writing-guide'))
        self.assertEqual(created['status'], 'created')
        self.assertEqual(json.loads(run('publish-writing-guide'))['post_id'], created['post_id'])


if __name__ == '__main__':
    unittest.main()
