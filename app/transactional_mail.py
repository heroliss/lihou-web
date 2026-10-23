"""Small HTTPS transactional-mail adapter; secrets and tokens never enter logs."""
from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from flask import current_app, render_template, url_for


@dataclass(frozen=True)
class DeliveryResult:
    accepted: bool
    reason: str = ''


def email_accounts_enabled():
    config = current_app.config
    if not config.get('LISS_EMAIL_ACCOUNTS'):
        return False
    provider = config.get('LISS_MAIL_PROVIDER', '').lower()
    base = urlsplit(config.get('LISS_PUBLIC_BASE_URL', ''))
    valid_base = (base.scheme == 'https' and bool(base.hostname)
                  and not base.username and not base.password and not base.query
                  and not base.fragment and base.path in {'', '/'})
    if provider == 'outbox':
        return bool(current_app.testing and valid_base)
    sender = config.get('LISS_MAIL_SENDER_EMAIL', '')
    return bool(valid_base and provider in {'brevo', 'resend'}
                and config.get('LISS_MAIL_API_KEY') and '@' in sender
                and '\r' not in sender and '\n' not in sender)


def account_link(endpoint, **values):
    """Use the configured public origin, never a request-controlled Host header."""
    return current_app.config['LISS_PUBLIC_BASE_URL'].rstrip('/') + url_for(endpoint, **values)


def deliver_account_email(recipient, subject, template, **context):
    if not email_accounts_enabled():
        return DeliveryResult(False, 'not_configured')
    config = current_app.config
    message = {
        'to': recipient,
        'subject': '[LISS] ' + subject,
        'text': render_template(template + '.txt', **context),
        'html': render_template(template + '.html', **context),
    }
    provider = config['LISS_MAIL_PROVIDER'].lower()
    if provider == 'outbox':
        # This transport cannot be enabled outside Flask's explicit testing mode.
        current_app.extensions.setdefault('liss_mail_outbox', []).append(message)
        return DeliveryResult(True)
    sender_email = config['LISS_MAIL_SENDER_EMAIL']
    sender_name = config.get('LISS_MAIL_SENDER_NAME', 'LISS')
    if provider == 'brevo':
        endpoint = 'https://api.brevo.com/v3/smtp/email'
        payload = {'sender': {'name': sender_name, 'email': sender_email},
                   'to': [{'email': recipient}], 'subject': message['subject'],
                   'htmlContent': message['html'], 'textContent': message['text']}
        headers = {'api-key': config['LISS_MAIL_API_KEY']}
    else:
        endpoint = 'https://api.resend.com/emails'
        payload = {'from': f'{sender_name} <{sender_email}>', 'to': [recipient],
                   'subject': message['subject'], 'html': message['html'],
                   'text': message['text']}
        headers = {'Authorization': 'Bearer ' + config['LISS_MAIL_API_KEY']}
    headers.update({'Content-Type': 'application/json', 'Accept': 'application/json',
                    'User-Agent': 'LISS-Account-Mail/1.0'})
    request = Request(endpoint, data=json.dumps(payload).encode('utf-8'),
                      headers=headers, method='POST')
    try:
        with urlopen(request, timeout=float(config.get('LISS_MAIL_TIMEOUT', 10))) as response:
            # Provider acceptance is not proof that a recipient inbox received it.
            if 200 <= response.status < 300:
                return DeliveryResult(True)
        return DeliveryResult(False, 'provider_rejected')
    except HTTPError:
        return DeliveryResult(False, 'provider_rejected')
    except (URLError, TimeoutError, OSError, ValueError):
        return DeliveryResult(False, 'unavailable')
