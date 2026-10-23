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
    if not app.config.get('DEMO_MODE') or app.config.get('PUBLIC_DEMO'):
        raise click.ClickException('Set FLASK_CONFIG=demo; this command never seeds another database.')
    from app.demo_seed import seed_sample_content
    seed_sample_content()
    click.echo('Demo database ready. Sample account: demo@example.com / local-demo-only')
    click.echo('Sample content only; no Heroku or recovered historical data was used.')


@app.cli.command('prepare-public-demo')
def prepare_public_demo():
    """Initialize or check only the isolated public sandbox."""
    sandbox = app.extensions.get('public_demo')
    if sandbox is None:
        raise click.ClickException('Set FLASK_CONFIG=public-demo first.')
    with sandbox.exclusive():
        sandbox.prepare()
    click.echo('Public sandbox ready. Visitors enter through /demo; no shared password.')


@app.cli.command('reset-public-demo')
@click.option('--yes', is_flag=True, help='Confirm disposal of the isolated public demo data.')
def reset_public_demo(yes):
    """Reset only public-demo.sqlite, keeping every other database untouched."""
    sandbox = app.extensions.get('public_demo')
    if sandbox is None or not yes:
        raise click.ClickException('Requires FLASK_CONFIG=public-demo and --yes.')
    with sandbox.exclusive():
        sandbox.reset()
    click.echo('Public sandbox reset. Existing demo sessions have expired.')

if __name__ == '__main__':
    app.cli.main(args=sys.argv[1:], prog_name='python manage.py',
                 obj=ScriptInfo(create_app=lambda: app))
