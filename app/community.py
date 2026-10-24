"""Project discussions and a stable entry point to the engineering gallery."""
from pathlib import Path
import os
import re
from urllib.parse import quote, quote_from_bytes, urlsplit, urlunsplit

from flask import Blueprint, abort, current_app, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user
from werkzeug.utils import safe_join

from . import db
from .main.forms import PostForm
from .models import Permission, Post

community = Blueprint('community', __name__)


def _clean_portfolio_path(path):
    """Do not let a request path change the configured destination or its base."""
    if (path.startswith('/') or '\\' in path or '%' in path or '//' in path or
            any(ord(character) < 32 or ord(character) == 127 for character in path)):
        return False
    return all(not part.startswith('.') for part in path.rstrip('/').split('/')) if path else True


def portfolio_base_url(value):
    """Validate a fixed public HTTPS destination; empty keeps local serving enabled."""
    if value is None or value == '':
        return ''
    error = ('LISS_PORTFOLIO_URL must be a fixed HTTPS URL without credentials, '
             'port, query or fragment, and with a clean path.')
    if (not isinstance(value, str) or value != value.strip() or '\\' in value or
            '?' in value or '#' in value or
            any(ord(character) <= 32 or ord(character) == 127 for character in value)):
        raise ValueError(error)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or
                parsed.password is not None or parsed.port is not None or
                parsed.netloc.lower() != parsed.hostname or
                len(parsed.hostname) > 253 or any(not re.fullmatch(
                    r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                    for label in parsed.hostname.split('.')) or
                not _clean_portfolio_path(parsed.path.lstrip('/')) or parsed.path.startswith('//')):
            raise ValueError(error)
    except ValueError as exception:
        raise ValueError(error) from exception
    return urlunsplit(('https', parsed.hostname, parsed.path.rstrip('/') + '/', '', ''))


@community.record_once
def validate_portfolio_config(state):
    # Invalid production configuration must fail clearly, rather than serve an old build.
    state.app.config['LISS_PORTFOLIO_URL'] = portfolio_base_url(
        state.app.config.get('LISS_PORTFOLIO_URL'))


def _with_request_query(location):
    if request.query_string:
        location += '?' + quote_from_bytes(request.query_string, safe="!$&'()*+,-./:;=?@_%~")
    return location

# Stable slugs shared with the engineering gallery; never accept arbitrary links.
PROJECTS = {
    'nomadworkshop': ('NomadWorkshop', '移动生存游戏 · AI 驱动开发'),
    'outpost': ('Outpost', 'Unity 波次防守游戏'),
    'gomoku': ('Gomoku v3', '五子棋与搜索引擎实验'),
    'dubforge': ('DubForge', 'AI 视频译制工作台'),
    'ssframework': ('SSFramework', 'Unity 模块化框架'),
    'eazybuildpipeline': ('EazyBuildPipeline', 'Unity 资源与构建工具'),
    'intelligence-index-cost-linear': ('Intelligence × Cost', 'AI 能力与费用可视化'),
    'china-house-price-map': ('全国房价交互地图', '地图与数据探索'),
    'learnopengl': ('LearnOpenGL', '图形渲染实验'),
    'aoe4-flow': ('AoE4 Flow · 帝国时代 4 自动化助手', '帝国时代 4 · 图像识别与可视流程自动化'),
    'ga-sort': ('GA-sort', '遗传算法实验'),
    'route-lab': ('Route Lab', '旅行商算法实验室'),
    'sort-lab': ('Sort Lab', '排序算法实验台'),
    'qt-labs': ('Liss Qt Labs', '进程调度、压缩与计算器'),
    'text-lab': ('Liss Text Lab', '分词、校对与文件检索'),
    'systems-lab': ('Liss Systems Lab', '分页、设备、资源与质数实验'),
    'nex': ('NEX', '本地事项与计划管理'),
    'liss-blocks': ('Liss Blocks', '方块消行与桌面游戏'),
    'floydshortestpath': ('FloydShortestPath', 'Unity 寻路算法'),
    'liss-web': ('Liss Community', '写作与交流社区'),
}
PROJECT_SLUG_ALIASES = {'aoe4-autovillager-tool': 'aoe4-flow'}


def canonical_project_slug(slug):
    """Keep historical discussion entries readable without rewriting their data."""
    return PROJECT_SLUG_ALIASES.get(slug, slug)


class ProjectEntry(db.Model):
    """Attach a real article to a project without rewriting existing content."""
    __tablename__ = 'project_entries'
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), primary_key=True)
    project_slug = db.Column(db.String(80), nullable=False, index=True)
    post = db.relationship('Post', backref=db.backref('project_entry', uselist=False))


@community.app_context_processor
def project_context():
    from .transactional_mail import email_accounts_enabled
    return {'project_catalog': PROJECTS, 'canonical_project_slug': canonical_project_slug,
            'email_accounts_enabled': email_accounts_enabled}


@community.get('/writing-guide/')
def writing_guide():
    from .writing_guide import get_published_writing_guide
    guide = get_published_writing_guide()
    if guide is None:
        abort(404, description='写作指南尚未发布。')
    return redirect(url_for('main.post', id=guide.id))


@community.route('/projects/<slug>/talk/', methods=['GET', 'POST'])
def project_talk(slug):
    requested_slug = slug
    slug = canonical_project_slug(slug)
    if slug not in PROJECTS:
        abort(404)
    if requested_slug != slug and request.method in {'GET', 'HEAD'}:
        return redirect(_with_request_query(url_for('community.project_talk', slug=slug)), code=308)
    form = PostForm()
    if form.validate_on_submit():
        if not current_user.is_authenticated or not current_user.can(Permission.WRITE_ARTICLES):
            abort(403)
        post = Post(body=form.body.data, author=current_user._get_current_object())
        db.session.add(ProjectEntry(project_slug=slug, post=post))
        db.session.commit()
        return redirect(url_for('main.post', id=post.id))
    related_slugs = [slug] + [old for old, current in PROJECT_SLUG_ALIASES.items() if current == slug]
    pagination = Post.query.join(ProjectEntry).filter(ProjectEntry.project_slug.in_(related_slugs), Post.hidden.is_(False)).order_by(
        Post.timestamp.desc(), Post.id.desc()).paginate(
            page=request.args.get('page', 1, type=int),
            per_page=current_app.config['FLASKY_POSTS_PER_PAGE'], error_out=False)
    return render_template('project_talk.html', slug=slug, project=PROJECTS[slug],
                           posts=pagination.items, pagination=pagination, form=form)


def portfolio_root():
    return Path(current_app.config.get('LISS_PORTFOLIO_DIR') or os.environ.get('LISS_PORTFOLIO_DIR') or
                Path(current_app.root_path).parent / 'portfolio').resolve()


@community.get('/works/')
@community.get('/works/<path:path>')
def portfolio(path=''):
    if not _clean_portfolio_path(path):
        abort(404)
    external = portfolio_base_url(current_app.config.get('LISS_PORTFOLIO_URL'))
    if external:
        return redirect(_with_request_query(external + quote(path, safe='/')), code=308)
    root = portfolio_root()
    target = safe_join(str(root), path)
    if target is None:
        abort(404)
    target = Path(target)
    if target.is_dir():
        if path and not request.path.endswith('/'):
            return redirect(_with_request_query(request.path + '/'), code=308)
        target /= 'index.html'
    if not target.is_file():
        if (root / '404.html').is_file():
            return send_from_directory(root, '404.html'), 404
        abort(404, description='作品页正在准备中，请稍后再来。')
    # send_from_directory rechecks traversal; only a dedicated build directory is served.
    return send_from_directory(root, target.relative_to(root).as_posix(), conditional=True,
                               max_age=31536000 if '_astro' in target.relative_to(root).parts else 3600)
