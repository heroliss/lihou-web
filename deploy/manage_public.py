"""Use the same private key and storage as PythonAnywhere's WSGI configuration."""
import argparse
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--settings', type=Path, default=Path.home() / '.config' / 'lihou-web-demo.json')
parser.add_argument('--data-dir', type=Path, default=Path.home() / 'lihou-demo-data')
options, commands = parser.parse_known_args()
if not commands or commands[0] not in {'prepare-public-demo', 'backup-public-demo', 'restore-public-demo', 'publish-writing-guide', 'grant-admin', 'merge-site-owner'}:
    parser.error('Choose prepare-public-demo, backup-public-demo, restore-public-demo, publish-writing-guide, grant-admin or merge-site-owner.')
settings = json.loads(options.settings.read_text(encoding='utf-8'))
for name in ('LISS_EMAIL_ACCOUNTS', 'LISS_MAIL_PROVIDER', 'LISS_MAIL_API_KEY',
             'LISS_MAIL_SENDER_EMAIL', 'LISS_MAIL_SENDER_NAME', 'LISS_PUBLIC_BASE_URL',
             'LISS_ALIYUN_ACCESS_KEY_ID', 'LISS_ALIYUN_ACCESS_KEY_SECRET',
             'LISS_ALIYUN_REGION', 'LISS_ALIYUN_SENDER_VERIFIED',
             'LISS_MAIL_TIMEOUT', 'LISS_PORTFOLIO_DIR', 'LISS_PORTFOLIO_URL'):
    if name in settings:
        os.environ[name] = str(settings[name])
os.environ.update(FLASK_CONFIG='public-demo', SECRET_KEY=settings['SECRET_KEY'],
                  LISS_DATA_DIR=str(options.data_dir.expanduser().resolve()),
                  LIHOU_DEMO_DIR=str(options.data_dir.expanduser().resolve()))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manage import app  # noqa: E402
from flask.cli import ScriptInfo  # noqa: E402
from app.writing_guide import publish_writing_guide_command  # noqa: E402

app.cli.add_command(publish_writing_guide_command)

app.cli.main(args=commands, prog_name='python deploy/manage_public.py',
             obj=ScriptInfo(create_app=lambda: app))
