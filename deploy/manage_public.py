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
if not commands or commands[0] not in {'prepare-public-demo', 'backup-public-demo', 'restore-public-demo'}:
    parser.error('Choose prepare-public-demo, backup-public-demo or restore-public-demo.')
settings = json.loads(options.settings.read_text(encoding='utf-8'))
os.environ.update(FLASK_CONFIG='public-demo', SECRET_KEY=settings['SECRET_KEY'],
                  LISS_DATA_DIR=str(options.data_dir.expanduser().resolve()),
                  LIHOU_DEMO_DIR=str(options.data_dir.expanduser().resolve()))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manage import app  # noqa: E402
from flask.cli import ScriptInfo  # noqa: E402

app.cli.main(args=commands, prog_name='python deploy/manage_public.py',
             obj=ScriptInfo(create_app=lambda: app))
