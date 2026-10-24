"""DirectMail's documented RPC API, with bounded HTTPS calls and no logging.

Only account messages are supported: one recipient, no attachments or tracking.
The adapter never probes credentials, creates resources, or retries a send.
"""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


ENDPOINTS = {
    'cn-hangzhou': 'https://dm.aliyuncs.com/',
    'ap-southeast-1': 'https://dm.ap-southeast-1.aliyuncs.com/',
    'ap-southeast-2': 'https://dm.ap-southeast-2.aliyuncs.com/',
    'us-east-1': 'https://dm.us-east-1.aliyuncs.com/',
    'eu-central-1': 'https://dm.eu-central-1.aliyuncs.com/',
}
_MAILBOX = re.compile(r'[^\s@,;<>\x00-\x1f\x7f]+@[^\s@,;<>\x00-\x1f\x7f]+\.[^\s@,;<>\x00-\x1f\x7f]+\Z')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


# Do not forward a signed message or its account link to a redirect destination.
_open = build_opener(_NoRedirect()).open


def _mailbox(value):
    return isinstance(value, str) and len(value) <= 254 and bool(_MAILBOX.fullmatch(value))


def configured(config):
    """An explicit operator acknowledgement is required after console verification.

    This switch records setup, not a live claim about the provider's current state.
    A later revoked domain or key still fails closed when the provider rejects it.
    """
    return bool(config.get('LISS_ALIYUN_SENDER_VERIFIED') is True
                and config.get('LISS_ALIYUN_REGION', 'cn-hangzhou') in ENDPOINTS
                and _mailbox(config.get('LISS_MAIL_SENDER_EMAIL'))
                and all(isinstance(config.get(name), str) and config[name].strip()
                        for name in ('LISS_ALIYUN_ACCESS_KEY_ID', 'LISS_ALIYUN_ACCESS_KEY_SECRET')))


def _encode(value):
    return quote(str(value), safe='-_.~', encoding='utf-8')


def sign_parameters(parameters, access_key_secret, method='POST'):
    """HMAC-SHA1 RPC v1 signature; see official DirectMail signature example."""
    canonical = '&'.join(_encode(key) + '=' + _encode(parameters[key])
                         for key in sorted(parameters) if key != 'Signature')
    to_sign = method.upper() + '&%2F&' + _encode(canonical)
    digest = hmac.new((access_key_secret + '&').encode('utf-8'),
                      to_sign.encode('utf-8'), hashlib.sha1).digest()
    return base64.b64encode(digest).decode('ascii')


def send_account_mail(config, message):
    """Return (provider accepted, safe reason); acceptance is not inbox delivery."""
    if not configured(config):
        return False, 'not_configured'
    if (not _mailbox(message.get('to')) or not isinstance(message.get('subject'), str)
            or not 1 <= len(message['subject']) <= 256
            or any(char in message['subject'] for char in '\r\n\x00')
            or not isinstance(message.get('html'), str) or not message['html']
            or not isinstance(message.get('text'), str) or not message['text']):
        return False, 'invalid_message'
    alias = config.get('LISS_MAIL_SENDER_NAME', 'LISS')
    if (not isinstance(alias, str) or not 1 <= len(alias) < 15
            or any(char in alias for char in '\r\n\x00')):
        return False, 'not_configured'
    try:
        timeout = float(config.get('LISS_MAIL_TIMEOUT', 10))
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            return False, 'not_configured'
    except (TypeError, ValueError):
        return False, 'not_configured'
    region = config.get('LISS_ALIYUN_REGION', 'cn-hangzhou')
    parameters = {
        'AccessKeyId': config['LISS_ALIYUN_ACCESS_KEY_ID'],
        'Action': 'SingleSendMail', 'Version': '2015-11-23', 'Format': 'JSON',
        'RegionId': region, 'SignatureMethod': 'HMAC-SHA1', 'SignatureVersion': '1.0',
        'SignatureNonce': str(uuid.uuid4()),
        'Timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'AccountName': config['LISS_MAIL_SENDER_EMAIL'], 'AddressType': '1',
        'ReplyToAddress': 'false', 'FromAlias': alias, 'ToAddress': message['to'],
        'Subject': message['subject'], 'HtmlBody': message['html'], 'TextBody': message['text'],
        'ClickTrace': '0',
    }
    parameters['Signature'] = sign_parameters(parameters, config['LISS_ALIYUN_ACCESS_KEY_SECRET'])
    body = '&'.join(_encode(key) + '=' + _encode(value) for key, value in parameters.items()).encode('ascii')
    # Use the conservative documented RPC size limit, not the newer SDK body limit.
    if len(body) > 80 * 1024:
        return False, 'invalid_message'
    request = Request(ENDPOINTS[region], data=body, method='POST', headers={
        'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json',
        'User-Agent': 'LISS-Account-Mail/1.0',
    })
    try:
        with _open(request, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                return False, 'provider_rejected'
            raw = response.read(65537)
            if len(raw) > 65536:
                return False, 'provider_rejected'
            payload = json.loads(raw)
            if (not isinstance(payload, dict) or 'Code' in payload
                    or not isinstance(payload.get('EnvId'), str) or not payload['EnvId'].strip()
                    or not isinstance(payload.get('RequestId'), str) or not payload['RequestId'].strip()):
                return False, 'provider_rejected'
            return True, ''
    except HTTPError:
        return False, 'provider_rejected'
    except (URLError, TimeoutError, OSError, ValueError, TypeError):
        return False, 'unavailable'
