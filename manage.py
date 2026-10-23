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
    """Prepare LISS storage and compatible account metadata without deleting data."""
    sandbox = app.extensions.get('public_demo')
    if sandbox is None:
        raise click.ClickException('Set FLASK_CONFIG=public-demo first.')
    with sandbox.exclusive():
        sandbox.prepare()
    click.echo('LISS is ready. Saved content is retained; join through /welcome.')


@app.cli.command('backup-public-demo')
@click.option('--output', required=True, type=click.Path(path_type=str))
def backup_public_demo(output):
    """Create a consistent SQLite snapshot; never overwrite an existing file."""
    sandbox = app.extensions.get('public_demo')
    if sandbox is None:
        raise click.ClickException('Requires FLASK_CONFIG=public-demo.')
    try:
        with sandbox.exclusive():
            destination = sandbox.backup(output)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f'Consistent SQLite backup saved: {destination}')


@app.cli.command('restore-public-demo')
@click.option('--input', 'source', required=True, type=click.Path(exists=True, path_type=str))
@click.option('--yes', is_flag=True, help='Explicitly replace current data with this backup.')
def restore_public_demo(source, yes):
    """DANGER: replace current data with a backup; revoke browser logins."""
    sandbox = app.extensions.get('public_demo')
    if sandbox is None or not yes:
        raise click.ClickException('Requires FLASK_CONFIG=public-demo and explicit --yes; this replaces current data.')
    try:
        with sandbox.exclusive():
            safety = sandbox.restore(source)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f'Restored snapshot. Pre-restore safety backup kept: {safety}')
    click.echo('Previous browser logins are invalid; saved accounts and content in the snapshot remain.')

if __name__ == '__main__':
    app.cli.main(args=sys.argv[1:], prog_name='python manage.py',
                 obj=ScriptInfo(create_app=lambda: app))
