"""Add initial LISS writing guides without replacing any reader's content."""
from pathlib import Path
import secrets
import time
from flask import current_app
from . import db
from .models import Comment, Post, Role, User


# Exact historical bodies are retained solely to identify untouched seed rows.
LEGACY_POSTS = [
    '# LIHOU 个人网站\n\n这是独立开发的 Flask 社交博客项目。\n\n支持 **Markdown 文章、评论、关注时间线和 REST API**。\n\n> 这些内容是新建演示数据，不是找回的旧 Heroku 网站数据。',
    '## Markdown 编辑与阅读\n\n进入演示后可以发布内容，编辑器同时提供预览。\n\n- 标题与列表\n- 行内代码与代码段\n- 经过清理的富文本\n\n```python\nfrom app import create_app\napp = create_app("demo")\n```',
    '## 账户与社交功能\n\n用户资料、关注与被关注列表、评论管理和权限控制共同组成了这个早期 Web 工程。\n\n源码中同时保留了 REST API 和回归测试。',
]
WELCOME_POSTS = [
    '# 欢迎来到 LISS\n\n一个可以随手写下想法、交流技术与分享日常的小站。\n\n**不用注册也可以加入。** 以当前浏览器的访客身份发布文章、回复评论、关注感兴趣的作者；也可以创建账号，在不同浏览器中继续使用同一身份。\n\n文章、评论与关注关系会保存在站点中，不会因为退出浏览器而自动清空。欢迎留下你的第一篇文字。',
    '## 从一段文字开始\n\n编辑器支持 **Markdown**，可以边写边看预览。\n\n- 用标题组织内容\n- 用列表整理想法\n- 用代码段分享实践\n\n```python\nprint("Hello, LISS")\n```\n\n发布后可以继续编辑自己的文章，也可以在文章下方参与讨论。',
    '## 关于身份与内容\n\n访客身份保存在当前浏览器的 Cookie 中。清除 Cookie、无痕浏览或换一台设备后，可能无法重新认回该身份，但已发布的内容仍会保留。\n\n**创建账号时，当前访客的文章、评论与关注关系会一起保留。** 已有账号可以直接用用户名和密码登录；登录其他账号不会合并两个身份的内容。\n\n本站不要求邮箱，因此暂不提供邮件找回密码。请保存好自己的账号信息，也请勿发布敏感资料。',
]
LEGACY_COMMENT = '示例评论：文章、Markdown 与回复功能展示。'
EARLIER_ACCOUNT_GUIDE = WELCOME_POSTS[2]
WELCOME_POSTS[2] = EARLIER_ACCOUNT_GUIDE.replace(
    '本站不要求邮箱，因此暂不提供邮件找回密码。请保存好自己的账号信息，也请勿发布敏感资料。',
    '注册方式以注册页当前开放的功能为准。邮箱注册开放后，确认邮件中的链接即可验证邮箱；已绑定并验证的邮箱可用于找回密码。旧用户名账号仍可登录，之后再绑定邮箱。\n\n访客身份和未绑定邮箱的账号无法通过邮件找回。请保存好登录信息，也请勿发布敏感资料。')
WELCOME_COMMENT = '欢迎在这里留言，分享你的想法。'
LEGACY_BIO = '独立开发的 Flask 社交博客；这里的用户、文章和评论是新建示例。'


def _upgrade_untouched_welcome(author):
    """Back up, then update exact known seed text, preserving IDs and replies."""
    changes = [(post, WELCOME_POSTS[LEGACY_POSTS.index(post.body)])
               for post in author.posts.all() if post.body in LEGACY_POSTS]
    changes += [(post, WELCOME_POSTS[2]) for post in author.posts.all()
                if post.body == EARLIER_ACCOUNT_GUIDE]
    comments = [comment for comment in author.comments.all()
                if comment.body == LEGACY_COMMENT and comment.post.author_id == author.id]
    metadata = {}
    if author.name == 'LIHOU':
        metadata['name'] = 'Liss'
    if author.location == '在线示例':
        metadata['location'] = ''
    if author.about_me == LEGACY_BIO:
        metadata['about_me'] = 'LISS 的站点介绍与写作指南。'
    if not changes and not comments and not metadata:
        return
    from .public_demo_storage import backup_database
    database = Path(current_app.config['PUBLIC_DEMO_DIR']) / 'public-demo.sqlite'
    backup_database(database, database.parent / ('before-welcome-' + time.strftime('%Y%m%d-%H%M%S')
                                                 + '-' + secrets.token_hex(3) + '.sqlite'))
    for post, body in changes:
        post.body = body
    for comment in comments:
        comment.body = WELCOME_COMMENT
    for field, value in metadata.items():
        setattr(author, field, value)
    db.session.commit()


def seed_sample_content(*, password='local-demo-only', public=False):
    db.create_all()
    Role.insert_roles()
    author = User.query.filter_by(username='demo').first()
    if author is None:
        author = User(email='demo@example.com', username='demo', password=password,
                      confirmed=True, name='Liss', account_kind='system' if public else 'account',
                      location='', about_me='LISS 的站点介绍与写作指南。' if public else '本地开发用初始内容。',
                      role=Role.query.filter_by(default=True).first())
        db.session.add(author)
        db.session.commit()
    elif author.email == 'demo@example.invalid':
        author.email = 'demo@example.com'
        db.session.commit()
    if public and author.is_system:
        _upgrade_untouched_welcome(author)
    if Post.query.filter_by(author_id=author.id).count() == 0:
        for body in WELCOME_POSTS:
            post = Post(body=body, author=author)
            db.session.add(post)
            db.session.flush()
            db.session.add(Comment(body=WELCOME_COMMENT,
                                   author=author, post=post, disabled=False))
        db.session.commit()
    return author
