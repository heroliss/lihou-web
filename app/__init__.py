from flask import Flask, redirect, request
from flask_bootstrap import Bootstrap
from flask_mail import Mail
from flask_moment import Moment
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_pagedown import PageDown
from config import config

bootstrap = Bootstrap()
mail = Mail()
moment = Moment()
db = SQLAlchemy()
pagedown = PageDown()

login_manager = LoginManager()
login_manager.session_protection = 'strong'
login_manager.login_view = 'auth.login'
login_manager.login_message="请先登录"

def create_app(config_name):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    config[config_name].init_app(app)

    bootstrap.init_app(app)
    mail.init_app(app)
    moment.init_app(app)
    db.init_app(app)
    login_manager.init_app(app)
    pagedown.init_app(app)

    if app.config.get('PUBLIC_DEMO'):
        from .public_demo import install
        install(app)
        from .admin import install as install_admin
        install_admin(app)

    if not app.debug and not app.testing and not app.config['SSL_DISABLE']:
        @app.before_request
        def require_https():
            if not request.is_secure:
                return redirect(request.url.replace('http://', 'https://', 1), code=308)

    from .main import main as main_blueprint
    app.register_blueprint(main_blueprint)

    from .community import community as community_blueprint
    app.register_blueprint(community_blueprint)

    from .auth import auth as auth_blueprint
    app.register_blueprint(auth_blueprint, url_prefix='/auth')

    from .api_1_0 import api as api_1_0_blueprint
    app.register_blueprint(api_1_0_blueprint, url_prefix='/api/v1.0')

    return app
