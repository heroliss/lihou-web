"""List previews must be concise without altering article content or visibility."""
import html
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import create_app, db
from app.models import Post, Role, User
from app.post_preview import post_preview


class PostPreviewUnitTests(unittest.TestCase):
    def preview(self, rendered='', body=''):
        return post_preview(SimpleNamespace(body_html=rendered, body=body))

    def test_heading_and_short_summary_preserve_paragraph_boundaries(self):
        result = self.preview('<h1>一篇<strong>分享</strong></h1><p>第一段。</p><p>' + '有意义的摘要。' * 40 + '</p>')
        self.assertEqual(result.title, '一篇分享')
        self.assertTrue(result.excerpt.startswith('第一段。 有意义'))
        self.assertEqual(len(result.excerpt), 112)
        self.assertTrue(result.excerpt.endswith('…'))

    def test_code_example_is_not_a_cover_or_a_wall_of_excerpt(self):
        result = self.preview('<h1>写作</h1><pre><code><img src="https://example.com/code.png">example</code></pre><p>说明。</p><img src="/static/cover.svg">')
        self.assertEqual(result.image, '/static/cover.svg')
        self.assertEqual(result.excerpt, '说明。')

    def test_unsafe_or_private_urls_are_skipped_for_the_next_valid_image(self):
        for source in ('javascript:alert(1)', 'data:image/png;base64,YQ==', '//evil.test/a.png',
                       'http://example.com/a.png', 'file:///private/a.png',
                       'https://localhost/a.png', 'https://127.0.0.1/a.png',
                       'https://127.1/a.png', 'https://0x7f.0.0.1/a.png',
                       'https://10.0.0.2/a.png', 'https://user:password@example.com/a.png',
                       '/\\evil.test/a.png', 'https://example.com/a\nb.png'):
            with self.subTest(source=source):
                result = self.preview('<h1>Title</h1><img src="' + html.escape(source, quote=True) + '"><img src="/static/cover.svg">')
                self.assertEqual(result.image, '/static/cover.svg')

    def test_first_safe_cover_is_kept_and_attributes_are_not_forwarded(self):
        result = self.preview('<h1>Title</h1><img src="https://example.com/a.png" onerror="run()"><img src="/second.png">')
        self.assertEqual(result.image, 'https://example.com/a.png')
        self.assertFalse(hasattr(result, 'onerror'))

    def test_plain_text_empty_and_inline_code_have_readable_titles(self):
        self.assertEqual(self.preview(body='A title\n\nSome text.').excerpt, 'Some text.')
        self.assertEqual(self.preview().title, '一段新的分享')
        self.assertEqual(self.preview('<h1>使用 <code>KMP</code></h1>').title, '使用 KMP')


class PostPreviewHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict('os.environ', {
            'SECRET_KEY': 'post-preview-tests-only-' * 3,
            'LISS_DATA_DIR': self.directory.name,
            'LIHOU_PROXY_HOPS': '0', 'LISS_EMAIL_ACCOUNTS': '0',
        })
        self.environment.start()
        self.app = create_app('public-demo')
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = self.app.test_client()
        self.assertEqual(self.client.get('/').status_code, 200)
        with self.app.app_context():
            self.writer = User(username='preview_writer', password='preview-test-password',
                               account_kind='account', confirmed=True,
                               role=Role.query.filter_by(default=True).one())
            db.session.add(self.writer)
            db.session.flush()
            visible = Post(body='# 完整内容标题\n\n![cover](/static/community-welcome.svg)\n\n' + '保留正文。' * 100 + '\n\nDETAIL_END_MARKER', author=self.writer)
            hidden = Post(body='# HIDDEN_PREVIEW_NEEDLE\n\n![cover](/static/hidden-preview.svg)', hidden=True, author=self.writer)
            hostile = Post(body='# <em>转义</em> & 标题\n\n可阅读的文字。', author=self.writer)
            db.session.add_all([visible, hidden, hostile])
            db.session.commit()
            self.post_id, self.hidden_id = visible.id, hidden.id
            self.saved_body, self.saved_html = visible.body, visible.body_html

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.environment.stop()
        self.directory.cleanup()

    def test_generic_author_gets_safe_cover_and_bounded_excerpt(self):
        page = self.client.get('/user/preview_writer').get_data(as_text=True)
        self.assertIn('data-post-cover-image', page)
        self.assertIn('src="/static/community-welcome.svg"', page)
        excerpt = re.search(r'<p class="post-excerpt">(保留正文.*?)</p>', page).group(1)
        self.assertLessEqual(len(html.unescape(excerpt)), 112)
        self.assertNotIn('DETAIL_END_MARKER', page)
        self.assertIn('转义 &amp; 标题', page)
        self.assertNotRegex(page, r'class="post-title"[^>]*>.*<em>')

    def test_hidden_content_and_cover_are_absent_from_list_and_detail(self):
        for path in ('/', '/user/preview_writer'):
            with self.subTest(path=path):
                page = self.client.get(path).get_data(as_text=True)
                self.assertNotIn('HIDDEN_PREVIEW_NEEDLE', page)
                self.assertNotIn('/static/hidden-preview.svg', page)
        self.assertEqual(self.client.get(f'/post/{self.hidden_id}').status_code, 404)

    def test_detail_keeps_full_markdown_and_does_not_run_list_preview(self):
        page = self.client.get(f'/post/{self.post_id}').get_data(as_text=True)
        self.assertIn('DETAIL_END_MARKER', page)
        self.assertIn('保留正文。' * 100, page)
        self.assertNotIn('data-post-cover-image', page)
        with self.app.app_context():
            post = db.session.get(Post, self.post_id)
            self.assertEqual((post.body, post.body_html), (self.saved_body, self.saved_html))

    def test_template_escapes_title_and_image_attribute_even_for_legacy_html(self):
        with self.app.app_context():
            post = db.session.get(Post, self.post_id)
            post.body_html = '<h1>&lt;script&gt;alert(1)&lt;/script&gt;</h1><p>Summary.</p><img src="https://example.com/a?x=&quot;onerror=&quot;run()">'
            db.session.commit()
        page = self.client.get('/user/preview_writer').get_data(as_text=True)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertNotIn(' onerror=', page)


if __name__ == '__main__':
    unittest.main()
