#!/usr/bin/env python
"""Flask 3 CLI entrypoint. Local demonstration uses FLASK_CONFIG=demo."""
import os
import sys
import unittest
import click
from flask.cli import ScriptInfo
from flask_migrate import Migrate
from app import create_app, db
from app.models import User, Follow, Role, Permission, Post, Comment

app = create_app(os.getenv('FLASK_CONFIG') or 'default')
migrate = Migrate(app, db)

@app.shell_context_processor
def make_shell_context():
    return dict(db=db, User=User, Follow=Follow, Role=Role,
                Permission=Permission, Post=Post, Comment=Comment)

@app.cli.command('test')
def test():
    """Run model, HTTP client and API regression tests."""
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestLoader().discover('tests'))
    if not result.wasSuccessful():
        raise click.ClickException('Test suite failed')

@app.cli.command('runserver')
@click.option('--host', default='127.0.0.1')
@click.option('--port', default=5000, type=int)
def runserver(host, port):
    """Compatibility alias for a local Flask development server."""
    app.run(host=host, port=port, use_reloader=False)

@app.cli.command('deploy')
def deploy():
    """Apply existing migrations and initialize roles."""
    from flask_migrate import upgrade
    upgrade()
    Role.insert_roles()
    User.add_self_follows()

@app.cli.command('seed-demo')
def seed_demo():
    """Create repeatable sample content in the separate demo database."""
    if not app.config.get('DEMO_MODE'):
        raise click.ClickException('Set FLASK_CONFIG=demo; this command never seeds another database.')
    db.create_all()
    Role.insert_roles()
    author = User.query.filter_by(username='demo').first()
    if author is None:
        author = User(email='demo@example.com', username='demo',
                      password='local-demo-only', confirmed=True,
                      name='LIHOU', location='本地示例',
                      about_me='独立开发的 Flask 社交博客。这里的用户、文章、评论是新建演示数据。')
        db.session.add(author)
        db.session.commit()
    elif author.email == 'demo@example.invalid':
        author.email = 'demo@example.com'
        db.session.commit()
    if Post.query.filter_by(author_id=author.id).count() == 0:
        posts = [
            '# LIHOU 个人网站\n\n这是独立开发的 Flask 社交博客项目。\n\n支持 **Markdown 文章、评论、关注时间线和 REST API**。\n\n> 这段内容是 2026 年本地运行时创建的示例，不是旧 Heroku 数据。',
            '## Markdown 编辑与阅读\n\n可在登录后发布内容，编辑器同时提供预览。\n\n- 标题与列表\n- 行内代码与代码段\n- 经过清理的富文本\n\n```python\nfrom app import create_app\napp = create_app("demo")\n```',
            '## 账户与社交功能\n\n用户资料、关注与被关注列表、评论管理和权限控制共同组成了这个早期 Web 工程。\n\n源码中同时保留了 REST API 和回归测试。'
        ]
        for body in posts:
            post = Post(body=body, author=author)
            db.session.add(post)
            db.session.flush()
            db.session.add(Comment(body='本地示例评论：文章、Markdown 与回复功能已运行。', author=author, post=post, disabled=False))
        db.session.commit()
    click.echo('Demo database ready. Sample account: demo@example.com / local-demo-only')
    click.echo('Sample content only; no Heroku or recovered historical data was used.')

if __name__ == '__main__':
    app.cli.main(args=sys.argv[1:], prog_name='python manage.py',
                 obj=ScriptInfo(create_app=lambda: app))
