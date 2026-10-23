"""Add missing example content without deleting stored visitor content."""
from . import db
from .models import Comment, Post, Role, User


def seed_sample_content(*, password='local-demo-only', public=False):
    db.create_all()
    Role.insert_roles()
    author = User.query.filter_by(username='demo').first()
    if author is None:
        author = User(email='demo@example.com', username='demo', password=password,
                      confirmed=True, name='LIHOU',
                      location='在线示例' if public else '本地示例',
                      about_me='独立开发的 Flask 社交博客；这里的用户、文章和评论是新建示例。')
        db.session.add(author)
        db.session.commit()
    elif author.email == 'demo@example.invalid':
        author.email = 'demo@example.com'
        db.session.commit()
    if Post.query.filter_by(author_id=author.id).count() == 0:
        posts = [
            '# LIHOU 个人网站\n\n这是独立开发的 Flask 社交博客项目。\n\n支持 **Markdown 文章、评论、关注时间线和 REST API**。\n\n> 这些内容是新建演示数据，不是找回的旧 Heroku 网站数据。',
            '## Markdown 编辑与阅读\n\n进入演示后可以发布内容，编辑器同时提供预览。\n\n- 标题与列表\n- 行内代码与代码段\n- 经过清理的富文本\n\n```python\nfrom app import create_app\napp = create_app("demo")\n```',
            '## 账户与社交功能\n\n用户资料、关注与被关注列表、评论管理和权限控制共同组成了这个早期 Web 工程。\n\n源码中同时保留了 REST API 和回归测试。'
        ]
        for body in posts:
            post = Post(body=body, author=author)
            db.session.add(post)
            db.session.flush()
            db.session.add(Comment(body='示例评论：文章、Markdown 与回复功能展示。',
                                   author=author, post=post, disabled=False))
        db.session.commit()
    return author
