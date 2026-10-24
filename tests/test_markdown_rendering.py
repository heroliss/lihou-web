import unittest

from app.models import Post


class MarkdownRenderingTest(unittest.TestCase):
    def test_external_image_keeps_description_and_rejects_event_handlers(self):
        post = Post(body='![插图说明](https://example.com/photo.png "图注")\n\n'
                         '<img src="https://example.com/a.png" onerror="alert(1)">')
        self.assertIn('alt="插图说明"', post.body_html)
        self.assertIn('title="图注"', post.body_html)
        self.assertNotIn('onerror', post.body_html)

    def test_linkify_preserves_literal_urls_in_markdown_examples(self):
        post = Post(body='```markdown\n![说明](https://example.com/picture.png)\n```\n\n'
                         '`https://example.com/inline`\n\nhttps://example.com/link')
        self.assertIn('![说明](https://example.com/picture.png)', post.body_html)
        self.assertIn('<code>https://example.com/inline</code>', post.body_html)
        self.assertNotIn('<code><a', post.body_html)
        self.assertIn('<a href="https://example.com/link"', post.body_html)

    def test_inline_image_payload_is_still_rejected(self):
        post = Post(body='<img src="data:image/png;base64,YQ==" onerror="alert(1)">')
        self.assertNotIn('data:', post.body_html)
        self.assertNotIn('onerror', post.body_html)

    def test_code_entities_are_escaped_exactly_once(self):
        from html import unescape
        post = Post(body='```python\nprint("Hello <Liss> & friends")\n```')
        self.assertIn('print("Hello <Liss> & friends")', unescape(post.body_html))
        self.assertNotIn('&amp;quot;', post.body_html)


if __name__ == '__main__':
    unittest.main()
