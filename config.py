import os
from datetime import timedelta
basedir = os.path.abspath(os.path.dirname(__file__))


def database_url(name, default):
    url = os.environ.get(name) or default
    for prefix in ('postgres://', 'postgresql://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg://' + url[len(prefix):]
    return url


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or '据说这里要写个复杂的字符串？hard to guess string'
    SSL_DISABLE = True
    MAIL_SERVER = 'smtp.126.com'
    MAIL_PORT = 994
    MAIL_USE_SSL = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    FLASKY_MAIL_SUBJECT_PREFIX = '[LIHOU网站]'
    FLASKY_MAIL_SENDER = 'LIHOU网站系统 <lihou_tk@126.com>'
    FLASKY_ADMIN = os.environ.get('FLASKY_ADMIN')
    FLASKY_POSTS_PER_PAGE = 20
    FLASKY_FOLLOWERS_PER_PAGE = 50
    FLASKY_COMMENTS_PER_PAGE = 30
    FLASKY_SLOW_DB_QUERY_TIME = 0.5
    SQLALCHEMY_RECORD_QUERIES = True
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SERVER_NAME = None
    # SQLALCHEMY_COMMIT_ON_TEARDOWN = True  # 该配置在 Flask-SQLAlchemy 2.0后被移除

    @classmethod
    def init_app(cls, app):
        pass


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = database_url('DEV_DATABASE_URL',
        'sqlite:///' + os.path.join(basedir, 'data-dev.sqlite'))


class TestingConfig(Config):
    TESTING = True
    SERVER_NAME = 'localhost'
    MAIL_SUPPRESS_SEND = True
    SQLALCHEMY_DATABASE_URI = database_url('TEST_DATABASE_URL', 'sqlite:///:memory:')
    WTF_CSRF_ENABLED = False


class ProductionConfig(Config):
    SQLALCHEMY_DATABASE_URI = database_url('DATABASE_URL',
        'sqlite:///' + os.path.join(basedir, 'data.sqlite'))

    @classmethod
    def init_app(cls, app):
        Config.init_app(app)

        # email errors to the administrators
        import logging
        from logging.handlers import SMTPHandler
        credentials = None
        secure = None
        if getattr(cls, 'MAIL_USERNAME', None) is not None:
            credentials = (cls.MAIL_USERNAME, cls.MAIL_PASSWORD)
            if getattr(cls, 'MAIL_USE_SSL', None):
                secure = ()
        mail_handler = SMTPHandler(
            mailhost=(cls.MAIL_SERVER, cls.MAIL_PORT),
            fromaddr=cls.FLASKY_MAIL_SENDER,
            toaddrs=[cls.FLASKY_ADMIN],
            subject=cls.FLASKY_MAIL_SUBJECT_PREFIX + ' 服务器错误',
            credentials=credentials,
            secure=secure)
        mail_handler.setLevel(logging.ERROR)
        app.logger.addHandler(mail_handler)


class DemoConfig(DevelopmentConfig):
    DEBUG = False
    DEMO_MODE = True
    MAIL_SUPPRESS_SEND = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(basedir, 'data-demo.sqlite')
    BOOTSTRAP_SERVE_LOCAL = True


class PublicDemoConfig(DemoConfig):
    """Persistent visitor showcase; the historical config name stays compatible."""
    PUBLIC_DEMO = True
    MAX_CONTENT_LENGTH = 16 * 1024
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    SESSION_REFRESH_EACH_REQUEST = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PUBLIC_DEMO_MAX_VISITORS = 2000
    PUBLIC_DEMO_MAX_POSTS = 10
    PUBLIC_DEMO_MAX_COMMENTS = 20
    PUBLIC_DEMO_MAX_FOLLOWS = 10
    PUBLIC_DEMO_MAX_BODY = 4000
    PUBLIC_DEMO_MAX_COMMENT_BODY = 1000
    PUBLIC_DEMO_WRITE_LIMIT = 20
    PUBLIC_DEMO_START_LIMIT = 10
    PUBLIC_DEMO_RATE_WINDOW = 60

    @classmethod
    def init_app(cls, app):
        secret = os.environ.get('SECRET_KEY', '')
        if len(secret) < 32:
            raise RuntimeError('public-demo requires a random SECRET_KEY of at least 32 characters.')
        storage = os.path.abspath(os.environ.get('LIHOU_DEMO_DIR') or
                                  os.path.join(basedir, 'instance', 'public-demo'))
        os.makedirs(storage, exist_ok=True)
        max_visitors = int(os.environ.get('LIHOU_DEMO_MAX_VISITORS', '2000'))
        max_storage_mib = int(os.environ.get('LIHOU_DEMO_MAX_STORAGE_MIB', '128'))
        if max_visitors < 1 or max_storage_mib < 1:
            raise RuntimeError('Visitor and storage limits must be positive.')
        app.config.update(
            SECRET_KEY=secret, FLASKY_ADMIN=None, PUBLIC_DEMO_DIR=storage,
            SQLALCHEMY_DATABASE_URI='sqlite:///' + os.path.join(storage, 'public-demo.sqlite'),
            PUBLIC_DEMO_MAX_VISITORS=max_visitors,
            PUBLIC_DEMO_MAX_STORAGE_BYTES=max_storage_mib * 1024 * 1024,
            SESSION_COOKIE_SECURE=os.environ.get('LIHOU_DEMO_COOKIE_SECURE', '1') != '0',
            SESSION_COOKIE_NAME='lihou_demo',
        )
        proxy_hops = int(os.environ.get('LIHOU_PROXY_HOPS', '0'))
        if not 0 <= proxy_hops <= 2:
            raise RuntimeError('LIHOU_PROXY_HOPS must be 0, 1 or 2.')
        if proxy_hops:
            from werkzeug.middleware.proxy_fix import ProxyFix
            app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_hops, x_proto=proxy_hops,
                                   x_host=0, x_port=0, x_prefix=0)


class HerokuConfig(ProductionConfig):
    SSL_DISABLE = bool(os.environ.get('SSL_DISABLE'))

    @classmethod
    def init_app(cls, app):
        ProductionConfig.init_app(app)

        # 处理代理服务器首部
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app)

        # 输出到 stderr
        import logging
        from logging import StreamHandler
        file_handler = StreamHandler()
        file_handler.setLevel(logging.WARNING)
        app.logger.addHandler(file_handler)


config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'heroku': HerokuConfig,
    'demo': DemoConfig,
    'public-demo': PublicDemoConfig,

    'default': DevelopmentConfig
}
