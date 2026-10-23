"""Real project discussions, cross-links, static gallery and persistent mail limits."""
import html
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text

from app import create_app, db
from app.community import PROJECTS, ProjectEntry
from app.models import Post, User


class CommunityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict('os.environ', {'SECRET_KEY': 'community-tests-only-' * 3,
            'LISS_DATA_DIR': self.directory.name, 'LIHOU_PROXY_HOPS': '0'})
        self.env.start()
        self.app = create_app('public-demo')
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = self.app.test_client()
        self.client.get('/')

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.env.stop()
        self.directory.cleanup()

    def csrf(self, path='/welcome', client=None):
        data = (client or self.client).get(path).get_data(as_text=True)
        return html.unescape(re.search(r'name="csrf_token"[^>]*value="([^"]+)"', data).group(1))

    def join(self, slug='outpost'):
        dest = '/projects/' + slug + '/talk/'
        response = self.client.post('/welcome?next=' + dest, data={'csrf_token': self.csrf()})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith(dest))

    def test_all_projects_have_real_empty_discussions_and_return_links(self):
        for slug, project in PROJECTS.items():
            response = self.client.get('/projects/' + slug + '/talk/')
            self.assertEqual(response.status_code, 200)
            page = response.get_data(as_text=True)
            self.assertIn(project[0], page)
            self.assertIn('/works/projects/' + slug + '/', page)
        self.assertEqual(self.client.get('/projects/unknown/talk/').status_code, 404)

    def test_discussion_is_real_article_visible_in_feed_and_original_author_can_edit(self):
        self.join()
        target = '/projects/outpost/talk/'
        response = self.client.post(target, data={'csrf_token': self.csrf(target), 'body': '# My Outpost feedback\n\nA real thought.'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/post/', response.location)
        with self.app.app_context():
            entry = ProjectEntry.query.one()
            post_id = entry.post_id
            self.assertEqual(entry.project_slug, 'outpost')
            self.assertEqual(entry.post.body, '# My Outpost feedback\n\nA real thought.')
        self.assertIn('My Outpost feedback', self.client.get('/').get_data(as_text=True))
        detail = self.client.get('/post/' + str(post_id)).get_data(as_text=True)
        self.assertIn('Outpost · 项目交流', detail)
        self.assertIn('/edit/' + str(post_id), detail)
        self.assertNotIn('My Outpost feedback', self.client.get('/projects/dubforge/talk/').get_data(as_text=True))

    def test_guest_csrf_and_content_limits_still_apply(self):
        target = '/projects/outpost/talk/'
        self.assertEqual(self.client.post(target, data={'body':'unauthenticated'}).status_code, 403)
        self.join()
        self.assertEqual(self.client.post(target, data={'body':'no csrf'}).status_code, 400)
        self.assertEqual(self.client.post(target, data={'csrf_token':self.csrf(target),'body':'x'*8001}).status_code, 413)
        with self.app.app_context():
            self.assertEqual(ProjectEntry.query.count(), 0)

    def test_regular_articles_are_not_reclassified(self):
        with self.app.app_context():
            self.assertEqual(Post.query.count(), 3)
            self.assertEqual(ProjectEntry.query.count(), 0)

    def test_portfolio_build_is_served_without_exposing_storage_or_dotfiles(self):
        portfolio = Path(self.directory.name) / 'gallery'
        (portfolio/'projects/outpost').mkdir(parents=True)
        (portfolio/'index.html').write_text('Gallery home',encoding='utf-8')
        (portfolio/'projects/outpost/index.html').write_text('Outpost page',encoding='utf-8')
        (portfolio/'.secret').write_text('DO NOT SERVE',encoding='utf-8')
        self.app.config['LISS_PORTFOLIO_DIR'] = str(portfolio)
        with self.client.get('/works/') as response:
            self.assertIn(b'Gallery home', response.data)
        with self.client.get('/works/projects/outpost/') as response:
            self.assertIn(b'Outpost page', response.data)
        self.assertEqual(self.client.get('/works/projects/outpost').status_code,308)
        self.assertEqual(self.client.get('/works/.secret').status_code,404)
        self.assertEqual(self.client.get('/works/../public-demo.sqlite').status_code,404)

    def test_mail_cooldown_is_persistent_and_does_not_store_addresses(self):
        with self.app.test_request_context('/', environ_base={'REMOTE_ADDR':'192.0.2.1'}):
            sandbox=self.app.extensions['public_demo']
            with sandbox.exclusive():
                sandbox.limit_mail('Example@invalid.test')
                with self.assertRaisesRegex(Exception, '429'):
                    sandbox.limit_mail('example@invalid.test')
                keys=db.session.execute(text('SELECT bucket FROM public_mail_rate')).scalars().all()
                self.assertEqual(len(keys),2)
                self.assertTrue(all(len(key)==64 and '@' not in key for key in keys))
        second_app=create_app('public-demo')
        with second_app.test_request_context('/', environ_base={'REMOTE_ADDR':'192.0.2.2'}):
            sandbox=second_app.extensions['public_demo']
            with sandbox.exclusive():
                sandbox.prepare()
                with self.assertRaisesRegex(Exception,'429'):
                    sandbox.limit_mail('example@invalid.test')
            db.session.remove()
            db.engine.dispose()


if __name__=='__main__':
    unittest.main()
