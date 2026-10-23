"""Copy into the PythonAnywhere WSGI configuration and replace YOUR_USERNAME.

Select Python 3.13 and the matching virtualenv in the Web tab. The private JSON
file is outside the Git checkout; its SECRET_KEY must never enter the repository.
"""
import json
import os
from pathlib import Path
import sys

account = Path('/home/YOUR_USERNAME')
project = account / 'liss-web'
if not project.is_dir() and (account / 'lihou-web').is_dir():
    project = account / 'lihou-web'  # Compatibility with an existing checkout.
private = account / '.config' / 'lihou-web-demo.json'
settings = json.loads(private.read_text(encoding='utf-8'))
os.environ['SECRET_KEY'] = settings['SECRET_KEY']
# Explicit allowlist: never expose or print credentials from this private file.
for name in ('LISS_EMAIL_ACCOUNTS', 'LISS_MAIL_PROVIDER', 'LISS_MAIL_API_KEY',
             'LISS_MAIL_SENDER_EMAIL', 'LISS_MAIL_SENDER_NAME', 'LISS_PUBLIC_BASE_URL',
             'LISS_MAIL_TIMEOUT', 'LISS_PORTFOLIO_DIR'):
    if name in settings:
        os.environ[name] = str(settings[name])
os.environ['FLASK_CONFIG'] = 'public-demo'
os.environ['LISS_DATA_DIR'] = str(account / 'lihou-demo-data')  # Existing persistent storage.
os.environ['LIHOU_DEMO_DIR'] = os.environ['LISS_DATA_DIR']
# Keep the WSGI server's REMOTE_ADDR. Change only after verifying proxy headers.
os.environ['LIHOU_PROXY_HOPS'] = '0'
sys.path.insert(0, str(project))

from manage import app as application  # noqa: E402,F401
