from datetime import datetime, timezone
from functools import partial
import hashlib
from werkzeug.security import generate_password_hash, check_password_hash
from .tokens import TokenSerializer as Serializer
from bleach.css_sanitizer import CSSSanitizer
from bleach.linkifier import LinkifyFilter
from markdown import markdown
import bleach
from flask import current_app, request, url_for
from flask_login import UserMixin, AnonymousUserMixin
from itsdangerous import BadData
import hmac
from app.exceptions import ValidationError
from . import db, login_manager


def utcnow():
    """Keep the historical naive-UTC schema without deprecated utcnow."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Permission:
    FOLLOW = 0x01
    COMMENT = 0x02
    WRITE_ARTICLES = 0x04
    MODERATE_COMMENTS = 0x08
    ADMINISTER = 0x80


class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    default = db.Column(db.Boolean, default=False, index=True)
    permissions = db.Column(db.Integer)
    users = db.relationship('User', backref='role', lazy='dynamic')

    @staticmethod
    def insert_roles():
        roles = {
            '普通用户': (Permission.FOLLOW |
                     Permission.COMMENT |
                     Permission.WRITE_ARTICLES, True),
            '论坛版主': (Permission.FOLLOW |
                          Permission.COMMENT |
                          Permission.WRITE_ARTICLES |
                          Permission.MODERATE_COMMENTS, False),
            '管理员': (0xff, False)
        }
        for r in roles:
            role = Role.query.filter_by(name=r).first()
            if role is None:
                role = Role(name=r)
            role.permissions = roles[r][0]
            role.default = roles[r][1]
            db.session.add(role)
        db.session.commit()

    def __repr__(self):
        return '<Role %r>' % self.name


class Follow(db.Model):
    __tablename__ = 'follows'
    follower_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                            primary_key=True)
    followed_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                            primary_key=True)
    timestamp = db.Column(db.DateTime, default=utcnow)


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(64), unique=True, index=True)
    username = db.Column(db.String(64), unique=True, index=True)
    account_kind = db.Column(db.String(16), nullable=False, default='account', server_default='account')
    disabled = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    session_version = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    is_site_owner = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    merged_into_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'))
    password_hash = db.Column(db.String(256))
    confirmed = db.Column(db.Boolean, default=False)
    name = db.Column(db.String(64))
    location = db.Column(db.String(64))
    about_me = db.Column(db.Text())
    member_since = db.Column(db.DateTime(), default=utcnow)
    last_seen = db.Column(db.DateTime(), default=utcnow)
    avatar_hash = db.Column(db.String(32))
    posts = db.relationship('Post', backref='author', lazy='dynamic')
    followed = db.relationship('Follow',
                               foreign_keys=[Follow.follower_id],
                               backref=db.backref('follower', lazy='joined'),
                               lazy='dynamic',
                               cascade='all, delete-orphan')
    followers = db.relationship('Follow',
                                foreign_keys=[Follow.followed_id],
                                backref=db.backref('followed', lazy='joined'),
                                lazy='dynamic',
                                cascade='all, delete-orphan')
    comments = db.relationship('Comment', backref='author', lazy='dynamic')

    @staticmethod
    def generate_fake(count=100):
        from sqlalchemy.exc import IntegrityError
        from random import seed
        from faker import Faker
        fake = Faker()

        seed()
        for i in range(count):
            u = User(email=fake.unique.email(),
                     username=fake.unique.user_name(),
                     password=fake.word(),
                     confirmed=True,
                     name=fake.name(),
                     location=fake.city(),
                     about_me=fake.sentence(),
                     member_since=fake.date_time_between(start_date='-5y'))
            db.session.add(u)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()

    @staticmethod
    def add_self_follows():
        for user in User.query.all():
            if not user.is_following(user):
                user.follow(user)
                db.session.add(user)
                db.session.commit()

    def __init__(self, **kwargs):
        super(User, self).__init__(**kwargs)
        with db.session.no_autoflush:
            if self.role is None:
                if self.email and current_app.config.get('FLASKY_ADMIN') and self.email == current_app.config['FLASKY_ADMIN']:
                    self.role = Role.query.filter_by(permissions=0xff).first()
                if self.role is None:
                    self.role = Role.query.filter_by(default=True).first()
        if self.email is not None and self.avatar_hash is None:
            self.avatar_hash = hashlib.md5(
                self.email.encode('utf-8')).hexdigest()
        self.followed.append(Follow(followed=self))

    @property
    def is_visitor(self):
        return self.account_kind == 'guest'

    is_guest = is_visitor

    @property
    def is_registered(self):
        return self.account_kind == 'account'

    @property
    def is_system(self):
        return self.account_kind == 'system'

    @property
    def is_active(self):
        return not self.disabled

    @property
    def admin_role_key(self):
        # A disabled account keeps its stored role, but can() grants no access.
        permissions = (self.role.permissions or 0) if self.role is not None else 0
        if permissions & Permission.ADMINISTER:
            return 'admin'
        if permissions & Permission.MODERATE_COMMENTS:
            return 'moderator'
        return 'normal'

    @property
    def display_name(self):
        if self.is_system and self.username == 'demo' and self.name in {None, '', 'LIHOU'}:
            return 'Liss'
        return self.name or self.username

    @property
    def password(self):
        raise AttributeError('password is not a readable attribute')

    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def account_credential_fingerprint(self):
        material = self.password_hash or ''
        if self.session_version:
            material += f':version:{self.session_version}'
        return hashlib.sha256(material.encode()).hexdigest()

    def generate_account_token(self, purpose, new_email=None, expiration=None):
        """Public-account actions are scoped and invalidated by credential changes."""
        if purpose not in {'confirm-email', 'reset-password', 'change-email'}:
            raise ValueError('Unsupported account-token purpose.')
        serializer = Serializer(current_app.config['SECRET_KEY'], expiration if expiration is not None
                                else current_app.config.get('LISS_EMAIL_TOKEN_SECONDS', 3600))
        return serializer.dumps({'purpose': purpose, 'uid': self.id,
            'email': self.email, 'new_email': new_email,
            'credential': self.account_credential_fingerprint})

    @staticmethod
    def load_account_token(token, purpose):
        serializer = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = serializer.loads(token)
        except (BadData, ValueError, TypeError):
            return None
        if (not isinstance(data, dict) or data.get('purpose') != purpose
                or type(data.get('uid')) is not int):
            return None
        user = db.session.get(User, data['uid'])
        if not user or user.disabled or not user.is_registered or user.email != data.get('email'):
            return None
        fingerprint = user.account_credential_fingerprint
        if not isinstance(data.get('credential'), str) or not hmac.compare_digest(fingerprint, data['credential']):
            return None
        return user, data

    def generate_confirmation_token(self, expiration=3600):
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'confirm': self.id})

    def confirm(self, token):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except:
            return False
        if data.get('confirm') != self.id:
            return False
        self.confirmed = True
        db.session.add(self)
        return True

    def generate_reset_token(self, expiration=3600):
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'reset': self.id})

    def reset_password(self, token, new_password):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except:
            return False
        if data.get('reset') != self.id:
            return False
        self.password = new_password
        db.session.add(self)
        return True

    def generate_email_change_token(self, new_email, expiration=3600):
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'change_email': self.id, 'new_email': new_email})

    def change_email(self, token):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except:
            return False
        if data.get('change_email') != self.id:
            return False
        new_email = data.get('new_email')
        if new_email is None:
            return False
        if self.query.filter_by(email=new_email).first() is not None:
            return False
        self.email = new_email
        self.avatar_hash = hashlib.md5(
            self.email.encode('utf-8')).hexdigest()
        db.session.add(self)
        return True

    def can(self, permissions):
        if (current_app.config.get('PUBLIC_DEMO') and not self.is_registered
                and permissions & (Permission.ADMINISTER | Permission.MODERATE_COMMENTS)):
            return False
        return not self.disabled and self.role is not None and \
            ((self.role.permissions or 0) & permissions) == permissions

    def is_administrator(self):
        if current_app.config.get('PUBLIC_DEMO') and not self.is_registered:
            return False
        return self.can(Permission.ADMINISTER)

    def ping(self):
        self.last_seen = utcnow()
        db.session.add(self)

    def gravatar(self, size=100, default='identicon', rating='g'):
        if current_app.config.get('DEMO_MODE'):
            return url_for('static', filename='市长.gif')
        if request.is_secure:
            url = 'https://secure.gravatar.com/avatar'
        else:
            url = 'http://www.gravatar.com/avatar'
        hash = self.avatar_hash or hashlib.md5(
            (self.email or self.username or '').encode('utf-8')).hexdigest()
        return '{url}/{hash}?s={size}&d={default}&r={rating}'.format(
            url=url, hash=hash, size=size, default=default, rating=rating)

    def follow(self, user):
        if not self.is_following(user):
            f = Follow(follower=self, followed=user)
            db.session.add(f)

    def unfollow(self, user):
        f = self.followed.filter_by(followed_id=user.id).first()
        if f:
            db.session.delete(f)

    def is_following(self, user):
        return self.followed.filter_by(
            followed_id=user.id).first() is not None

    def is_followed_by(self, user):
        return self.followers.filter_by(
            follower_id=user.id).first() is not None

    @property
    def followed_posts(self):
        return Post.query.join(Follow, Follow.followed_id == Post.author_id)\
            .filter(Follow.follower_id == self.id, Post.hidden.is_(False))

    @property
    def visible_post_count(self):
        return self.posts.filter(Post.hidden.is_(False)).count()

    def to_json(self):
        json_user = {
            'url': url_for('api.get_user', id=self.id, _external=True),
            'username': self.username,
            'member_since': self.member_since,
            'last_seen': self.last_seen,
            'posts': url_for('api.get_user_posts', id=self.id, _external=True),
            'followed_posts': url_for('api.get_user_followed_posts',
                                      id=self.id, _external=True),
            'post_count': self.posts.count()
        }
        return json_user

    def generate_auth_token(self, expiration):
        s = Serializer(current_app.config['SECRET_KEY'],
                       expires_in=expiration)
        return s.dumps({'id': self.id})

    @staticmethod
    def verify_auth_token(token):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except:
            return None
        return db.session.get(User, data['id'])

    def __repr__(self):
        return '<User %r>' % self.username


class AnonymousUser(AnonymousUserMixin):
    is_visitor = is_guest = is_registered = is_system = False
    is_site_owner = False
    display_name = '访客'

    def can(self, permissions):
        return False

    def is_administrator(self):
        return False

login_manager.anonymous_user = AnonymousUser


class UsernameAlias(db.Model):
    """Reserved historical names: canonical profile links and opt-in login aliases."""
    __tablename__ = 'username_aliases'
    username = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    login_allowed = db.Column(db.Boolean, nullable=False, default=False, server_default='0')


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (ValueError, TypeError):
        return None


class Post(db.Model):
    __tablename__ = 'posts'
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text)
    body_html = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, index=True, default=utcnow)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    hidden = db.Column(db.Boolean, nullable=False, default=False, server_default='0', index=True)
    comments = db.relationship('Comment', backref='post', lazy='dynamic')

    @property
    def visible_comment_count(self):
        return self.comments.filter(Comment.disabled.is_not(True)).count()

    @staticmethod
    def generate_fake(count=100):
        from random import seed, randint
        from faker import Faker
        fake = Faker()

        seed()
        user_count = User.query.count()
        for i in range(count):
            u = User.query.offset(randint(0, user_count - 1)).first()
            p = Post(body=' '.join(fake.sentences(randint(1, 5))),
                     timestamp=fake.date_time_between(start_date='-1y'),
                     author=u)
            db.session.add(p)
            db.session.commit()

    @staticmethod
    def on_changed_body(target, value, oldvalue, initiator):
        allowed_tags = ['a', 'abbr', 'acronym', 'b', 'blockquote', 'code',
                        'em', 'i', 'li', 'ol', 'pre', 'strong', 'ul',
                        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'br', 'p','img','button','table','time','font']
        allowed_attributes = ['src','alt','title','style','color','href']
        allowed_styles = ['color','font-weight']
        # Clean and linkify in one parser pass: running linkify again would
        # double-escape entities inside code examples (e.g. &quot;).
        cleaner = bleach.Cleaner(
            tags=allowed_tags, attributes=allowed_attributes,
            css_sanitizer=CSSSanitizer(allowed_css_properties=allowed_styles), strip=True,
            protocols=['http', 'https', 'mailto', 'ed2k', 'thunder'],
            filters=[partial(LinkifyFilter, skip_tags=['pre', 'code'])])
        target.body_html = cleaner.clean(markdown(value, output_format='html', extensions=['fenced_code']))

    def to_json(self):
        json_post = {
            'url': url_for('api.get_post', id=self.id, _external=True),
            'body': self.body,
            'body_html': self.body_html,
            'timestamp': self.timestamp,
            'author': url_for('api.get_user', id=self.author_id,
                              _external=True),
            'comments': url_for('api.get_post_comments', id=self.id,
                                _external=True),
            'comment_count': self.comments.count()
        }
        return json_post

    @staticmethod
    def from_json(json_post):
        body = json_post.get('body')
        if body is None or body == '':
            raise ValidationError('post does not have a body')
        return Post(body=body)


    def to_json(self):
        json_post = {
            'url': url_for('api.get_post', id=self.id, _external=True),
            'body': self.body,
            'body_html': self.body_html,
            'timestamp': self.timestamp,
            'author': url_for('api.get_user', id=self.author_id,
                              _external=True),
            'comments': url_for('api.get_post_comments', id=self.id,
                                _external=True),
            'comment_count': self.comments.count()
        }
        return json_post

    @staticmethod
    def from_json(json_post):
        body = json_post.get('body')
        if body is None or body == '':
            raise ValidationError('post does not have a body')
        return Post(body=body)


    def to_json(self):
        json_post = {
            'url': url_for('api.get_post', id=self.id, _external=True),
            'body': self.body,
            'body_html': self.body_html,
            'timestamp': self.timestamp,
            'author': url_for('api.get_user', id=self.author_id,
                              _external=True),
            'comments': url_for('api.get_post_comments', id=self.id,
                                _external=True),
            'comment_count': self.comments.count()
        }
        return json_post

    @staticmethod
    def from_json(json_post):
        body = json_post.get('body')
        if body is None or body == '':
            raise ValidationError('post does not have a body')
        return Post(body=body)


db.event.listen(Post.body, 'set', Post.on_changed_body)


class Comment(db.Model):
    __tablename__ = 'comments'
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text)
    body_html = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, index=True, default=utcnow)
    disabled = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'))

    @staticmethod
    def generate_fake(count:'生成数量'=100,post_id:'要添加评论的帖子id，该值默认-1表示所有帖子随机添加'=-1) -> '无返回值':
        from random import seed, randint
        from faker import Faker
        fake = Faker()

        seed()
        user_count = User.query.count()
        if post_id == -1:
            post_count = Post.query.count()
        else:
            p = Post.query.filter_by(id=post_id).first()
        for i in range(count):
            u = User.query.offset(randint(0, user_count - 1)).first()
            if post_id == -1:
                p = Post.query.offset(randint(0,post_count -1)).first()
            c = Comment(body=' '.join(fake.sentences(randint(1, 5))),
                     timestamp=fake.date_time_between(start_date='-1y'),
                     author=u,post=p)
            db.session.add(c)
            db.session.commit()

    @staticmethod
    def on_changed_body(target, value, oldvalue, initiator):
        allowed_tags = ['a', 'abbr', 'acronym', 'b', 'code', 'em', 'i',
                        'strong']
        target.body_html = bleach.linkify(bleach.clean(
            markdown(value, output_format='html'),
            tags=allowed_tags, strip=True))

    def to_json(self):
        json_comment = {
            'url': url_for('api.get_comment', id=self.id, _external=True),
            'post': url_for('api.get_post', id=self.post_id, _external=True),
            'body': self.body,
            'body_html': self.body_html,
            'timestamp': self.timestamp,
            'author': url_for('api.get_user', id=self.author_id,
                              _external=True),
        }
        return json_comment

    @staticmethod
    def from_json(json_comment):
        body = json_comment.get('body')
        if body is None or body == '':
            raise ValidationError('comment does not have a body')
        return Comment(body=body)


db.event.listen(Comment.body, 'set', Comment.on_changed_body)
