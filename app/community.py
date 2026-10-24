"""Project discussions and a same-origin, statically built engineering gallery."""
from pathlib import Path
import os

from flask import Blueprint, abort, current_app, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user
from werkzeug.utils import safe_join

from . import db
from .main.forms import PostForm
from .models import Permission, Post

community = Blueprint('community', __name__)

# Stable slugs shared with the engineering gallery; never accept arbitrary links.
PROJECTS = {
    'nomadworkshop': ('NomadWorkshop', '移动生存游戏 · AI 驱动开发'),
    'outpost': ('Outpost', 'Unity 波次防守游戏'),
    'dubforge': ('DubForge', 'AI 视频译制工作台'),
    'ssframework': ('SSFramework', 'Unity 模块化框架'),
    'eazybuildpipeline': ('EazyBuildPipeline', 'Unity 资源与构建工具'),
    'intelligence-index-cost-linear': ('Intelligence × Cost', 'AI 能力与费用可视化'),
    'china-house-price-map': ('全国房价交互地图', '地图与数据探索'),
    'learnopengl': ('LearnOpenGL', '图形渲染实验'),
    'aoe4-autovillager-tool': ('AOE4 Flow Editor', '节点式游戏辅助工具'),
    'ga-sort': ('GA-sort', '遗传算法实验'),
    'floydshortestpath': ('FloydShortestPath', 'Unity 寻路算法'),
    'liss-web': ('Liss Community', '写作与交流社区'),
}


class ProjectEntry(db.Model):
    """Attach a real article to a project without rewriting existing content."""
    __tablename__ = 'project_entries'
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), primary_key=True)
    project_slug = db.Column(db.String(80), nullable=False, index=True)
    post = db.relationship('Post', backref=db.backref('project_entry', uselist=False))


@community.app_context_processor
def project_context():
    from .transactional_mail import email_accounts_enabled
    return {'project_catalog': PROJECTS, 'email_accounts_enabled': email_accounts_enabled}


@community.route('/projects/<slug>/talk/', methods=['GET', 'POST'])
def project_talk(slug):
    if slug not in PROJECTS:
        abort(404)
    form = PostForm()
    if form.validate_on_submit():
        if not current_user.is_authenticated or not current_user.can(Permission.WRITE_ARTICLES):
            abort(403)
        post = Post(body=form.body.data, author=current_user._get_current_object())
        db.session.add(ProjectEntry(project_slug=slug, post=post))
        db.session.commit()
        return redirect(url_for('main.post', id=post.id))
    pagination = Post.query.join(ProjectEntry).filter(ProjectEntry.project_slug == slug).order_by(
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
    root = portfolio_root()
    target = safe_join(str(root), path)
    if target is None or any(part.startswith('.') for part in Path(path).parts):
        abort(404)
    target = Path(target)
    if target.is_dir():
        if path and not request.path.endswith('/'):
            return redirect(request.path + '/', code=308)
        target /= 'index.html'
    if not target.is_file():
        if (root / '404.html').is_file():
            return send_from_directory(root, '404.html'), 404
        abort(404, description='作品页正在准备中，请稍后再来。')
    # send_from_directory rechecks traversal; only a dedicated build directory is served.
    return send_from_directory(root, target.relative_to(root).as_posix(), conditional=True,
                               max_age=31536000 if '_astro' in target.relative_to(root).parts else 3600)
