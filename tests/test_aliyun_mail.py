"""DirectMail contract tests: every transport is isolated; no real mail is sent."""
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs

from flask import Flask

from app import aliyun_mail
from app.transactional_mail import deliver_account_email, email_accounts_enabled


class Reply(BytesIO):
    def __init__(self, payload=None, status=200, raw=None):
        super().__init__(raw if raw is not None else json.dumps(payload).encode('utf-8'))
        self.status = status


class AliyunMailTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            'LISS_EMAIL_ACCOUNTS': True, 'LISS_MAIL_PROVIDER': 'aliyun',
            'LISS_PUBLIC_BASE_URL': 'https://liss.example.com',
            'LISS_ALIYUN_SENDER_VERIFIED': True, 'LISS_ALIYUN_REGION': 'cn-hangzhou',
            'LISS_ALIYUN_ACCESS_KEY_ID': 'example-test-id',
            'LISS_ALIYUN_ACCESS_KEY_SECRET': 'not-a-real-access-key-secret',
            'LISS_MAIL_SENDER_EMAIL': 'accounts@example.com', 'LISS_MAIL_SENDER_NAME': 'LISS',
            'LISS_MAIL_TIMEOUT': 10,
        }
        self.message = {
            'to': 'reader@example.com', 'subject': '[LISS] 邮箱验证 & password + reset',
            'html': '<p>Welcome 中文 + * ~</p><a href="https://liss.example.com/auth/confirm/test-token">确认</a>',
            'text': '请访问 https://liss.example.com/auth/confirm/test-token',
        }
        self.accepted = {'EnvId': 'example-message-id', 'RequestId': 'example-request-id'}

    def test_signature_matches_published_official_post_vector(self):
        # Public test credentials and expected signature, not account secrets:
        # https://www.alibabacloud.com/help/en/direct-mail/signature
        parameters = {
            'AccessKeyId': 'testid', 'AccountName': "<a%b'>", 'Action': 'SingleSendMail',
            'AddressType': '1', 'Format': 'XML', 'HtmlBody': '4', 'RegionId': 'cn-hangzhou',
            'ReplyToAddress': 'true', 'SignatureMethod': 'HMAC-SHA1',
            'SignatureNonce': 'c1b2c332-4cfb-4a0f-b8cc-ebe622aa0a5c',
            'SignatureVersion': '1.0', 'Subject': '3', 'TagName': '2',
            'Timestamp': '2016-10-20T06:27:56Z', 'ToAddress': '1@test.com', 'Version': '2015-11-23',
        }
        self.assertEqual(aliyun_mail.sign_parameters(parameters, 'testsecret'),
                         'llJfXJjBW3OacrVgxxsITgYaYm0=')
        parameters['Signature'] = 'exclude-this-from-canonical-parameters'
        self.assertEqual(aliyun_mail.sign_parameters(dict(reversed(list(parameters.items()))), 'testsecret'),
                         'llJfXJjBW3OacrVgxxsITgYaYm0=')

    def test_request_keeps_credentials_and_tokens_out_of_url_and_round_trips_unicode(self):
        with patch('app.aliyun_mail._open', return_value=Reply(self.accepted)) as transport:
            self.assertEqual(aliyun_mail.send_account_mail(self.config, self.message), (True, ''))
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, 'https://dm.aliyuncs.com/')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(transport.call_args.kwargs['timeout'], 10)
        raw = request.data.decode('ascii')
        self.assertIn('%20', raw)
        self.assertNotIn('+', raw)
        self.assertIn('%2B', raw)
        self.assertIn('%2A', raw)
        self.assertIn('~', raw)
        parameters = {key: values[0] for key, values in parse_qs(raw).items()}
        for field, original in [('Subject', 'subject'), ('HtmlBody', 'html'), ('TextBody', 'text'), ('ToAddress', 'to')]:
            self.assertEqual(parameters[field], self.message[original])
        self.assertNotIn(self.config['LISS_ALIYUN_ACCESS_KEY_SECRET'], raw)
        self.assertEqual(parameters['Action'], 'SingleSendMail')
        self.assertEqual(parameters['Version'], '2015-11-23')
        self.assertEqual(parameters['AddressType'], '1')
        self.assertEqual(parameters['ReplyToAddress'], 'false')
        self.assertEqual(parameters['ClickTrace'], '0')
        self.assertRegex(parameters['Timestamp'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        self.assertEqual(parameters['Signature'], aliyun_mail.sign_parameters(
            parameters, self.config['LISS_ALIYUN_ACCESS_KEY_SECRET']))

    def test_nonce_changes_on_each_attempt_and_no_automatic_retry_occurs(self):
        with patch('app.aliyun_mail._open', side_effect=[Reply(self.accepted), Reply(self.accepted)]) as transport:
            for _ in range(2):
                self.assertEqual(aliyun_mail.send_account_mail(self.config, self.message), (True, ''))
        nonces = [parse_qs(call.args[0].data.decode())['SignatureNonce'][0] for call in transport.call_args_list]
        self.assertNotEqual(*nonces)
        self.assertEqual(transport.call_count, 2)

    def test_each_official_region_uses_matching_fixed_https_endpoint(self):
        for region, endpoint in aliyun_mail.ENDPOINTS.items():
            with self.subTest(region=region):
                self.config['LISS_ALIYUN_REGION'] = region
                with patch('app.aliyun_mail._open', return_value=Reply(self.accepted)) as transport:
                    self.assertTrue(aliyun_mail.send_account_mail(self.config, self.message)[0])
                self.assertEqual(transport.call_args.args[0].full_url, endpoint)
                parameters = parse_qs(transport.call_args.args[0].data.decode())
                self.assertEqual(parameters['RegionId'], [region])

    def test_missing_keys_unverified_sender_and_unknown_region_never_contact_provider(self):
        changes = [
            {'LISS_ALIYUN_ACCESS_KEY_ID': ''}, {'LISS_ALIYUN_ACCESS_KEY_SECRET': '  '},
            {'LISS_ALIYUN_SENDER_VERIFIED': False}, {'LISS_ALIYUN_SENDER_VERIFIED': '1'},
            {'LISS_MAIL_SENDER_EMAIL': 'not-an-email'}, {'LISS_MAIL_SENDER_EMAIL': 'a@example.com\r\nBcc:b@example.com'},
            {'LISS_ALIYUN_REGION': 'https://untrusted.example.com'},
        ]
        with patch('app.aliyun_mail._open') as transport:
            for change in changes:
                with self.subTest(change=list(change)):
                    config = {**self.config, **change}
                    self.assertFalse(aliyun_mail.configured(config))
                    self.assertEqual(aliyun_mail.send_account_mail(config, self.message), (False, 'not_configured'))
        transport.assert_not_called()

    def test_invalid_message_or_timeout_configuration_never_sends(self):
        for change in [{'to': 'one@example.com,two@example.com'}, {'to': 'Reader <one@example.com>'},
                       {'subject': 'unsafe\r\nheader'}, {'subject': 'a' * 257}, {'html': ''},
                       {'text': None}, {'html': 'x' * 90000}]:
            with self.subTest(change=list(change)), patch('app.aliyun_mail._open') as transport:
                self.assertEqual(aliyun_mail.send_account_mail(self.config, {**self.message, **change}),
                                 (False, 'invalid_message'))
                transport.assert_not_called()
        for change in [{'LISS_MAIL_TIMEOUT': float('nan')}, {'LISS_MAIL_TIMEOUT': 0},
                       {'LISS_MAIL_TIMEOUT': 'bad'}, {'LISS_MAIL_TIMEOUT': 31},
                       {'LISS_MAIL_SENDER_NAME': 'a' * 15}]:
            with self.subTest(change=list(change)), patch('app.aliyun_mail._open') as transport:
                self.assertEqual(aliyun_mail.send_account_mail({**self.config, **change}, self.message),
                                 (False, 'not_configured'))
                transport.assert_not_called()

    def test_2xx_business_error_or_missing_acceptance_fields_never_reports_success(self):
        for payload in [{'Code': 'InvalidMailAddress.NotFound', 'Message': 'private error', 'RequestId': 'r'},
                        {'Code': 'Success', **self.accepted}, {'RequestId': 'r'}, {'EnvId': 'e'},
                        {'EnvId': '', 'RequestId': 'r'}, {'EnvId': 1, 'RequestId': 'r'}, [], None]:
            with self.subTest(payload=payload), patch('app.aliyun_mail._open', return_value=Reply(payload)):
                self.assertEqual(aliyun_mail.send_account_mail(self.config, self.message),
                                 (False, 'provider_rejected'))

    def test_malformed_or_oversized_provider_response_is_not_success(self):
        for raw, expected in [(b'not-json', 'unavailable'), (b'x' * 65537, 'provider_rejected')]:
            with self.subTest(expected=expected), patch('app.aliyun_mail._open', return_value=Reply(raw=raw)):
                self.assertEqual(aliyun_mail.send_account_mail(self.config, self.message), (False, expected))

    def test_http_refusal_redirect_and_network_timeout_are_safe_failures(self):
        errors = [
            (HTTPError('https://dm.aliyuncs.com/', 403, 'private body', {}, None), 'provider_rejected'),
            (HTTPError('https://dm.aliyuncs.com/', 302, 'redirect', {}, None), 'provider_rejected'),
            (URLError('private key / token in provider error'), 'unavailable'),
            (TimeoutError('simulated timeout'), 'unavailable'),
        ]
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            for error, expected in errors:
                with self.subTest(error=type(error).__name__), patch('app.aliyun_mail._open', side_effect=error) as transport:
                    self.assertEqual(aliyun_mail.send_account_mail(self.config, self.message), (False, expected))
                    self.assertEqual(transport.call_count, 1)
        self.assertEqual(output.getvalue(), '')
        self.assertIsNone(aliyun_mail._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example/'))

    def test_non_2xx_response_is_rejected_even_if_body_looks_successful(self):
        with patch('app.aliyun_mail._open', return_value=Reply(self.accepted, status=500)):
            self.assertEqual(aliyun_mail.send_account_mail(self.config, self.message), (False, 'provider_rejected'))

    def test_flask_adapter_gates_feature_and_preserves_structured_result(self):
        app = Flask(__name__, template_folder='../app/templates')
        app.config.update(self.config)
        with app.test_request_context('/'):
            self.assertTrue(email_accounts_enabled())
            with patch('app.transactional_mail.render_template', return_value='Rendered content'), \
                    patch('app.aliyun_mail._open', return_value=Reply(self.accepted)):
                result = deliver_account_email('reader@example.com', '邮箱验证', 'ignored')
                self.assertTrue(result.accepted)
            app.config['LISS_ALIYUN_SENDER_VERIFIED'] = False
            self.assertFalse(email_accounts_enabled())
            with patch('app.aliyun_mail._open') as transport:
                result = deliver_account_email('reader@example.com', '邮箱验证', 'ignored')
                self.assertEqual((result.accepted, result.reason), (False, 'not_configured'))
                transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
