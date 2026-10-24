"""Plain-text list previews; never trust stored HTML as thumbnail attributes."""
from dataclasses import dataclass
from html.parser import HTMLParser
from ipaddress import ip_address
import re
from urllib.parse import urlsplit


def _image_url(value):
    value = (value or '').strip()
    if not value or '\\' in value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if value.startswith('/'):
            return value if not value.startswith('//') and not parsed.netloc else None
        if parsed.scheme.lower() != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return None
        hostname = parsed.hostname.lower().rstrip('.')
        if hostname == 'localhost' or hostname.endswith(('.localhost', '.local', '.internal')):
            return None
        try:
            if not ip_address(hostname).is_global:
                return None
        except ValueError:
            if '.' not in hostname or all(re.fullmatch(r'(?:0[xX][\da-fA-F]+|\d+)', part) for part in hostname.split('.')):
                return None
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None
        return value
    except ValueError:
        return None


class _PreviewParser(HTMLParser):
    blocks = {'p', 'div', 'br', 'li', 'ul', 'ol', 'blockquote', 'h1', 'h2', 'h3',
              'h4', 'h5', 'h6', 'tr', 'table', 'hr'}
    ignored = {'script', 'style', 'pre'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.image = None
        self.skip = 0
        self.code = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.ignored:
            self.skip += 1
        if self.skip:
            return
        if tag == 'code':
            self.code += 1
        if tag in self.blocks:
            self.parts.append('\n')
        if tag == 'img' and not self.code and not self.image:
            self.image = _image_url(dict(attrs).get('src'))

    def handle_endtag(self, tag):
        if tag in self.ignored:
            self.skip = max(0, self.skip - 1)
            self.parts.append('\n')
            return
        if self.skip:
            return
        if tag == 'code':
            self.code = max(0, self.code - 1)
        if tag in self.blocks:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


@dataclass(frozen=True)
class PostPreview:
    title: str
    excerpt: str
    image: str | None


def _shorten(value, limit):
    return value if len(value) <= limit else value[:limit - 1].rstrip() + '…'


def post_preview(post):
    parser = _PreviewParser()
    rendered = getattr(post, 'body_html', None)
    if rendered:
        parser.feed(rendered)
        text = ''.join(parser.parts)
    else:
        text = getattr(post, 'body', '') or ''
    lines = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    title = lines[0] if lines else '一段新的分享'
    excerpt = ' '.join(lines[1:])
    return PostPreview(_shorten(title, 100), _shorten(excerpt, 112), parser.image)
